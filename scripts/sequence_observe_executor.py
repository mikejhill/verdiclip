"""Fixed supervised two-frame executor; no scrolling or parent-death profile.

All native references remain in this child. The fixed adjacent outer support
retains unchanged reference and exact native ownership algorithms.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import math
import re
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, Protocol, override

if TYPE_CHECKING:
    from verdiclip.capture.observe_protocol import ObserveTarget

Json = dict[str, Any]


def _support() -> ModuleType:
    """Load the one fixed adjacent sequence harness, never a supplied module path."""
    name = "_sequence_executor_outer_support"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).with_name("installed_sequence_smoke.py")
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Missing fixed sequence outer support")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


harness = _support()
base = harness.base
contract = harness.contract()
PREPARATION_SECONDS = 10.0
OUTER_SECONDS = 25.0
CALL_SECONDS = 3.0
WITNESS_X = 960
WITNESS_Y = 704
_FINAL_FRAME = 2
Journal = base.Journal
rect = base.rect
latest = base.latest
fixture_key = base.fixture_key
write = base.write
write_handshake = base.write_handshake
pin_preparation = base.pin_preparation
prepare_owned_case = base.prepare_owned_case
ownership_checkpoint = base.ownership_checkpoint
acquire_temporary = base.acquire_temporary
validate_temporary_retirement = base.validate_temporary_retirement
reference = base.reference


def logged_call[T](journal: Journal, name: str, operation: Callable[[], T]) -> T:
    """Flush first, then freshly admit native work under the original call/case caps."""
    journal.check()
    started = time.monotonic()
    journal.emit("call_start", name)
    journal.check()
    if time.monotonic() - started >= CALL_SECONDS:
        raise RuntimeError("Call deadline expired before action")
    result = operation()
    journal.emit("call_end", name)
    journal.check()
    if time.monotonic() - started >= CALL_SECONDS:
        raise RuntimeError("Call deadline expired after action")
    return result


class Facade(Protocol):
    """Only public sequence actions used by the fixed controller."""

    def begin(self, caller: int, target: ObserveTarget, /) -> None:
        """Begin one fixed target."""
        ...

    def observe(self, caller: int, /) -> None:
        """Request the next frame."""
        ...

    def grant_frame_credit(
        self, generation: int, request: int, frame: int, index: int, /
    ) -> None:
        """Grant only the exact public token."""
        ...

    def cancel(self) -> None:
        """Cancel synchronously without a held grant."""
        ...

    def finish(self) -> None:
        """Finish only after both acceptances."""
        ...


class SequenceController:
    """Pure callback state machine for exactly two read-only frame requests."""

    def __init__(  # noqa: PLR0913, PLR0917 - fixed independent inputs
        self,
        facade: Facade,
        journal: Journal,
        generation: int,
        epoch: str,
        size: int,
        case: str,
    ) -> None:
        harness.case_index(case)
        self.facade, self.journal = facade, journal
        self.generation, self.epoch, self.case = generation, epoch, case
        self.transcripts = tuple(
            contract.CreditTranscript(generation, epoch, size, i) for i in (1, 2)
        )
        self.phase = "new"
        self.frame = 0
        self.accepted: list[Json] = []
        self.hold: Json | None = None
        self.chain = bytes(32)
        self.observe_calls: list[Json] = []
        self.sequence_deadline_ns = 0

    def begin(self, target: ObserveTarget) -> None:
        """Consume begin before calling the public API, including reentrant failures."""
        self.journal.check()
        if self.phase != "new":
            raise ValueError("Duplicate sequence begin")
        self.phase = "begin"
        self.facade.begin(1, target)

    def _observe(self) -> None:
        self.journal.check()
        self.frame += 1
        self.phase = "frame"
        event = {"frame_id": self.frame, "called_ns": time.monotonic_ns()}
        self.observe_calls.append(event)
        self.journal.call(
            "sequence.observe", lambda: self.facade.observe(self.frame + 1)
        )
        event["returned_ns"] = time.monotonic_ns()

    def reply(self, generation: int, caller: int, raw: bytes) -> None:
        """Check exact replies and issue only the next fixed action."""
        self.journal.check()
        contract._exact(generation, self.generation)
        value = contract.parse_json(raw)
        if self.phase == "begin":
            contract._exact(caller, 1)
            contract._object(
                value, "operation sequence_schema epoch max_frames deadline_ns"
            )
            for key, expected in {
                "operation": "sequence_begin",
                "sequence_schema": 1,
                "epoch": self.epoch,
                "max_frames": 8,
            }.items():
                contract._exact(value[key], expected)
            now = time.monotonic_ns()
            self.sequence_deadline_ns = contract._integer(
                value["deadline_ns"], now + 1, min(2**63 - 1, now + 30_000_000_000)
            )
            self._observe()
            return
        if self.phase != "frame" or self.frame not in (1, 2):
            raise ValueError("Unexpected sequence acceptance")
        contract._exact(caller, self.frame + 1)
        contract._object(value, "operation frame_id proof_sha256 sequence_sha256")
        contract._exact(value["operation"], "frame_accepted")
        contract._exact(value["frame_id"], self.frame)
        digest = value["proof_sha256"]
        if type(digest) is not str or re.fullmatch("[0-9a-f]{64}", digest) is None:
            raise ValueError("Invalid accepted proof digest")
        transcript = self.transcripts[self.frame - 1]
        if len(transcript.events) != (transcript.size + 65535) // 65536 or any(
            "grant_returned_ns" not in event for event in transcript.events
        ):
            raise ValueError("Acceptance before complete public grants")
        self.chain = hashlib.sha256(self.chain + bytes.fromhex(digest)).digest()
        contract._exact(value["sequence_sha256"], self.chain.hex())
        self.accepted.append({**value, "received_ns": time.monotonic_ns()})
        if self.frame == 1:
            self._observe()
        else:
            if self.case != "success":
                raise ValueError("Cancellation case unexpectedly accepted frame2")
            self.phase = "finishing"
            self.journal.call("sequence.finish", self.facade.finish)

    def credit(self, generation: int, raw: bytes) -> None:
        """Record each callback before immediate exact grant or frame2 cancellation."""
        self.journal.check()
        if self.phase != "frame":
            raise ValueError("Unexpected credit outside active frame")
        transcript = self.transcripts[self.frame - 1]
        received = time.monotonic_ns()
        identity = transcript.record_wait(generation, raw, received)
        if self.case == "cancel-frame2" and self.frame == _FINAL_FRAME:
            if self.hold is not None or len(transcript.events) != 1:
                raise ValueError("Duplicate frame2 hold")
            self.hold = {
                "token": transcript.events[0]["token"],
                "wait_received_ns": received,
                "cancel_called_ns": None,
                "cancel_returned_ns": None,
            }
            self.phase = "cancelling"
            self.hold["cancel_called_ns"] = time.monotonic_ns()
            self.journal.call("credit.cancel", self.facade.cancel)
            self.hold["cancel_returned_ns"] = time.monotonic_ns()
            return
        called = time.monotonic_ns()
        self.journal.call(
            "credit.grant", lambda: self.facade.grant_frame_credit(*identity)
        )
        transcript.record_grant(identity, called, time.monotonic_ns())

    def terminal(self) -> None:
        """Require the exact expected complete callback history before final proof."""
        self.journal.check()
        expected = "finishing" if self.case == "success" else "cancelling"
        if self.phase != expected or len(self.accepted) != (
            2 if self.case == "success" else 1
        ):
            raise ValueError("Unexpected sequence terminal history")
        self.phase = "terminal"


class RetainedConsumer(threading.Thread):
    """Off-GUI immutable result comparison, with no pixel aliases in diagnostics."""

    def __init__(  # noqa: PLR0913, PLR0917 - fixed independent inputs
        self,
        sequence: object,
        rgb: bytes,
        terminal: bytes,
        rectangle: tuple[int, ...],
        target: bytes,
        input_tick: int,
        transcripts: tuple[Any, ...],
        accepted: tuple[str, ...],
        pid: int,
        *,
        public_calls: bytes,
        case_deadline_ns: int,
        sequence_deadline_ns: int,
    ) -> None:
        super().__init__(name="owned-sequence-pixel-consumer", daemon=False)
        self.inputs: tuple[Any, ...] | None = (
            sequence,
            rgb,
            terminal,
            rectangle,
            target,
            input_tick,
            transcripts,
            accepted,
        )
        self.pid = pid
        self.timing = {
            "public_calls": public_calls,
            "case_deadline_ns": case_deadline_ns,
            "sequence_deadline_ns": sequence_deadline_ns,
        }
        self.result: Json | None = None
        self.error: str | None = None
        self.started_at = 0.0

    def begin(self, journal: Journal) -> float:
        """Release unstarted aliases even if the first deadline/log check fails."""
        try:
            journal.check()
            started = self.started_at = time.monotonic()
            journal.emit("call_start", "retained.compare")
            journal.check()
            if time.monotonic() - started >= CALL_SECONDS:
                raise RuntimeError("Retained comparison exceeded3s before start")  # noqa: TRY301 - discard unstarted inputs
            self.start()
        except BaseException:
            self.inputs = None
            raise
        return started

    def poll(self, journal: Journal, started: float) -> Json | None:
        """No completion at the original deadline or renewed comparison allowance."""
        journal.check()
        if time.monotonic() - started >= CALL_SECONDS:
            raise RuntimeError("Retained comparison exceeded3s")
        if self.is_alive():
            return None
        self.join(timeout=0)
        journal.emit("call_end", "retained.compare")
        journal.check()
        if time.monotonic() - started >= CALL_SECONDS:
            raise RuntimeError("Retained comparison exceeded3s")
        if self.error is not None or self.result is None:
            raise RuntimeError(f"Retained comparison failed: {self.error}")
        return self.result

    def dispose(self, journal: Journal) -> None:
        """Drop unstarted data; join within the existing deadlines."""
        self.inputs = None
        if self.ident is not None:
            self.join(
                timeout=max(
                    0.0,
                    min(self.started_at + CALL_SECONDS, journal.deadline)
                    - time.monotonic(),
                )
            )

    @override
    def run(self) -> None:
        """Drop immutable aliases before thread completion, retaining no traceback."""
        inputs, self.inputs = self.inputs, None
        try:
            if inputs is None:
                raise RuntimeError("Missing retained sequence inputs")  # noqa: TRY301 - bounded worker diagnostic
            self.result = contract.compare_retained_sequence(
                *inputs, pid=self.pid, **self.timing
            )
        except BaseException as error:  # noqa: BLE001 - do not retain traceback/pixels
            self.error = repr(error)[:2048]
        finally:
            inputs = None


def child(args: argparse.Namespace) -> int:
    """Close the journal even for pre-loop import, provenance or setup failures."""
    journal = Journal.__new__(Journal)
    try:
        Journal.__init__(
            journal,
            args.output / "calls.jsonl",
            args.preparation_start + PREPARATION_SECONDS,
        )
    except BaseException:
        stream = getattr(journal, "stream", None)
        if stream is not None:
            stream.close()
        raise
    try:
        return _run_child(args, journal)
    finally:
        journal.stream.close()


def _run_child(args: argparse.Namespace, journal: Journal) -> int:  # noqa: C901, PLR0912, PLR0915 - fixed owned executor lifecycle
    """All native references run here, never in the outer watchdog process."""
    held_index = harness.case_index(args.case)
    preparation_deadline = args.preparation_start + PREPARATION_SECONDS
    absolute_deadline = args.preparation_start + OUTER_SECONDS
    module = journal.call("installed.imports", base.support)
    provenance = journal.call(
        "installed.verify", lambda: harness.provenance(args.wheel)
    )
    from PySide6.QtCore import (  # noqa: PLC0415 - isolated executor/runtime import
        Qt,
        QTimer,
    )
    from PySide6.QtWidgets import (  # noqa: PLC0415 - isolated executor/runtime import
        QApplication,
    )

    from verdiclip.capture.observe_protocol import (  # noqa: PLC0415 - isolated runtime import
        ObserveTarget,
    )
    from verdiclip.platform.sequence_channel import (  # noqa: PLC0415 - isolated runtime import
        SequenceChannel,
    )
    from verdiclip.platform.sequence_runtime import (  # noqa: PLC0415 - isolated runtime import
        inspect_runtime,
    )

    def setup_gui() -> tuple[Any, ...]:
        app = QApplication([])
        window = module.WitnessWindow()
        window.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        window.setGeometry(WITNESS_X, WITNESS_Y, 32, 32)
        window.show()
        heartbeat = QTimer(window)
        heartbeat.timeout.connect(window.heartbeat)
        heartbeat.start(10)
        channel = SequenceChannel()
        return app, window, heartbeat, channel

    app, window, _heartbeat, channel = logged_call(journal, "gui.setup", setup_gui)
    result: Json = {
        "passed": False,
        "first_failure": None,
        "responsiveness_qualified": False,
        "case": "two-read-only-owned-wpf-explicit-ownership",
        "credit_case": getattr(args, "case", "success"),
        "cold_startup_in_capture_latency": False,
        "preparation_start": args.preparation_start,
        "provenance": provenance,
        "contract_validator_sha256": hashlib.sha256(
            Path(__file__).with_name("sequence_observe_contract.py").read_bytes()
        ).hexdigest(),
        "cancel_validator_sha256": hashlib.sha256(
            Path(__file__).with_name("sequence_observe_contract.py").read_bytes()
        ).hexdigest()
        if held_index is not None
        else None,
        "source_sha256": {
            name: hashlib.sha256(
                Path(__file__).with_name(name).read_bytes()
            ).hexdigest()
            for name in (
                "sequence_observe_executor.py",
                "sequence_observe_contract.py",
                "sequence_installed_runtime.py",
            )
        },
    }
    guard = None
    generation = 0
    identity: Json = {}
    target = None
    expected: Json = {}
    before_root = b""
    before_panel = b""
    submitted = False
    baseline_pins = None
    baseline_profile: Json = {}
    temporary_pins = None
    temporary_profile: Json = {}
    consumer: RetainedConsumer | None = None
    consumer_started = 0.0
    consumer_receipt: Json = {}
    consumer_timer = QTimer(window)
    controller: SequenceController | None = None

    def identified(value: int, payload: bytes) -> None:
        if value != generation or identity:
            raise RuntimeError("Unexpected observe peer identity")
        identity.update(contract.parse_json(payload))
        harness._ready_profile(identity)
        write_handshake(
            args.output / "peer-identity.json",
            {"schema": 1, "session": args.session, "peer": identity},
        )

    def ready(value: int) -> None:
        nonlocal submitted, temporary_pins, temporary_profile, controller
        if value != generation or submitted or target is None or guard is None:
            raise RuntimeError("Unexpected observe ready")
        journal.call("reference.before_observe", guard.check)
        if fixture_key(latest(args.states)) != expected:
            raise RuntimeError("Fixture changed before observe")
        if baseline_pins is None:
            raise RuntimeError("Missing retained baseline ownership")
        temporary_pins, temporary_profile = acquire_temporary(
            args.output, args.session, identity, baseline_profile, journal
        )
        ownership_checkpoint(
            args.output,
            args.session,
            "pre-observe",
            identity,
            baseline_pins,
            baseline_profile,
            journal,
            temporary_profile,
        )
        current = temporary_pins.observe()
        if (
            current["cleanup_errors"]
            or current["processes"] != temporary_profile["records"]["processes"]
        ):
            raise RuntimeError("Temporary identity changed before observe")
        result["temporary_profile"] = temporary_profile
        journal.call("reference.after_ownership", guard.check)
        submitted = True
        controller = SequenceController(
            channel,
            journal,
            generation,
            identity["epoch"],
            len(before_panel),
            args.case,
        )
        result["credit_public_events"] = [t.events for t in controller.transcripts]
        active_controller, active_target = controller, target
        journal.call("sequence.begin", lambda: active_controller.begin(active_target))

    def grant_credit(value: int, raw: bytes) -> None:
        if controller is None or result["first_failure"] is not None:
            raise RuntimeError("Unexpected sequence credit callback")
        controller.credit(value, raw)
        if controller.hold is not None:
            result["credit_hold"] = controller.hold

    def replied(value: int, caller: int, raw: bytes) -> None:
        if controller is None or result["first_failure"] is not None:
            raise RuntimeError("Unexpected sequence reply")
        controller.reply(value, caller, raw)
        result["accepted_frames"] = controller.accepted
        result["observe_calls"] = controller.observe_calls
        result["sequence_deadline_ns"] = controller.sequence_deadline_ns
        result["case_deadline_ns"] = math.floor(journal.deadline * 1_000_000_000)

    def terminal(value: int, payload: bytes) -> None:
        nonlocal consumer, consumer_started, consumer_receipt
        if value != generation or guard is None or target is None:
            raise RuntimeError("Unexpected sequence terminal")
        receipt = contract.parse_json(payload)
        result["terminal"] = receipt
        if result["first_failure"] is not None:
            app.exit(1)
            return
        journal.check()
        if controller is None:
            raise RuntimeError("Missing sequence controller")
        controller.terminal()
        timing = {
            "public_calls": contract.canonical({"calls": controller.observe_calls}),
            "case_deadline_ns": math.floor(journal.deadline * 1_000_000_000),
            "sequence_deadline_ns": controller.sequence_deadline_ns,
        }
        if held_index is not None:
            result["credit_cancellation"] = contract.validate_cancel(
                payload,
                identity,
                controller.transcripts[0],
                controller.accepted[0]["proof_sha256"],
                controller.accepted[0]["received_ns"],
                controller.hold,
                **timing,
            )
            try:
                channel.take_sequence_result(value)
            except (ValueError, RuntimeError):
                result["cancelled_take_rejected"] = True
            else:
                raise RuntimeError("Cancelled sequence was transferable")
            complete_reference(receipt)
            return
        sequence = channel.take_sequence_result(value)
        try:
            channel.take_sequence_result(value)
        except (ValueError, RuntimeError):
            result["second_take_rejected"] = True
        else:
            raise RuntimeError("Retained sequence was transferable twice")
        consumer_receipt = receipt
        consumer = RetainedConsumer(
            sequence,
            before_panel,
            payload,
            rect(expected["rect"]),
            contract.canonical(target.value()),
            guard.initial["input_tick"],
            tuple(controller.transcripts),
            tuple(item["proof_sha256"] for item in controller.accepted),
            identity["pid"],
            **timing,
        )
        sequence = None
        consumer_started = consumer.begin(journal)
        consumer_timer.start(1)

    def consumed() -> None:
        if consumer is None:
            raise RuntimeError("Missing retained consumer")
        evidence = consumer.poll(journal, consumer_started)
        if evidence is None:
            return
        consumer_timer.stop()
        result["retained_pixels"] = evidence
        complete_reference(consumer_receipt)

    def complete_reference(receipt: Json) -> None:
        """Both cases retain the same reference and explicit retirement gates."""
        if guard is None or target is None:
            raise RuntimeError("Missing retained reference guard")
        if baseline_pins is None:
            raise RuntimeError("Missing retained baseline ownership")
        if temporary_pins is None:
            raise RuntimeError("Missing retained temporary handles")
        retired = temporary_pins.retired()
        if retired.get("retired") is not True:
            raise RuntimeError("Temporary retained handles have not retired")
        retirement = {"witness": retired, "helper": receipt["helper"]}
        validate_temporary_retirement(
            retirement, temporary_profile, cancelled=held_index is not None
        )
        result["temporary_retirement"] = retirement
        write_handshake(
            args.output / "temporary-retired.json",
            {
                "schema": 1,
                "session": args.session,
                "retired": retirement,
            },
        )
        ownership_checkpoint(
            args.output,
            args.session,
            "post-terminal",
            None,
            baseline_pins,
            baseline_profile,
            journal,
        )
        after_root, after_panel = reference(
            journal, guard, args.states, expected, args.output, "after"
        )
        if before_root != after_root or before_panel != after_panel:
            raise RuntimeError("Post-terminal independent pixels changed")
        journal.call("reference.post_terminal", guard.check)
        journal.check()
        result.update(
            passed=True,
            identity=identity,
            target=target.value(),
            reference={
                "rect": expected["rect"],
                "root": guard.initial,
                "state": expected,
                "rgb_sha256": hashlib.sha256(before_panel).hexdigest(),
                "full_root_sha256": hashlib.sha256(before_root).hexdigest(),
                "pre_post_exact": True,
            },
            generation=generation,
        )
        app.exit(0)

    def start() -> None:
        nonlocal guard, generation, target, expected
        nonlocal before_root, before_panel, baseline_pins, baseline_profile
        wait_until = preparation_deadline
        while not list(args.states.glob("[0-9][0-9][0-9][0-9].json")):
            if time.monotonic() >= wait_until:
                raise RuntimeError("Fixture telemetry startup missing")
            time.sleep(0.01)
        fixture, expected, guard = pin_preparation(
            args.states, args.fixture_pid, journal
        )
        # The witness widget is fixed outside both the selected panel and root.
        root = rect(guard.initial["rect"])
        if not (root[2] <= WITNESS_X or root[3] <= WITNESS_Y):
            raise RuntimeError("GUI witness would overlap reference root")
        reference_guard = guard
        result["witness_rect"] = journal.call(
            "reference.widget_geometry",
            lambda: reference_guard.excluded_window(int(window.winId())),
        )
        handoff, baseline_pins, baseline_profile = prepare_owned_case(
            args.output,
            args.states,
            args.session,
            args.preparation_start,
            args.fixture_pid,
            fixture,
            expected,
            guard,
            journal,
        )
        result.update(
            preparation_ready=handoff["ready"],
            case_start=handoff["case_start"],
            case_deadline=journal.deadline,
            absolute_deadline=absolute_deadline,
        )
        result["ownership_profile"] = baseline_profile
        ownership_checkpoint(
            args.output,
            args.session,
            "pre-reference",
            None,
            baseline_pins,
            baseline_profile,
            journal,
        )
        before_root, before_panel = reference(
            journal, guard, args.states, expected, args.output, "before"
        )
        selected = rect(expected["rect"])
        if (selected[2] - selected[0], selected[3] - selected[1]) != (510, 430):
            raise ValueError("Expected fixed 510 by 430 full WPF panel")
        target = ObserveTarget(
            args.fixture_pid,
            guard.created,
            guard.hwnd,
            ((selected[0] + selected[2]) // 2, (selected[1] + selected[3]) // 2),
        )
        runtime = journal.call("installed.runtime", inspect_runtime)
        generation = logged_call(
            journal, "channel.start", lambda: channel.start(runtime)
        )

    def failed(_type: object, error: BaseException, _trace: object) -> None:
        consumer_timer.stop()
        if result["first_failure"] is None:
            result["first_failure"] = repr(error)
        write(args.output / "diagnostic.json", result)
        if generation and channel._state not in ("idle", "terminal"):
            channel.cancel()
        else:
            app.exit(1)

    consumer_timer.timeout.connect(consumed)
    channel.reply.connect(replied)
    channel.credit_required.connect(grant_credit)
    channel.identity.connect(identified)
    channel.ready.connect(ready)
    channel.terminal.connect(terminal)
    old_hook = sys.excepthook
    sys.excepthook = failed
    QTimer.singleShot(0, start)
    try:
        code = app.exec()
    finally:
        sys.excepthook = old_hook
        if consumer is not None:
            try:
                consumer.dispose(journal)
            except Exception as error:  # noqa: BLE001 - release native pins despite join failure
                result["passed"] = False
                result["first_failure"] = result["first_failure"] or repr(error)
            if consumer.is_alive():
                result["passed"] = False
                result["first_failure"] = (
                    result["first_failure"]
                    or "Consumer did not finish before existing deadline"
                )
        if temporary_pins is not None:
            try:
                errors = temporary_pins.close()
                result["temporary_pin_cleanup"] = errors
                if errors:
                    result["passed"] = False
                    result["first_failure"] = result["first_failure"] or repr(errors)
            except Exception as error:  # noqa: BLE001 - continue baseline cleanup
                result["passed"] = False
                result["first_failure"] = result["first_failure"] or repr(error)
        if baseline_pins is not None:
            try:
                final_pins = baseline_pins.finish()
                result["baseline_pin_cleanup"] = final_pins
                if (
                    final_pins["cleanup_errors"]
                    or final_pins["processes"]
                    != baseline_profile["records"]["processes"]
                ):
                    raise RuntimeError("Baseline pre-exit pin cleanup/identity failed")  # noqa: TRY301 - release owned resources and preserve failure
            except Exception as error:  # noqa: BLE001 - preserve first failure while releasing pins
                result["passed"] = False
                result["first_failure"] = result["first_failure"] or repr(error)
            finally:
                try:
                    baseline_pins.close()
                except Exception as error:  # noqa: BLE001 - still close the reference guard
                    result["passed"] = False
                    result["first_failure"] = result["first_failure"] or repr(error)
        if guard is not None:
            try:
                journal.cleanup("reference.pin.close", guard.close)
            except Exception as error:  # noqa: BLE001 - preserve first failure
                result["passed"] = False
                result["first_failure"] = result["first_failure"] or repr(error)
        if time.monotonic() >= journal.deadline:
            result["passed"] = False
            result["first_failure"] = result["first_failure"] or "Case deadline expired"
        result.update(heartbeat_count=window.beats, paint_count=window.paints)
        write(args.output / "result.json", result)

    return 0 if code == 0 and result.get("passed") is True else 1
