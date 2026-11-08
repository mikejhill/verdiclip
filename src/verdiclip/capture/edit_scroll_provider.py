"""Incremental fixed owned-EDIT sequence; no arbitrary seek or app dispatch."""

from __future__ import annotations

import json
import math
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any, Protocol, cast, override

import mss

from verdiclip.capture.edit_native import ProbeBudget
from verdiclip.capture.edit_samples import crop, stable_samples
from verdiclip.capture.edit_scroll_contract import assemble, validate_target_setup
from verdiclip.capture.edit_scroll_native import (
    NativeStateMismatchError,
    ScrollObservation,
)
from verdiclip.capture.moving_edit_frame import PeerPixelSlots, PixelReservation
from verdiclip.capture.observe_native import NativeObservation, _Owner
from verdiclip.capture.observe_protocol import ObserveTarget
from verdiclip.capture.scroll_stitch import ScrollFrame, StitchedImage

# ruff: noqa: PLR2004 -- fixed dimensions, steps and byte limits of this profile.
# ruff: noqa: TRY301 -- every failed admission permanently latches its operation.

POSITIONS = (3, 0, 8, 13, 13, 0, 3)


class EvidenceEmitter(Protocol):
    """A synchronous journal admission using the producer's original timestamp."""

    def __call__(self, value: dict[str, Any], *, timestamp_ns: int) -> None:
        """Persist and acknowledge one exact event without restamping it."""
        ...


class EvidenceController(Protocol):
    """Authenticated context annotations, never a target-write permission."""

    def failure(self, reason: str, *, timestamp_ns: int) -> None:
        """Record the actual first failure in the active command context."""
        ...

    def restore_enter(self, failure_ns: int, *, timestamp_ns: int) -> None:
        """Bind a one-shot restore to the original failed context and stamps."""
        ...

    def restore_phase(
        self, phase_id: int, operation: str, *, timestamp_ns: int
    ) -> None:
        """Record an original ordered restore subphase entry."""
        ...


