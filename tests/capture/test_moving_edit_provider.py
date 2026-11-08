"""Incremental native writes, exact sampling and exception-safe pixel ownership."""

from __future__ import annotations

import gc
import json
import time
from collections.abc import Callable
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from tests.capture.moving_wire_fixtures import make_begin
from tests.capture.test_moving_edit_native_extraction import Harness

from verdiclip.capture import edit_scroll_provider as module
from verdiclip.capture.edit_scroll_provider import (
    Captured,
    MovingProvider,
    NativeBoundary,
)
from verdiclip.capture.moving_edit_frame import PeerPixelSlots, discard_traceback
from verdiclip.capture.scroll_stitch import ScrollFrame

# ruff: noqa: ANN401 -- fake native and JSON interfaces deliberately retain native types.

ROOT_BYTES = 533 * 661 * 3
CLIENT_BYTES = 493 * 413 * 3
BGRA_BYTES = 533 * 661 * 4


def _check() -> None:
    pass


class Boundary:
    """Small in-memory exact-row boundary, without native resource behavior."""

    unsafe_write_reason: str | None = None

    def __init__(self) -> None:
        self.pose = 0
        self.writes: list[tuple[float, bool]] = []
        self.uncertain = False
        self.rows = [bytes([i % 256, i // 256, 71]) * 493 for i in range(621)]

    def verify_top_setup(self) -> None:
        """Require exact initial top."""
        assert self.pose == 0

    def seek(
        self, percent: float, *, restoring: bool, admit_write: Callable[[], None]
    ) -> None:
        """Write only the freshly admitted requested pose."""
        admit_write()
        self.writes.append((percent, restoring))
        if self.uncertain:
            self.unsafe_write_reason = "unknown write"
            raise TimeoutError("unknown write")
        self.pose = round(percent * 13 / 100)

    def capture(self, expected_line: int, *, restoring: bool) -> Captured:
        """Return full source rows and root, including restoration repeats."""
        assert self.pose == expected_line
        assert not restoring or expected_line == 3
        rgb = b"".join(self.rows[self.pose * 16 : self.pose * 16 + 413])
        return Captured(
            ScrollFrame(493, 413, rgb),
            bytes(ROOT_BYTES),
            str(self.pose).encode(),
            self.pose,
            (13 - self.pose) * 16,
        )


def _started() -> tuple[Boundary, MovingProvider]:
    boundary = Boundary()
    provider = MovingProvider(boundary)
    provider.begin(_check)
    return boundary, provider


def _accepted(provider: MovingProvider, step: int) -> Captured:
    provider.move(step, _check)
    frame = provider.capture(step, _check)
    provider.accept(step, _check)
    return frame


def test_exact_seven_steps_accept_before_next_move() -> None:
    """Retain all seven sources and use only 2/3/4 for every output row."""
    boundary, provider = _started()
    for step in range(1, 8):
        _accepted(provider, step)
    result = provider.finish(_check)
    assert result.image.rgb == b"".join(boundary.rows)
    assert len(result.frames) == 7
    assert len(result.provenance) == 621
    assert len(boundary.writes) == 7
    assert boundary.writes[-1][1] is True
    assert {source for source, _row in result.provenance} == {2, 3, 4}
    stride = 493 * 3
    for output, (source, row) in enumerate(result.provenance):
        assert (
            result.image.rgb[output * stride : (output + 1) * stride]
            == (result.frames[source - 1].frame.rgb[row * stride : (row + 1) * stride])
        )
    provider.dispose()
    assert provider.slots.reserved_bytes == 7 * (ROOT_BYTES + CLIENT_BYTES) + 918459
    del result
    assert provider.slots.reserved_bytes == 0


def test_no_move_before_acceptance() -> None:
    """A completed capture still needs matching transport acceptance."""
    boundary, provider = _started()
    provider.move(1, _check)
    provider.capture(1, _check)
    with pytest.raises(ValueError, match="Unexpected moving step"):
        provider.move(2, _check)
    assert len(boundary.writes) == 1
    assert provider.failed
    provider.fail(_check)
    assert len(boundary.writes) == 1


def test_uncertain_write_permanently_prohibits_restoration() -> None:
    """A timeout cannot be converted to a restoration write."""
    boundary, provider = _started()
    boundary.uncertain = True
    with pytest.raises(TimeoutError, match="unknown write"):
        provider.move(1, _check)
    provider.fail(_check)
    assert len(boundary.writes) == 1
    assert provider.restoration == "prohibited"


def test_cancel_does_not_restore_or_publish() -> None:
    """Immediate public cancel stays irreversible and unrestored."""
    boundary, provider = _started()
    _accepted(provider, 1)
    provider.move(2, _check)
    provider.cancel()
    provider.fail(_check)
    assert len(boundary.writes) == 2
    assert boundary.pose == 0
    with pytest.raises(ValueError, match="terminal"):
        provider.finish(_check)


def test_capture_alias_retains_pixel_reservation_after_provider_disposal() -> None:
    """Dropping the provider never frees capacity owned by returned source aliases."""
    _, provider = _started()
    frame = _accepted(provider, 1)
    assert provider.slots.reserved_bytes == ROOT_BYTES + CLIENT_BYTES
    provider.dispose()
    assert provider.slots.reserved_bytes > 0
    del frame
    assert provider.slots.reserved_bytes == 0


@pytest.mark.parametrize("error_type", [TimeoutError, ValueError])
def test_expired_move_admission_never_writes(error_type: type[Exception]) -> None:
    """Even a ValueError admission failure is not an observational safe restore."""
    boundary, provider = _started()
    _accepted(provider, 1)

    def expired() -> None:
        raise error_type("original deadline")

    with pytest.raises(error_type, match="original deadline"):
        provider.move(2, expired)
    provider.fail(_check)
    assert len(boundary.writes) == 1
    assert provider.failed


@pytest.mark.parametrize("step", [0, 2, True, 1.0])
def test_invalid_capture_cannot_trigger_restoration(step: Any) -> None:
    """Protocol errors are not provider-local observational errors."""
    boundary, provider = _started()
    _accepted(provider, 1)
    with pytest.raises(ValueError, match="Unexpected moving capture"):
        provider.capture(step, _check)
    provider.fail(_check)
    assert len(boundary.writes) == 1


def _observation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Boundary, MovingProvider]:
    boundary, provider = _started()
    _accepted(provider, 1)
    provider.move(2, _check)
    original = boundary.capture

    def capture(expected_line: int, *, restoring: bool) -> Captured:
        value = original(expected_line, restoring=restoring)
        return value if restoring else replace(value, first_line=8)

    monkeypatch.setattr(boundary, "capture", capture)
    with pytest.raises(ValueError, match="Changed fixed capture"):
        provider.capture(2, _check)
    return boundary, provider


def test_local_observation_failure_restores_once_and_remains_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed write and healthy fresh guards permit only one local restore."""
    boundary, provider = _observation_failure(monkeypatch)
    provider.fail(_check)
    assert boundary.pose == 3
    assert provider.restoration == "verified"
    assert provider.failed
    provider.fail(_check)
    assert len(boundary.writes) == 3
    with pytest.raises(ValueError, match="terminal"):
        provider.finish(_check)


@pytest.mark.parametrize("failure", ["input", "cancel", "reserve"])
def test_local_observation_restore_requires_fresh_original_safety(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Lost input, cancellation or elapsed original reserve forbids a restore."""
    boundary, provider = _observation_failure(monkeypatch)
    if failure == "input":
        boundary.unsafe_write_reason = "input changed"
    elif failure == "cancel":
        provider.cancel()

    def check() -> None:
        if failure == "reserve":
            raise TimeoutError("original reserve")

    if failure == "reserve":
        with pytest.raises(TimeoutError, match="original reserve"):
            provider.fail(check)
    else:
        provider.fail(check)
    assert len(boundary.writes) == 2
    assert provider.restoration == "prohibited"


class CaptureSource:
    """MSS-compatible fixed complete root with exact client content."""

    def __init__(self, native: Harness) -> None:
        self.native = native
        self.closed = 0
        self.grabs = 0
        self.after_grab: Callable[[], None] = _check

    def grab(self, region: dict[str, int]) -> Any:
        """Generate a native-sized root and crop with stable distinctive rows."""
        assert region == {"left": 40, "top": 40, "width": 533, "height": 661}
        self.grabs += 1
        rgb = bytearray([7]) * ROOT_BYTES
        for row in range(413):
            content_row = self.native.first * 16 + row
            offset = ((row + 20) * 533 + 20) * 3
            rgb[offset : offset + CLIENT_BYTES // 413] = (
                bytes([content_row % 256, content_row // 256, 71]) * 493
            )
        shot = SimpleNamespace(
            rgb=bytes(rgb), raw=bytes(BGRA_BYTES), width=533, height=661
        )
        self.after_grab()
        return shot

    def close(self) -> None:
        """Expose exact cleanup count."""
        self.closed += 1


def _evidence() -> dict[str, Any]:
    evidence = make_begin()["profile_evidence"]
    evidence["target_setup"]["completed_monotonic"] = 0.0
    return evidence


def _native_fixture(
    monkeypatch: pytest.MonkeyPatch,
    *,
    emit: Callable[[dict[str, Any]], None] | None = None,
    check: Callable[[], object] = _check,
    construct: bool = True,
) -> tuple[Harness, CaptureSource, PeerPixelSlots, NativeBoundary | None]:
    fake = Harness(monkeypatch)
    monkeypatch.setattr(time, "monotonic_ns", lambda: round(fake.now * 1e9))
    monkeypatch.setattr(
        time,
        "sleep",
        lambda seconds: setattr(fake, "now", fake.now + seconds + 0.000001),
    )
    native = fake.native
    native.__class__ = module._TimedScrollObservation
    source = CaptureSource(fake)
    slots = PeerPixelSlots()

    def create_native(_target: Any, _child: int, budget: Any, **_kwargs: Any) -> Any:
        native.budget = budget
        native.edit_emit = budget.emit
        return native

    monkeypatch.setattr(module, "_TimedScrollObservation", create_native)
    monkeypatch.setattr(module.mss, "MSS", Mock(return_value=source))
    boundary = None
    if construct:
        boundary = NativeBoundary(
            native.target,
            _evidence(),
            30_000_000_000,
            check,
            slots,
            fake.events.append if emit is None else emit,
        )
    return fake, source, slots, boundary


def _native(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Harness, CaptureSource, PeerPixelSlots, NativeBoundary]:
    fake, source, slots, boundary = _native_fixture(monkeypatch)
    assert boundary is not None
    return fake, source, slots, boundary


def test_actual_native_capture_keeps_root_client_and_sampling_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise constructor, paired snapshots, all samples, crop and leases."""
    _, source, slots, boundary = _native(monkeypatch)
    boundary.verify_top_setup()
    result = boundary.capture(0, restoring=False)
    assert len(result.root_rgb) == ROOT_BYTES
    assert len(result.frame.rgb) == CLIENT_BYTES
    assert json.loads(result.state)["native"]["vertical_bar"] == [553, 60, 570, 473]
    assert source.grabs == 3
    assert boundary.last_acquisition == {
        "started_ns": 0,
        "finished_ns": 200_002_000,
        "samples": 3,
        "quiet_ns": 200_002_000,
    }
    boundary.close()
    assert source.closed == 1
    assert slots.reserved_bytes == ROOT_BYTES + CLIENT_BYTES
    del result
    assert slots.reserved_bytes == 0


def test_actual_native_move_stamps_dispatch_not_slow_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Write duration excludes logger time but whole-move readback includes it."""
    fake, _, _, boundary = _native(monkeypatch)
    boundary.verify_top_setup()
    fake.seek_duration = 0.25

    def emit(event: dict[str, Any]) -> None:
        fake.events.append(event)
        if event["event"] == "seek_end":
            fake.now += 0.1

    fake.native.edit_emit = emit
    boundary.seek(100 * 3 / 13, restoring=False, admit_write=_check)
    evidence = boundary.last_move
    assert evidence is not None
    assert evidence["write_started_ns"] == 0
    assert evidence["write_returned_ns"] == 250_000_000
    assert evidence["readback_ns"] == evidence["completed_ns"] == 350_000_000
    assert evidence["native_state"]["text_layout"]["first_visible_line"] == 3
    boundary.close()


def test_actual_native_seven_frames_transfer_temporary_owners(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full extracted native sequence frees only transient slots after each handoff."""
    fake, source, slots, boundary = _native(monkeypatch)
    provider = MovingProvider(boundary, slots)
    provider.begin(_check)
    for step in range(1, 8):
        _accepted(provider, step)
        assert slots.reserved_bytes == 2 * BGRA_BYTES + step * (
            ROOT_BYTES + CLIENT_BYTES
        )
    result = provider.finish(_check)
    assert len(fake.writes) == 7
    assert source.grabs == 21
    assert provider.restoration == "verified"
    provider.dispose()
    boundary.close()
    assert slots.reserved_bytes == 7 * (ROOT_BYTES + CLIENT_BYTES) + 918459
    del result
    assert slots.reserved_bytes == 0


@pytest.mark.parametrize("field", ["focus", "input_tick", "style", "position"])
def test_native_paired_samples_reject_changed_guard(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    """A change immediately after grab is observed before another sample."""
    fake, source, slots, boundary = _native(monkeypatch)
    boundary.verify_top_setup()
    source.after_grab = lambda: setattr(fake, field, 999)
    with pytest.raises((ValueError, RuntimeError)) as caught:
        boundary.capture(0, restoring=False)
    assert source.grabs == 1
    assert boundary.last_acquisition is None
    assert slots.reserved_bytes > 2 * BGRA_BYTES
    boundary.close()
    assert slots.reserved_bytes > 0
    discard_traceback(caught.value)
    gc.collect()
    assert slots.reserved_bytes == 0


def test_sampling_failure_aliases_prevent_restoration_reuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retained failed sample stack cannot lose or reuse its owned capacity."""
    _, source, slots, boundary = _native(monkeypatch)
    provider = MovingProvider(boundary, slots)
    provider.begin(_check)
    _accepted(provider, 1)
    provider.move(2, _check)
    original = source.grab

    def changed(region: dict[str, int]) -> Any:
        shot = original(region)
        shot.rgb = bytes([source.grabs]) * ROOT_BYTES
        return shot

    monkeypatch.setattr(source, "grab", changed)
    with pytest.raises(ValueError, match="Readiness sample changed") as caught:
        provider.capture(2, _check)
    before = slots.reserved_bytes
    provider.fail(_check)
    assert provider.restoration == "prohibited"
    assert slots.reserved_bytes == before
    provider.dispose()
    boundary.close()
    assert slots.reserved_bytes > 0
    discard_traceback(caught.value)
    gc.collect()
    assert slots.reserved_bytes == 0


@pytest.mark.parametrize("fault", ["clock", "logger", "mss", "native"])
def test_close_attempts_every_resource_despite_first_failure(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """No clock, logger or close failure can skip another acquired resource."""
    fake, source, slots, boundary = _native(monkeypatch)
    close_native = Mock(wraps=fake.native.close)
    monkeypatch.setattr(fake.native, "close", close_native)
    error = RuntimeError(f"first {fault}")
    if fault == "clock":
        boundary._check_clock = Mock(side_effect=error)
    elif fault == "logger":
        boundary.emit = Mock(side_effect=error)
    elif fault == "mss":
        monkeypatch.setattr(source, "close", Mock(side_effect=error))
    else:
        close_native.side_effect = error
    with pytest.raises(RuntimeError, match=f"first {fault}") as caught:
        boundary.close()
    assert caught.value is error
    close_native.assert_called_once()
    if fault == "mss":
        assert isinstance(source.close, Mock)
        source.close.assert_called_once()
    else:
        assert source.closed == 1
    discard_traceback(caught.value)
    gc.collect()
    assert slots.reserved_bytes == 0
    boundary.close()
    close_native.assert_called_once()


def test_constructor_log_failure_preserves_acquired_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An MSS post-return logging error preserves the original and closes both."""
    fake, source, slots, _ = _native_fixture(monkeypatch, construct=False)
    primary = ValueError("mss post-return")

    def emit(event: dict[str, Any]) -> None:
        if event["event"] == "call_end":
            raise primary

    with pytest.raises(ValueError, match="mss post-return") as caught:
        NativeBoundary(
            fake.native.target,
            _evidence(),
            30_000_000_000,
            _check,
            slots,
            emit,
        )
    assert caught.value is primary
    assert source.closed == 1
    assert fake.native._closed
    discard_traceback(caught.value)
    gc.collect()
    assert slots.reserved_bytes == 0


def test_constructor_original_failure_survives_broken_cleanup_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cleanup clock fault cannot replace the constructor's first failure."""
    fake, source, _, _ = _native_fixture(monkeypatch, construct=False)
    original_clock = time.monotonic
    fail_clock = False
    primary = ValueError("constructor first")

    def clock() -> float:
        if fail_clock:
            raise RuntimeError("cleanup clock")
        return original_clock()

    def emit(event: dict[str, Any]) -> None:
        nonlocal fail_clock
        if event["event"] == "call_end":
            fail_clock = True
            raise primary

    monkeypatch.setattr(time, "monotonic", clock)
    with pytest.raises(ValueError, match="constructor first") as caught:
        NativeBoundary(
            fake.native.target,
            _evidence(),
            30_000_000_000,
            _check,
            PeerPixelSlots(),
            emit,
        )
    assert caught.value is primary
    assert source.closed == 1
    assert fake.native._closed
    assert "Native cleanup failed: RuntimeError" in caught.value.__notes__
    discard_traceback(caught.value)


def test_native_phase_deadline_can_increase_only_within_original_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new frame phase never creates another thirty-second session."""
    _, _, _, boundary = _native(monkeypatch)
    boundary.set_deadline(2_000_000_000)
    boundary.set_deadline(3_000_000_000)
    with pytest.raises(ValueError, match="Expanded"):
        boundary.set_deadline(30_000_000_001)
    boundary.close()


def test_duplicate_capture_cannot_restore(monkeypatch: pytest.MonkeyPatch) -> None:
    """Matching pending step alone does not authorize a second capture or restore."""
    boundary, provider = _started()
    _accepted(provider, 1)
    provider.move(2, _check)
    provider.capture(2, _check)
    capture = Mock(wraps=boundary.capture)
    monkeypatch.setattr(boundary, "capture", capture)
    with pytest.raises(ValueError, match="Unexpected moving capture"):
        provider.capture(2, _check)
    provider.fail(_check)
    capture.assert_not_called()
    assert len(boundary.writes) == 2
    assert not provider.safe_observation_failed


@pytest.mark.parametrize("slot", ["source_root_2", "source_client_2"])
def test_source_slot_failure_never_starts_capture_or_restore(slot: str) -> None:
    """Reservations precede allocation and failures cannot become safe restores."""
    boundary, provider = _started()
    _accepted(provider, 1)
    provider.move(2, _check)
    occupied = provider.slots.reserve(slot)
    with pytest.raises(ValueError, match="slot unavailable") as caught:
        provider.capture(2, _check)
    provider.fail(_check)
    assert len(boundary.writes) == 2
    provider.dispose()
    discard_traceback(caught.value)
    del occupied
    gc.collect()
    assert provider.slots.reserved_bytes == 0


@pytest.mark.parametrize(
    "slot",
    ["sample_root_1", "sample_root_2", "sample_root_3", "crop_temp_1", "crop_temp_2"],
)
def test_every_sampling_slot_failure_precedes_pixel_read(
    monkeypatch: pytest.MonkeyPatch, slot: str
) -> None:
    """A partial reservation failure keeps existing aliases and allocates no image."""
    _, source, slots, boundary = _native(monkeypatch)
    boundary.verify_top_setup()
    occupied = slots.reserve(slot)
    with pytest.raises(ValueError, match="slot unavailable") as caught:
        boundary.capture(0, restoring=False)
    assert source.grabs == 0
    assert slot in slots.active_slots
    boundary.close()
    discard_traceback(caught.value)
    del occupied
    gc.collect()
    assert slots.reserved_bytes == 0


@pytest.mark.parametrize("where", ["admission", "native_call", "readback", "sampling"])
def test_original_deadline_failure_never_allows_later_write(
    monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    """Every native phase uses the same permanent admission and phase cutoff."""
    fake, source, _, boundary = _native(monkeypatch)
    provider = MovingProvider(boundary, boundary.slots)
    provider.begin(_check)
    _accepted(provider, 1)
    boundary.set_deadline(round((fake.now + 0.2) * 1e9))
    if where == "admission":
        fake.now += 0.2
    elif where == "native_call":
        fake.seek_duration = 0.2
    elif where == "readback":

        def slow_readback(event: dict[str, Any]) -> None:
            if event["event"] == "seek_end":
                fake.now += 0.2

        fake.native.edit_emit = slow_readback
    else:
        source.after_grab = lambda: setattr(fake, "now", fake.now + 0.2)

    def operation() -> None:
        provider.move(2, _check)
        provider.capture(2, _check)

    with pytest.raises(RuntimeError, match="deadline"):
        operation()
    count = len(fake.writes)
    provider.fail(_check)
    assert len(fake.writes) == count
    assert provider.restoration == "prohibited"
    assert boundary.unsafe_write_reason is not None
    boundary.close()


def test_value_error_from_original_check_is_not_safe_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An arbitrary admission exception while sampling cannot authorize a write."""
    fake, source, _, boundary = _native(monkeypatch)
    provider = MovingProvider(boundary, boundary.slots)
    provider.begin(_check)
    _accepted(provider, 1)
    provider.move(2, _check)

    def deny() -> None:
        raise ValueError("cancelled admission")

    source.after_grab = lambda: setattr(boundary, "check", deny)
    with pytest.raises(ValueError, match="cancelled admission"):
        provider.capture(2, _check)
    provider.fail(_check)
    assert len(fake.writes) == 2
    assert provider.restoration == "prohibited"
    assert not provider.safe_observation_failed
    boundary.close()


def test_failed_native_readback_is_known_completed_but_no_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fully read state mismatch can restore once, never masquerade as success."""
    fake, _, slots, boundary = _native(monkeypatch)
    provider = MovingProvider(boundary, slots)
    provider.begin(_check)
    _accepted(provider, 1)

    def mismatch(event: dict[str, Any]) -> None:
        if event["event"] == "seek_end":
            fake.position = 3

    fake.native.edit_emit = mismatch
    with pytest.raises(ValueError, match="disagreement"):
        provider.move(2, _check)
    assert not provider.write_uncertain
    assert provider.safe_observation_failed
    fake.native.edit_emit = fake.events.append
    fake.position = None
    provider.fail(_check)
    assert provider.restoration == "verified"
    assert provider.failed
    assert len(fake.writes) == 3
    boundary.close()


def test_boundary_and_provider_cannot_use_different_pixel_pools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One peer reservation covers native, retained and assembly allocations."""
    _, _, _, boundary = _native(monkeypatch)
    with pytest.raises(ValueError, match="different pixel allocation domain"):
        MovingProvider(boundary, PeerPixelSlots())
    boundary.close()


@pytest.mark.parametrize(
    "slot", ["crop_temp_1", "crop_temp_2", "assembly_chunks", "assembly_final"]
)
def test_assembly_slot_failure_never_returns_output(slot: str) -> None:
    """Each assembly allocation reserves first and preserves held failure aliases."""
    _, provider = _started()
    for step in range(1, 8):
        _accepted(provider, step)
    occupied = provider.slots.reserve(slot)
    with pytest.raises(ValueError, match="slot unavailable") as caught:
        provider.finish(_check)
    assert provider.failed
    assert not provider.finished
    provider.dispose()
    assert slot in provider.slots.active_slots
    discard_traceback(caught.value)
    del occupied
    gc.collect()
    assert provider.slots.reserved_bytes == 0


@pytest.mark.parametrize("slot", ["sample_bgra_1", "sample_bgra_2"])
def test_constructor_slot_failure_closes_native_without_opening_mss(
    monkeypatch: pytest.MonkeyPatch, slot: str
) -> None:
    """Partial native setup retains its reservations and closes the acquired owner."""
    fake, source, slots, _ = _native_fixture(monkeypatch, construct=False)
    occupied = slots.reserve(slot)
    with pytest.raises(ValueError, match="slot unavailable") as caught:
        NativeBoundary(
            fake.native.target,
            _evidence(),
            30_000_000_000,
            _check,
            slots,
            fake.events.append,
        )
    assert source.closed == 0
    assert fake.native._closed
    assert slot in slots.active_slots
    discard_traceback(caught.value)
    del occupied
    gc.collect()
    assert slots.reserved_bytes == 0


@pytest.mark.parametrize("phase", ["begin", "move", "capture", "accept", "finish"])
def test_cancel_during_admission_never_commits(phase: str) -> None:
    """Cancellation remains irreversible even if an admission callback returns."""
    boundary = Boundary()
    provider = MovingProvider(boundary)
    if phase != "begin":
        provider.begin(_check)
    if phase in ("capture", "accept"):
        provider.move(1, _check)
    if phase == "accept":
        provider.capture(1, _check)
    if phase == "finish":
        for step in range(1, 8):
            _accepted(provider, step)
    actions = {
        "begin": lambda: provider.begin(provider.cancel),
        "move": lambda: provider.move(1, provider.cancel),
        "capture": lambda: provider.capture(1, provider.cancel),
        "accept": lambda: provider.accept(1, provider.cancel),
        "finish": lambda: provider.finish(provider.cancel),
    }
    before = len(boundary.writes)
    with pytest.raises(ValueError, match="terminal"):
        actions[phase]()
    provider.fail(_check)
    assert len(boundary.writes) == before
    assert provider.restoration == "prohibited"
    assert not provider.finished


def test_cancel_at_exact_write_admission_never_dispatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The final native admission cannot ignore cancellation during its callback."""
    fake, _, slots, boundary = _native(monkeypatch)
    provider = MovingProvider(boundary, slots)
    provider.begin(_check)
    calls = 0

    def check() -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            provider.cancel()

    with pytest.raises(ValueError, match="terminal"):
        provider.move(1, check)
    assert not fake.writes
    boundary.close()


def test_cancel_at_restore_admission_never_dispatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The local failure-only exception cannot override an immediate cancel."""
    boundary, provider = _observation_failure(monkeypatch)
    with pytest.raises(ValueError, match="prohibited after cancellation"):
        provider.fail(provider.cancel)
    assert len(boundary.writes) == 2
    assert provider.restoration == "prohibited"


def test_slow_cleanup_logger_counts_toward_call_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cleanup logging is inside the existing per-call bound, not extra time."""
    fake, source, _, boundary = _native(monkeypatch)

    def slow_log(event: dict[str, Any]) -> None:
        if event["event"] == "call_end":
            fake.now += 3

    boundary.emit = slow_log
    with pytest.raises(TimeoutError, match="cleanup call deadline"):
        boundary.close()
    assert source.closed == 1
    assert fake.native._closed


def test_sampling_slot_exception_never_confers_restore_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even a cleared allocation traceback cannot become an observational restore."""
    fake, _, slots, boundary = _native(monkeypatch)
    provider = MovingProvider(boundary, slots)
    provider.begin(_check)
    _accepted(provider, 1)
    provider.move(2, _check)
    reserve = slots.reserve

    def fail_sample(name: str, *, size: int | None = None) -> Any:
        if name == "sample_root_1":
            raise ValueError("injected allocation failure")
        return reserve(name, size=size)

    monkeypatch.setattr(slots, "reserve", fail_sample)
    with pytest.raises(ValueError, match="injected allocation failure") as caught:
        provider.capture(2, _check)
    discard_traceback(caught.value)
    monkeypatch.setattr(slots, "reserve", reserve)
    assert not provider.safe_observation_failed
    provider.fail(_check)
    assert len(fake.writes) == 2
    boundary.close()