@dataclass(frozen=True, slots=True)
class Captured:
    """Retained complete source bytes and detached native evidence."""

    frame: ScrollFrame
    root_rgb: bytes
    state: bytes
    first_line: int
    line13_y: int
    _owners: tuple[PixelReservation, ...] = field(default=(), compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class ProviderResult:
    """Provisional provider result, never public output authorization."""

    frames: tuple[Captured, ...]
    image: StitchedImage
    provenance: tuple[tuple[int, int], ...]
    _owners: tuple[PixelReservation, ...] = field(default=(), compare=False, repr=False)


class Boundary(Protocol):
    """Retained native target boundary, confined to the peer thread."""

    @property
    def unsafe_write_reason(self) -> str | None:
        """Return the native permanent unsafe-write latch, if any."""
        ...

    def verify_top_setup(self) -> None:
        """Verify the owned starting pose before any write."""
        ...

    def seek(
        self, percent: float, *, restoring: bool, admit_write: Callable[[], None]
    ) -> None:
        """Write only after fresh original-clock and native admission."""
        ...

    def capture(self, expected_line: int, *, restoring: bool) -> Captured:
        """Acquire paired, bounded samples of the fixed complete native client."""
        ...


class MovingProvider:
    """A frame must be accepted before any subsequent fixed write can begin.

    Every supplied admission is the same authoritative deadline/cancel check,
    never a new timer. Native ownership uncertainty prohibits all later writes.
    """

    def __init__(self, boundary: Boundary, slots: PeerPixelSlots | None = None) -> None:
        self.boundary = boundary
        self.slots = slots if slots is not None else PeerPixelSlots()
        if getattr(boundary, "slots", self.slots) is not self.slots:
            raise ValueError("Native provider uses a different pixel allocation domain")
        self._owners: list[PixelReservation] = []
        self.frames: list[Captured] = []
        self.accepted = 0
        self.pending_move: int | None = None
        self.started = False
        self.failed = False
        self.cancelled = False
        self.restoration = "not_attempted"
        self.restoration_attempted = False
        self.write_uncertain = False
        self.finished = False
        self.safe_observation_failed = False
        self.failure_ns: int | None = None

    def _record_observation_failure(self, failure_ns: int) -> None:
        if self.failure_ns is None:
            self.failure_ns = failure_ns
            record = getattr(self.boundary, "record_failure", None)
            if record is not None:
                record(self.failure_ns, "observation")

    def _check_live(self, check: Callable[[], None]) -> None:
        if self.failed or self.cancelled or self.finished:
            raise ValueError("Moving provider is terminal")
        check()
        if self.failed or self.cancelled or self.finished:
            raise ValueError("Moving provider is terminal")

    def _admit(self, check: Callable[[], None]) -> None:
        self._check_live(check)
        if self.boundary.unsafe_write_reason is not None:
            self.write_uncertain = True
            raise ValueError("Unsafe native ownership")

    def begin(self, check: Callable[[], None]) -> None:
        """Verify the fixed top pose before the first write."""
        try:
            self._admit(check)
            if self.started:
                raise ValueError("Duplicate provider begin")
            self.boundary.verify_top_setup()
            self._check_live(check)
            self.started = True
        except BaseException:
            self.failed = True
            raise

    def move(self, step: int, check: Callable[[], None]) -> None:
        """Perform only the next fixed, freshly guarded, completed move."""
        try:
            self._admit(check)
            if (
                not self.started
                or type(step) is not int
                or step != self.accepted + 1
                or not 1 <= step <= len(POSITIONS)
                or self.pending_move is not None
                or len(self.frames) != self.accepted
            ):
                raise ValueError("Unexpected moving step")
            restoring = step == len(POSITIONS)
            if restoring:
                self.restoration_attempted = True
            self.write_uncertain = True
            self.boundary.seek(
                100.0 * POSITIONS[step - 1] / 13,
                restoring=restoring,
                admit_write=lambda: self._check_live(check),
            )
            self._check_live(check)
            self.write_uncertain = False
            self.pending_move = step
        except BaseException:
            failure_ns = time.monotonic_ns()
            self.failed = True
            if getattr(self.boundary, "safe_observation_failed", False):
                self.write_uncertain = False
                self.safe_observation_failed = True
                self._record_observation_failure(failure_ns)
            raise

    def _validate_capture(self, value: Captured, step: int) -> None:
        if (
            value.first_line != POSITIONS[step - 1]
            or value.frame.width != 493
            or value.frame.height != 413
            or type(value.frame.rgb) is not bytes
            or len(value.frame.rgb) != 493 * 413 * 3
            or type(value.root_rgb) is not bytes
            or len(value.root_rgb) != 533 * 661 * 3
            or type(value.state) is not bytes
            or not 0 < len(value.state) <= 16_384
        ):
            raise ValueError("Changed fixed capture")
        if step == 3 and self.frames[1].line13_y - value.line13_y != 128:
            raise ValueError("First native displacement mismatch")
        if step == 4 and self.frames[2].line13_y - value.line13_y != 80:
            raise ValueError("Final native displacement mismatch")
        repeat = {5: 3, 6: 1, 7: 0}.get(step)
        if repeat is not None and value != self.frames[repeat]:
            raise ValueError("Repeated or restored capture mismatch")

    def capture(self, step: int, check: Callable[[], None]) -> Captured:
        """Retain every full source frame, including repeats and restoration."""
        observing = ""
        try:
            self._admit(check)
            if (
                type(step) is not int
                or step != self.pending_move
                or step != len(self.frames) + 1
            ):
                raise ValueError("Unexpected moving capture")
            root_owner = self.slots.reserve(f"source_root_{step}")
            try:
                client_owner = self.slots.reserve(f"source_client_{step}")
            except BaseException:
                root_owner.release()
                raise
            self._owners.extend((root_owner, client_owner))
            observing = "boundary"
            value = self.boundary.capture(POSITIONS[step - 1], restoring=step == 7)
            observing = ""
            # Source slots already cover these aliases. The returned temporary
            # owners die only after the replacement has retained both sources.
            root_owner.bind(value.root_rgb)
            client_owner.bind(value.frame.rgb)
            value = replace(value, _owners=(root_owner, client_owner))
            self._check_live(check)
            observing = "validation"
            self._validate_capture(value, step)
            observing = ""
            self._check_live(check)
            self.frames.append(value)
            if step == 7:
                self.restoration = "verified"
        except BaseException as error:
            failure_ns = time.monotonic_ns()
            self.failed = True
            self.safe_observation_failed = (
                (
                    observing == "validation"
                    or (
                        observing == "boundary"
                        and getattr(self.boundary, "safe_observation_failed", False)
                    )
                )
                and isinstance(error, ValueError)
                and self.pending_move == step
                and not self.write_uncertain
                and self.boundary.unsafe_write_reason is None
            )
            if self.safe_observation_failed:
                self._record_observation_failure(failure_ns)
            raise
        else:
            return value

    def accept(self, step: int, check: Callable[[], None]) -> None:
        """Commit only after the matching transport proof has been validated."""
        try:
            self._admit(check)
            if (
                type(step) is not int
                or step != self.pending_move
                or step != len(self.frames)
                or step != self.accepted + 1
            ):
                raise ValueError("Unexpected frame acceptance")
            self._check_live(check)
            self.accepted = step
            self.pending_move = None
        except BaseException:
            self.failed = True
            raise

    def finish(self, check: Callable[[], None]) -> ProviderResult:
        """Assemble only the three source poses after verified normal restore."""
        try:
            self._admit(check)
            if self.accepted != 7 or self.restoration != "verified":
                raise ValueError("Moving sequence is incomplete")
            temporary = []
            for name in (
                "crop_temp_1",
                "crop_temp_2",
                "assembly_chunks",
                "assembly_final",
            ):
                owner = self.slots.reserve(name)
                self._owners.append(owner)
                temporary.append(owner)
            image, provenance = assemble(
                tuple(frame.frame for frame in self.frames[1:4]), (128, 80)
            )
            self._check_live(check)
            temporary[-1].bind(image.rgb)
            result = ProviderResult(
                tuple(self.frames),
                image,
                tuple((source + 2, row) for source, row in provenance),
                (temporary[-1],),
            )
            for owner in temporary[:-1]:
                owner.release()
                self._owners.remove(owner)
            self.finished = True
        except BaseException:
            self.failed = True
            raise
        else:
            return result

    def cancel(self) -> None:
        """Immediate cancellation never authorizes a restorative write."""
        self.cancelled = self.failed = True
        self.restoration = "prohibited"

    def _restore_admit(self, check: Callable[[], None]) -> None:
        if self.cancelled:
            raise ValueError("Restoration prohibited after cancellation")
        check()
        if self.cancelled:
            raise ValueError("Restoration prohibited after cancellation")

    def fail(self, check_restore: Callable[[], None]) -> None:
        """Try one known-safe local restore, never a new success or transport RPC.

        This primitive is not invoked by the public research peer. Its failure
        paths cancel without further writes; only the normal seventh restoration
        is integrated. Autonomous failure restoration remains unqualified, and
        full restoration/error paths must be verified before any user release.

        A future separately reviewed caller must supply fresh admission against
        the original reserve. Live failed sampling aliases prohibit a restore
        until their traceback is discarded; capacity cannot be recycled
        underneath retained evidence.
        """
        self.failed = True
        if (
            self.cancelled
            or self.write_uncertain
            or self.boundary.unsafe_write_reason is not None
        ):
            self.restoration = "prohibited"
            return
        if (
            not self.frames
            or self.restoration_attempted
            or not self.safe_observation_failed
        ):
            return
        if any(
            name.startswith(("sample_root_", "crop_temp_"))
            for name in self.slots.active_slots
        ):
            self.restoration = "prohibited"
            return
        self.restoration_attempted = True
        self.restoration = "failed"
        try:
            self._restore_admit(check_restore)
            restore = getattr(self.boundary, "begin_failure_restore", None)
            if restore is not None:
                restore(self.failure_ns)
        except BaseException:
            self.restoration = "prohibited"
            raise
        self.write_uncertain = True
        self.boundary.seek(
            100.0 * 3 / 13,
            restoring=True,
            admit_write=lambda: self._restore_admit(check_restore),
        )
        self._restore_admit(check_restore)
        self.write_uncertain = False
        restored = self.boundary.capture(3, restoring=True)
        self._restore_admit(check_restore)
        final = getattr(self.boundary, "finish_failure_restore", None)
        if final is not None:
            final()
        if restored != self.frames[0]:
            raise ValueError("Failure restoration mismatch")
        complete = getattr(self.boundary, "complete_failure_restore", None)
        if complete is not None:
            complete()
        self._restore_admit(check_restore)
        self.restoration = "verified"

    def dispose(self) -> None:
        """Drop retained frames before their pixel reservations, without native work."""
        self.failed = True
        self.frames.clear()
        # Captured/result aliases retain the same reservations. Never release
        # capacity while one of those controlled aliases still owns the bytes.
        self._owners.clear()


class _TimedScrollObservation(ScrollObservation):
    """New-profile instrumentation without changing the extracted native adapter."""

    budget: Any
    write_started_ns: int | None = None
    write_returned_ns: int | None = None

    def _reset_write_evidence(self) -> None:
        self.write_started_ns = self.write_returned_ns = None

    def _emit_at(self, value: dict[str, Any], timestamp_ns: int) -> None:
        self.budget.emit_at(value, timestamp_ns=timestamp_ns)

    @override
    def own(self, release: Callable[[], object]) -> _Owner:
        # Registration itself cannot log or postcheck: ownership must precede
        # every fallible operation after a HANDLE/interface/array is returned.
        def close() -> object:
            return self.budget.release(
                getattr(release, "__name__", "native.release"), release
            )

        return super().own(close)

    @override
    def raw(self, function: Any, *args: object) -> Any:  # noqa: C901, PLR0915 -- preserve native output and failure ordering
        """ACK the original start before dispatch; return outputs before failure."""
        self.budget.check()
        writing = self._write_admission is not None
        if writing:
            self.budget.restore_write_phase()
        started_ns = time.monotonic_ns()
        deadline_ns = min(started_ns + 3_000_000_000, self.budget.phase_deadline_ns)
        self._call_deadline = math.nextafter(deadline_ns / 1e9, -math.inf)
        name = getattr(function, "__name__", "COM")
        started = False
        result: Any = None
        try:
            self._attempt("native_call", {"name": name})
            self._emit_at(
                {
                    "event": "call_start",
                    "name": name,
                    "effective_deadline_ns": deadline_ns,
                },
                started_ns,
            )
            started = True
            self.budget.check()
            if time.monotonic_ns() >= deadline_ns:
                raise RuntimeError("Native start logging deadline")
            if writing:
                admit, self._write_admission = self._write_admission, None
                if admit is None:
                    raise ValueError("Missing native write admission")
                admit()
                self.budget.check()
            # Evidence ACK, caller cancellation and native guard precede the
            # same original deadline check immediately before actual dispatch.
            if time.monotonic_ns() >= deadline_ns:
                raise RuntimeError("Native dispatch deadline")

            def dispatch(*arguments: object) -> Any:  # noqa: ANN401 -- retain exact native output
                if time.monotonic_ns() >= deadline_ns:
                    raise RuntimeError("Native dispatch deadline")
                if writing:
                    self.write_started_ns = time.monotonic_ns()
                output = function(*arguments)
                if writing:
                    self.write_returned_ns = time.monotonic_ns()
                return output

            result = NativeObservation.raw(self, dispatch, *args)
        except Exception as error:
            self._latch(error)
            if started:
                try:
                    ended_ns = time.monotonic_ns()
                    self._emit_at(
                        {"event": "call_end", "seconds": (ended_ns - started_ns) / 1e9},
                        ended_ns,
                    )
                except Exception as diagnostic_error:  # noqa: BLE001 -- original failure wins
                    error.add_note(
                        f"Native evidence failed: {type(diagnostic_error).__name__}"
                    )
            raise
        try:
            # Keep result (including native output pointers in args) alive and
            # never raise after return until the caller registers its owner.
            ended_ns = time.monotonic_ns()
            self._emit_at(
                {"event": "call_end", "seconds": (ended_ns - started_ns) / 1e9},
                ended_ns,
            )
            self._observed("native_call", {"name": name, "result": result})
            if time.monotonic_ns() >= deadline_ns:
                raise RuntimeError("Native call/logging deadline")
            self.budget.check()
        except Exception as error:  # noqa: BLE001 -- return acquired outputs before failed postcheck
            self._latch(error)
        return result


class NativeBoundary:
    """Fixed native resource ownership for the packaged research peer only.

    Every operation runs on the peer thread with the original session admission.
    The extracted native guards and ownership ordering remain unchanged.
    """

    def __init__(  # noqa: C901, PLR0913, PLR0917 -- fixed peer construction contract
        self,
        target: ObserveTarget,
        profile_evidence: dict[str, Any],
        deadline_ns: int,
        check: Callable[[], object],
        slots: PeerPixelSlots,
        emit: Callable[[dict[str, Any]], None],
        *,
        evidence_emit: EvidenceEmitter | None = None,
        evidence_controller: EvidenceController | None = None,
        phase_deadline_ns: int | None = None,
    ) -> None:
        initial_deadline_ns = (
            deadline_ns if phase_deadline_ns is None else phase_deadline_ns
        )
        if (
            type(deadline_ns) is not int
            or type(initial_deadline_ns) is not int
            or not 0 < initial_deadline_ns <= deadline_ns
        ):
            raise ValueError("Expanded native initial phase deadline")
        self.check = check
        self.slots = slots
        self.last_move: dict[str, Any] | None = None
        self.last_acquisition: dict[str, int] | None = None
        self.safe_observation_failed = False
        self._admission_failed = False
        self.emit = emit
        self.evidence_emit = evidence_emit
        self.evidence_controller = evidence_controller
        self._restore_ceiling_ns: int | None = None
        self._restore_phase_id = 0
        self.native: _TimedScrollObservation | None = None
        self.source: mss.MSS | None = None
        self._bgra: list[PixelReservation] = []
        self._deadline_ns = deadline_ns
        self._check_clock = time.monotonic
        outer = self

        class Budget(ProbeBudget):
            phase_deadline_ns = initial_deadline_ns

            def emit_at(self, value: dict[str, Any], *, timestamp_ns: int) -> None:
                outer._emit_at(value, timestamp_ns)

            def release(self, name: str, operation: Callable[[], object]) -> object:
                return outer._release(name, operation)

            def restore_write_phase(self) -> None:
                if outer._restore_ceiling_ns is not None:
                    outer._restore_phase(2, "write", 3_000_000_000)

            @override
            def check(self) -> None:
                try:
                    outer.check()
                    super().check()
                    outer.check()
                except BaseException:
                    outer._admission_failed = True
                    raise

        self.budget = Budget(
            math.nextafter(initial_deadline_ns / 1e9, -math.inf),
            lambda value: self._emit_at(value, time.monotonic_ns()),
        )
        attestation = profile_evidence["font_attestation"]
        identity = {
            "epoch": attestation["epoch"],
            "pid": target.pid,
            "creation": target.process_created_100ns,
            "root": target.hwnd,
            "child": attestation["child"],
        }
        setup = validate_target_setup(profile_evidence["target_setup"], identity)
        self.expected_tick = setup["after_input_tick"]
        try:
            self.budget.check()
            if setup["completed_monotonic"] > self.budget.last:
                raise ValueError("Future owned setup receipt")
            self.native = _TimedScrollObservation(
                target,
                identity["child"],
                self.budget,
                font_attestation=attestation,
                epoch=identity["epoch"],
            )
            self.root = self.native._client_rect()
            if (self.root[2] - self.root[0], self.root[3] - self.root[1]) != (533, 661):
                raise ValueError("Changed fixed root geometry")
            self._open_capture()
        except BaseException:
            self.close()
            raise

    def _emit_at(self, value: dict[str, Any], timestamp_ns: int) -> None:
        try:
            if self.evidence_emit is not None:
                self.evidence_emit(value, timestamp_ns=timestamp_ns)
            else:
                self.emit(value)
        except Exception as error:
            self._admission_failed = True
            self.budget.failure = self.budget.failure or error
            raise

    def _call_start(self, name: str, started_ns: int) -> int:
        deadline_ns = min(started_ns + 3_000_000_000, self.budget.phase_deadline_ns)
        self._emit_at(
            {"event": "call_start", "name": name, "effective_deadline_ns": deadline_ns},
            started_ns,
        )
        return deadline_ns

    def _admit_call(self, deadline_ns: int) -> None:
        self.budget.check()
        if time.monotonic_ns() >= deadline_ns:
            raise RuntimeError("Native start logging deadline")

    def _call_end(self, started_ns: int) -> None:
        ended_ns = time.monotonic_ns()
        self._emit_at(
            {"event": "call_end", "seconds": (ended_ns - started_ns) / 1e9}, ended_ns
        )

    def _end_preserving(self, started_ns: int) -> None:
        primary = sys.exception()
        try:
            self._call_end(started_ns)
        except Exception as error:
            self._admission_failed = True
            self.budget.failure = self.budget.failure or error
            if primary is None:
                raise
            primary.add_note(f"Native evidence failed: {type(error).__name__}")

    def _open_capture(self) -> None:
        for name in ("sample_bgra_1", "sample_bgra_2"):
            self._bgra.append(self.slots.reserve(name))
        started_ns = time.monotonic_ns()
        deadline_ns = self._call_start("mss.open", started_ns)
        try:
            self._admit_call(deadline_ns)
            # Resource ownership is stored before call-end delivery/postchecks.
            self.source = mss.MSS(with_cursor=False)
        finally:
            self._end_preserving(started_ns)
        self.budget.check()
        if time.monotonic_ns() >= deadline_ns:
            raise TimeoutError("MSS acquisition deadline")

    @property
    def unsafe_write_reason(self) -> str | None:
        """Expose the permanent native guard/write failure latch."""
        if self._admission_failed:
            return "Original admission failed"
        return None if self.native is None else self.native.unsafe_write_reason

    def set_deadline(self, deadline_ns: int) -> None:
        """Change phase bounds only within the original, never renewed ceiling."""
        if type(deadline_ns) is not int or not 0 < deadline_ns <= self._deadline_ns:
            raise ValueError("Expanded native phase deadline")
        self.budget.phase_deadline_ns = deadline_ns
        self.budget.deadline = math.nextafter(deadline_ns / 1e9, -math.inf)
        self.budget.check()

    def record_failure(self, failure_ns: int, reason: str) -> None:
        """Attempt original failure evidence without replacing its exception."""
        if self.evidence_controller is not None:
            try:
                self.evidence_controller.failure(reason, timestamp_ns=failure_ns)
            except Exception as error:  # noqa: BLE001 -- original observation error wins
                self._admission_failed = True
                self.budget.failure = self.budget.failure or error

    def begin_failure_restore(self, failure_ns: int | None) -> None:
        """Enter only with the unchanged full reserve and original failure time."""
        self.budget.check()
        now = time.monotonic_ns()
        if (
            type(failure_ns) is not int
            or not 0 <= failure_ns <= now
            or now + 8_000_000_000 > self._deadline_ns
            or self._restore_ceiling_ns is not None
            or self.unsafe_write_reason is not None
        ):
            raise ValueError("Original failure restoration reserve unavailable")
        self._restore_ceiling_ns = min(self._deadline_ns, failure_ns + 8_000_000_000)
        if now >= self._restore_ceiling_ns:
            raise TimeoutError("Original failure restoration expired")
        if self.evidence_controller is not None:
            try:
                self.evidence_controller.restore_enter(failure_ns, timestamp_ns=now)
            except Exception:
                self._admission_failed = True
                raise
        self._restore_phase(1, "guard", 2_000_000_000)

    def _restore_phase(self, phase_id: int, operation: str, span_ns: int) -> None:
        self.budget.check()
        started_ns = time.monotonic_ns()
        if self._restore_ceiling_ns is None or phase_id != self._restore_phase_id + 1:
            raise ValueError("Invalid failure restoration subphase")
        deadline_ns = min(self._restore_ceiling_ns, started_ns + span_ns)
        if started_ns >= deadline_ns:
            raise TimeoutError("Original failure restoration expired")
        self._restore_phase_id = phase_id
        self.budget.phase_deadline_ns = deadline_ns
        self.budget.deadline = math.nextafter(deadline_ns / 1e9, -math.inf)
        if self.evidence_controller is not None:
            try:
                self.evidence_controller.restore_phase(
                    phase_id, operation, timestamp_ns=started_ns
                )
            except Exception:
                self._admission_failed = True
                raise
        self.budget.check()

    def finish_failure_restore(self) -> None:
        """Stamp final equality checks before they consume the original reserve."""
        self._restore_phase(4, "final", 1_000_000_000)

    def complete_failure_restore(self) -> None:
        """Recheck the same final subphase after full restored-byte comparison."""
        if self._restore_phase_id != 4:
            raise ValueError("Incomplete failure restoration subphases")
        self.budget.check()

    def verify_top_setup(self) -> None:
        """Freeze detached retained identity after the owned top-pose guard."""
        if self.native is None:
            raise ValueError("Native boundary is closed")
        state = self.native.snapshot()
        self.budget.check()
        if state["input_tick"] != self.expected_tick:
            self.native.unsafe_write_reason = "Target input epoch changed"
            raise ValueError("Target input epoch changed")
        if state["text_layout"]["first_visible_line"] != 0:
            raise ValueError("Initial target is not top")
        self.initial_state = state
        self.identity_evidence = json.loads(
            json.dumps(
                {
                    "root_runtime_id": self.native.root_id,
                    "panel_runtime_id": self.native.panel_id,
                    "root_structure": self.native.root_baseline,
                    "panel_structure": self.native.panel_baseline,
                },
                allow_nan=False,
            )
        )
        self.budget.check()

    def seek(
        self, percent: float, *, restoring: bool, admit_write: Callable[[], None]
    ) -> None:
        """Stamp the actual completed native write and its fresh paired readback."""
        if self.native is None:
            raise ValueError("Native boundary is closed")
        self.budget.check()
        self.last_move = None
        self.safe_observation_failed = False
        self.native._reset_write_evidence()

        def admission() -> None:
            admit_write()
            self.budget.check()

        self.native.seek(percent, restoring=restoring, admit_write=admission)
        self.budget.check()
        started = self.native.write_started_ns
        returned = self.native.write_returned_ns
        try:
            state = self.native.snapshot()
            if state["text_layout"]["first_visible_line"] != round(percent * 13 / 100):
                raise NativeStateMismatchError(
                    "Completed seek readback position mismatch"
                )
        except NativeStateMismatchError:
            self.safe_observation_failed = self.unsafe_write_reason is None
            raise
        readback = time.monotonic_ns()
        self.budget.check()
        completed = time.monotonic_ns()
        self.budget.check()
        if (
            started is None
            or returned is None
            or not started <= returned <= readback <= completed
        ):
            raise ValueError("Missing ordered native write evidence")
        self.last_move = {
            "write_started_ns": started,
            "write_returned_ns": returned,
            "readback_ns": readback,
            "completed_ns": completed,
            "native_state": state,
        }

    def _read_pixels(self) -> bytes:
        source = self.source
        if source is None:
            raise ValueError("Native boundary is closed")
        # A failed read traceback must keep these leases after close/disposal.
        bgra_owners = tuple(self._bgra)
        started_ns = time.monotonic_ns()
        deadline_ns = self._call_start("mss.grab", started_ns)
        try:
            self._admit_call(deadline_ns)
            shot = source.grab(
                {"left": self.root[0], "top": self.root[1], "width": 533, "height": 661}
            )
        finally:
            self._end_preserving(started_ns)
        self.budget.check()
        if time.monotonic_ns() >= deadline_ns:
            raise TimeoutError("MSS grab deadline")
        if (shot.width, shot.height) != (533, 661) or len(shot.raw) != 533 * 661 * 4:
            raise ValueError("Changed MSS root storage")
        pixels = bytes(shot.rgb)
        self.budget.check()
        # Keep native/copy owners through all post-return checks, including on
        # exceptions. The reference is deliberate even when no Python copy runs.
        del bgra_owners
        return pixels

    @staticmethod
    def _capture_pose(expected_line: int, *, restoring: bool) -> None:
        if (
            type(expected_line) is not int
            or expected_line not in POSITIONS
            or type(restoring) is not bool
            or (restoring and expected_line != 3)
        ):
            raise ValueError("Invalid fixed capture pose")

    def capture(self, expected_line: int, *, restoring: bool) -> Captured:  # noqa: C901 -- fixed ownership and restore phase order
        """Acquire exactly three equal roots paired with unchanged native state."""
        self.safe_observation_failed = False
        if self._restore_ceiling_ns is not None:
            self._restore_phase(3, "capture", 2_000_000_000)
        self._capture_pose(expected_line, restoring=restoring)
        self.budget.check()
        self.last_acquisition = None
        acquisition_started = time.monotonic_ns()
        native = self.native
        if native is None or self.source is None:
            raise ValueError("Native boundary is closed")
        try:
            state = native.snapshot()
            if state["text_layout"]["first_visible_line"] != expected_line:
                raise NativeStateMismatchError("Unexpected native capture pose")
        except NativeStateMismatchError:
            self.safe_observation_failed = self.unsafe_write_reason is None
            raise
        reservations: list[PixelReservation] = []
        for name in (
            "sample_root_1",
            "sample_root_2",
            "sample_root_3",
            "crop_temp_1",
            "crop_temp_2",
        ):
            reservations.append(self.slots.reserve(name))  # noqa: PERF401 -- retain each owner before the next acquisition

        def guard() -> None:
            self.budget.check()
            if native.snapshot() != state:
                raise NativeStateMismatchError(
                    "State changed during fixed frame readiness"
                )
            self.budget.check()

        sample_stamps: list[int] = []

        def sample_clock() -> float:
            value = self._check_clock()
            sample_stamps.append(time.monotonic_ns())
            return value

        try:
            pixels = cast(
                "bytes",
                stable_samples(
                    self._read_pixels,
                    guard,
                    self.budget.deadline,
                    clock=sample_clock,
                    sleep=time.sleep,
                ),
            )
        except ValueError as error:
            # Slot, crop, clock, logger and source API failures do not confer
            # restore authority. This is the extracted sampler's exact equality
            # failure or an explicitly fully-read native state mismatch only.
            self.safe_observation_failed = self.unsafe_write_reason is None and (
                isinstance(error, NativeStateMismatchError)
                or (
                    type(error) is ValueError
                    and error.args == ("Readiness sample changed; no reset",)
                )
            )
            raise
        self.budget.check()
        client = crop(pixels, self.root, state["native"]["client_screen"])
        reservations[2].bind(pixels)
        reservations[4].bind(client)
        result = Captured(
            ScrollFrame(493, 413, client),
            pixels,
            json.dumps(
                state, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode(),
            state["text_layout"]["first_visible_line"],
            state["text_layout"]["line13_y"],
            (reservations[2], reservations[4]),
        )
        self.budget.check()
        finished = time.monotonic_ns()
        self._record_acquisition(acquisition_started, finished, sample_stamps)
        self.budget.check()
        for index in (0, 1, 3):
            reservations[index].release()
        # Exceptions retain locals and reservations until their traceback dies.
        # Success returns owners for the only two still-live pixel payloads.
        return result

    def _record_acquisition(
        self, started: int, finished: int, sample_stamps: list[int]
    ) -> None:
        if len(sample_stamps) != 12:
            raise ValueError("Unexpected fixed sampling clock transcript")
        quiet_ns = sample_stamps[9] - sample_stamps[2]
        if quiet_ns < 200_000_000:
            raise ValueError("Insufficient exact sampling span")
        self.last_acquisition = {
            "started_ns": started,
            "finished_ns": finished,
            "samples": 3,
            "quiet_ns": quiet_ns,
        }

    def _release(self, name: str, operation: Callable[[], object]) -> object:
        """Release once despite lost admission/logging, never claim qualification."""
        first: BaseException | None = None
        started_ns: int | None = None
        started: float | None = None
        result: object = None

        def attempt(action: Callable[[], object]) -> None:
            nonlocal first
            try:
                action()
            except BaseException as error:  # noqa: BLE001 -- every owned release remains mandatory
                self._admission_failed = True
                if first is None:
                    first = error

        def start() -> None:
            nonlocal started_ns, started
            started_ns = time.monotonic_ns()
            self._emit_at(
                {
                    "event": "call_start",
                    "name": name,
                    "effective_deadline_ns": min(
                        started_ns + 3_000_000_000, self.budget.phase_deadline_ns
                    ),
                },
                started_ns,
            )
            started = self._check_clock()

        def release() -> None:
            nonlocal result
            result = operation()

        def end() -> None:
            if started_ns is None:
                raise ValueError("Missing original native cleanup timestamp")
            self._call_end(started_ns)
            completed = self._check_clock()
            now_ns = time.monotonic_ns()
            if (
                started is None
                or completed < started
                or completed - started >= 3
                or (
                    self.evidence_emit is not None
                    and now_ns >= self.budget.phase_deadline_ns
                )
            ):
                raise TimeoutError("Native cleanup call deadline")

        attempt(start)
        attempt(release)
        attempt(end)
        if first is not None:
            raise first
        return result

    def _close_resource(self, name: str, resource: Any) -> None:  # noqa: ANN401 -- the two fixed native resource types
        self._release(name, resource.close)

    def close(self) -> None:
        """Attempt all resources despite clocks/loggers and preserve first failure."""
        primary = sys.exception()
        first: BaseException | None = None
        bgra_owners = tuple(self._bgra)
        for name, resource in (
            ("mss.close", self.source),
            ("native.close", self.native),
        ):
            if resource is None:
                continue
            try:
                self._close_resource(name, resource)
            except BaseException as error:  # noqa: BLE001 -- every close is mandatory
                if first is None:
                    first = error
        self.source = self.native = None
        # Drop owners, never explicitly release capacity possibly still held by
        # failed sampling/native cleanup stacks. Their final aliases own them.
        self._bgra.clear()
        if first is not None:
            if primary is None:
                raise first
            primary.add_note(f"Native cleanup failed: {type(first).__name__}")
        del bgra_owners
