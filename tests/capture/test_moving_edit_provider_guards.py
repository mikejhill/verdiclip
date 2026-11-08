"""Adversarial lifecycle and original-clock guards through the moving provider."""

from __future__ import annotations

import gc
import time
from dataclasses import replace
from functools import partial
from typing import Any
from unittest.mock import Mock

import pytest
from tests.capture.test_moving_edit_evidence_timing import instrumented
from tests.capture.test_moving_edit_provider import (
    _accepted,
    _check,
    _evidence,
    _native,
    _native_fixture,
    _observation_failure,
    _started,
)

from verdiclip.capture import edit_scroll_provider as module
from verdiclip.capture.edit_scroll_provider import Captured, NativeBoundary
from verdiclip.capture.moving_edit_frame import discard_traceback
from verdiclip.capture.observe_native import NativeObservation

# ruff: noqa: ANN401 -- deliberately malformed boundary values test runtime admission.


def test_unsafe_boundary_stops_begin_without_native_write() -> None:
    """A retained unsafe latch invalidates provider admission irreversibly."""
    boundary, provider = _started()
    boundary.unsafe_write_reason = "lost native ownership"
    with pytest.raises(ValueError, match="Unsafe native ownership"):
        provider.move(1, _check)
    assert provider.failed
    assert provider.write_uncertain
    assert boundary.writes == []
    provider.fail(_check)
    assert provider.restoration == "prohibited"


def test_duplicate_begin_latches_without_writing() -> None:
    """A second setup cannot restart the same sequence."""
    boundary, provider = _started()
    with pytest.raises(ValueError, match="Duplicate provider begin"):
        provider.begin(_check)
    assert provider.failed
    assert boundary.writes == []


@pytest.mark.parametrize("operation", ["begin", "move", "capture", "accept", "finish"])
def test_cancellation_inside_admission_prevents_operation(operation: str) -> None:
    """Cancellation during the callback is checked again before native dispatch."""
    boundary, provider = _started()
    action = getattr(provider, operation)
    arguments = (
        (provider.cancel,) if operation in ("begin", "finish") else (1, provider.cancel)
    )
    with pytest.raises(ValueError, match="terminal"):
        action(*arguments)
    assert boundary.writes == []
    assert provider.failed
    assert provider.cancelled


@pytest.mark.parametrize("step", [0, 2, True, 1.0])
def test_accept_requires_matching_capture(step: Any) -> None:
    """Acceptance never skips or coerces a transport sequence number."""
    _, provider = _started()
    provider.move(1, _check)
    provider.capture(1, _check)
    with pytest.raises(ValueError, match="Unexpected frame acceptance"):
        provider.accept(step, _check)
    assert provider.accepted == 0
    assert provider.pending_move == 1
    assert provider.failed
    provider.dispose()


def test_finish_rejects_partial_sequence() -> None:
    """An accepted first frame is not a restored or publishable sequence."""
    _, provider = _started()
    _accepted(provider, 1)
    with pytest.raises(ValueError, match="Moving sequence is incomplete"):
        provider.finish(_check)
    assert provider.failed
    assert not provider.finished
    provider.dispose()


@pytest.mark.parametrize(
    ("step", "reason"),
    [(3, "First native"), (4, "Final native"), (5, "Repeated or restored")],
)
def test_capture_rejects_native_displacement_and_repeat_changes(
    monkeypatch: pytest.MonkeyPatch, step: int, reason: str
) -> None:
    """Correct-sized pixels cannot override independent displacement/repeat proof."""
    boundary, provider = _started()
    for previous in range(1, step):
        _accepted(provider, previous)
    provider.move(step, _check)
    capture = boundary.capture

    def changed(expected: int, *, restoring: bool) -> Captured:
        value = capture(expected, restoring=restoring)
        return replace(value, line13_y=value.line13_y + 1)

    monkeypatch.setattr(boundary, "capture", changed)
    with pytest.raises(ValueError, match=reason):
        provider.capture(step, _check)
    assert provider.failed
    assert provider.safe_observation_failed
    assert len(provider.frames) == step - 1
    provider.dispose()


def test_observation_failure_keeps_first_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Duplicate local reports cannot replace first-failure evidence."""
    boundary, provider = _observation_failure(monkeypatch)
    first = provider.failure_ns
    record = Mock()
    monkeypatch.setattr(boundary, "record_failure", record, raising=False)
    provider._record_observation_failure(999)
    assert provider.failure_ns == first
    record.assert_not_called()
    provider.dispose()


@pytest.mark.parametrize("already_cancelled", [False, True])
def test_restore_admission_checks_both_sides_of_callback(
    *,
    already_cancelled: bool,
) -> None:
    """The restore primitive rejects existing and callback-triggered cancellation."""
    boundary, provider = _started()
    check = Mock(side_effect=provider.cancel)
    if already_cancelled:
        provider.cancel()
    with pytest.raises(ValueError, match="Restoration prohibited after cancellation"):
        provider._restore_admit(check)
    assert check.call_count == int(not already_cancelled)
    assert boundary.writes == []


def test_local_restore_requires_full_original_capture_equality(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed restoration write alone cannot verify changed restored bytes."""
    boundary, provider = _observation_failure(monkeypatch)
    original = boundary.capture

    def changed(expected: int, *, restoring: bool) -> Captured:
        return replace(original(expected, restoring=restoring), state=b"changed")

    monkeypatch.setattr(boundary, "capture", changed)
    with pytest.raises(ValueError, match="Failure restoration mismatch"):
        provider.fail(_check)
    assert len(boundary.writes) == 3
    assert provider.restoration == "failed"
    assert provider.failed
    provider.fail(_check)
    assert len(boundary.writes) == 3
    provider.dispose()


@pytest.mark.parametrize(
    ("deadline", "phase"), [(True, None), (0, None), (30, True), (30, 0), (30, 31)]
)
def test_native_constructor_rejects_invalid_initial_deadline(
    monkeypatch: pytest.MonkeyPatch, deadline: Any, phase: Any
) -> None:
    """Reject expanded or coerced deadlines before acquiring native resources."""
    fake, source, slots, _ = _native_fixture(monkeypatch, construct=False)
    with pytest.raises(ValueError, match="Expanded native initial phase deadline"):
        NativeBoundary(
            fake.native.target,
            _evidence(),
            deadline,
            _check,
            slots,
            fake.events.append,
            phase_deadline_ns=phase,
        )
    assert not fake.native._closed
    assert source.closed == 0
    assert slots.reserved_bytes == 0


@pytest.mark.parametrize("fault", ["future", "geometry"])
def test_constructor_rejects_invalid_setup_and_closes_acquired_native(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """A future receipt or changed root cannot open an MSS capture source."""
    fake, source, slots, _ = _native_fixture(monkeypatch, construct=False)
    evidence = _evidence()
    if fault == "future":
        evidence["target_setup"]["completed_monotonic"] = 1.0
    else:
        fake.native._client_rect.return_value = [40, 40, 574, 701]
    with pytest.raises(ValueError, match=r"Future owned setup|Changed fixed root"):
        NativeBoundary(
            fake.native.target,
            evidence,
            30_000_000_000,
            _check,
            slots,
            fake.events.append,
        )
    assert fake.native._closed == (fault == "geometry")
    assert source.closed == 0
    assert slots.reserved_bytes == 0


@pytest.mark.parametrize(
    "method", ["verify_top_setup", "seek", "capture", "_read_pixels"]
)
def test_closed_native_boundary_rejects_use(
    monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    """Closing both resource owners makes every acquisition and write unusable."""
    fake, source, slots, boundary = _native(monkeypatch)
    boundary.close()
    assert boundary.unsafe_write_reason is None
    if method == "seek":
        action = partial(boundary.seek, 0, restoring=False, admit_write=_check)
    elif method == "capture":
        action = partial(boundary.capture, 0, restoring=False)
    else:
        action = getattr(boundary, method)
    with pytest.raises(ValueError, match="Native boundary is closed"):
        action()
    assert fake.writes == []
    assert source.grabs == 0
    assert slots.reserved_bytes == 0


@pytest.mark.parametrize("fault", ["input", "position"])
def test_top_setup_rejects_changed_input_or_nonzero_pose(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """A native-consistent but unauthorized starting state cannot admit writes."""
    fake, source, _, boundary = _native(monkeypatch)
    if fault == "input":
        fake.input_tick += 1
    else:
        fake.first = 3
    with pytest.raises(
        ValueError, match=r"Target input epoch changed|Initial target is not top"
    ):
        boundary.verify_top_setup()
    assert fake.writes == []
    assert source.grabs == 0
    assert (boundary.unsafe_write_reason is not None) == (fault == "input")
    boundary.close()


@pytest.mark.parametrize(
    ("line", "restoring"), [(True, False), (1, False), (3.0, False), (3, 1), (0, True)]
)
def test_native_capture_validates_pose_before_sampling(
    monkeypatch: pytest.MonkeyPatch, line: Any, restoring: Any
) -> None:
    """Only exact frozen positions and the one restore pose reach native reads."""
    _, source, _, boundary = _native(monkeypatch)
    with pytest.raises(ValueError, match="Invalid fixed capture pose"):
        boundary.capture(line, restoring=restoring)
    assert source.grabs == 0
    boundary.close()


def test_changed_native_pose_during_samples_retains_owners(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Consistent new native pose still invalidates paired sample equality."""
    fake, source, slots, boundary = _native(monkeypatch)
    source.after_grab = lambda: setattr(fake, "first", 3)
    with pytest.raises(
        ValueError, match="State changed during fixed frame readiness"
    ) as caught:
        boundary.capture(0, restoring=False)
    assert boundary.safe_observation_failed
    assert source.grabs == 1
    assert boundary.last_acquisition is None
    boundary.close()
    assert slots.reserved_bytes > 0
    discard_traceback(caught.value)
    gc.collect()
    assert slots.reserved_bytes == 0


@pytest.mark.parametrize("fault", ["position", "missing_stamp", "reversed_stamp"])
def test_seek_rejects_inconsistent_readback_or_write_stamps(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """Real completed dispatch needs matching native readback and ordered stamps."""
    fake, _, _, boundary = _native(monkeypatch)

    def end(event: dict[str, Any]) -> None:
        fake.events.append(event)
        if event["event"] == "seek_end":
            if fault == "position":
                fake.first = 8
            elif fault == "missing_stamp":
                fake.native.write_started_ns = None
            else:
                fake.native.write_started_ns = 1

    fake.native.edit_emit = end
    with pytest.raises(
        ValueError, match=r"Completed seek readback|Missing ordered native write"
    ):
        boundary.seek(100 * 3 / 13, restoring=False, admit_write=_check)
    assert len(fake.writes) == 1
    assert boundary.last_move is None
    assert boundary.safe_observation_failed == (fault == "position")
    boundary.close()


@pytest.mark.parametrize("stamps", [[0] * 11, [0] * 12])
def test_acquisition_rejects_incomplete_or_short_transcript(
    monkeypatch: pytest.MonkeyPatch, stamps: list[int]
) -> None:
    """Acquisition metadata cannot attest fewer reads or less than 200ms."""
    _, _, _, boundary = _native(monkeypatch)
    with pytest.raises(
        ValueError, match=r"sampling clock transcript|exact sampling span"
    ):
        boundary._record_acquisition(0, 200_000_000, stamps)
    assert boundary.last_acquisition is None
    boundary.close()


def test_failure_evidence_error_latches_without_replacing_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unavailable failure journal disables writes but preserves prior cause."""
    _, _, boundary, journal = instrumented(monkeypatch)
    first = ValueError("observation first")
    boundary.budget.failure = first
    monkeypatch.setattr(journal, "failure", Mock(side_effect=OSError("journal lost")))
    boundary.record_failure(1_000_000_000, "observation")
    assert boundary.budget.failure is first
    assert boundary.unsafe_write_reason == "Original admission failed"
    boundary.close()


@pytest.mark.parametrize("stage", ["enter", "phase"])
def test_restore_controller_error_precedes_any_write(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    """Failure restore annotation failures permanently invalidate write admission."""
    fake, _, boundary, journal = instrumented(monkeypatch)
    method = "restore_enter" if stage == "enter" else "restore_phase"
    monkeypatch.setattr(
        journal, method, Mock(side_effect=OSError("restore journal lost"))
    )
    with pytest.raises(OSError, match="restore journal lost"):
        boundary.begin_failure_restore(1_000_000_000)
    assert boundary.unsafe_write_reason == "Original admission failed"
    assert fake.writes == []
    boundary.close()


def test_restore_original_failure_age_cannot_get_new_reserve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Eight seconds since failure expires even with ample session time left."""
    fake, _, _, boundary = _native(monkeypatch)
    fake.now = 9
    with pytest.raises(TimeoutError, match="Original failure restoration expired"):
        boundary.begin_failure_restore(1_000_000_000)
    assert fake.writes == []
    boundary.close()


@pytest.mark.parametrize("entered", [False, True])
def test_restore_phase_requires_existing_ceiling_and_exact_order(
    monkeypatch: pytest.MonkeyPatch, *, entered: bool
) -> None:
    """Missing entry or a repeated guard cannot restart a restore subphase."""
    fake, _, _, boundary = _native(monkeypatch)
    if entered:
        boundary.begin_failure_restore(0)
    with pytest.raises(ValueError, match="Invalid failure restoration subphase"):
        boundary._restore_phase(1, "guard", 2_000_000_000)
    with pytest.raises(ValueError, match="Incomplete failure restoration subphases"):
        boundary.complete_failure_restore()
    assert fake.writes == []
    boundary.close()


@pytest.mark.parametrize("operation", ["open", "grab"])
def test_mss_three_second_call_bound_is_not_full_session_deadline(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """A completed source allocation/read still fails its tighter native bound."""
    fake, source, slots, boundary = _native_fixture(
        monkeypatch, construct=operation == "grab"
    )
    if operation == "open":

        def slow_open(*_args: Any, **_kwargs: Any) -> Any:
            fake.now = 3
            return source

        monkeypatch.setattr(module.mss, "MSS", slow_open)
        with pytest.raises(TimeoutError, match="MSS acquisition deadline"):
            NativeBoundary(
                fake.native.target,
                _evidence(),
                30_000_000_000,
                _check,
                slots,
                fake.events.append,
            )
        assert source.closed == 1
        assert fake.native._closed
    else:
        assert boundary is not None
        source.after_grab = lambda: setattr(fake, "now", 3)
        with pytest.raises(TimeoutError, match="MSS grab deadline") as caught:
            boundary._read_pixels()
        assert source.grabs == 1
        boundary.close()
        discard_traceback(caught.value)
    gc.collect()
    assert slots.reserved_bytes == 0


def test_mss_start_evidence_delay_prevents_grab(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A delayed logging ACK cannot dispatch a read past its original call bound."""
    fake, source, _, boundary = _native(monkeypatch)

    def delay(event: dict[str, Any]) -> None:
        if event["event"] == "call_start" and event["name"] == "mss.grab":
            fake.now = 3

    boundary.emit = delay
    with pytest.raises(RuntimeError, match="Native start logging deadline"):
        boundary._read_pixels()
    assert source.grabs == 0
    boundary.close()


def test_mss_original_failure_survives_end_evidence_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read errors retain primacy when their paired end record also fails."""
    _, source, _, boundary = _native(monkeypatch)
    first = OSError("grab first")
    monkeypatch.setattr(source, "grab", Mock(side_effect=first))

    def lose_end(event: dict[str, Any]) -> None:
        if event["event"] == "call_end":
            raise ValueError("end lost")

    boundary.emit = lose_end
    with pytest.raises(OSError, match="grab first") as caught:
        boundary._read_pixels()
    assert caught.value is first
    assert "Native evidence failed: ValueError" in first.__notes__
    assert boundary.unsafe_write_reason == "Original admission failed"
    boundary.emit = lambda _event: None
    boundary.close()
    discard_traceback(first)


def test_cleanup_timestamp_failure_still_releases_every_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even missing original clock output cannot skip owned native cleanup."""
    fake, source, slots, boundary = _native(monkeypatch)
    with monkeypatch.context() as patch:
        patch.setattr(time, "monotonic_ns", Mock(side_effect=OSError("clock lost")))
        with pytest.raises(OSError, match="clock lost") as caught:
            boundary.close()
    assert source.closed == 1
    assert fake.native._closed
    discard_traceback(caught.value)
    gc.collect()
    assert slots.reserved_bytes == 0


@pytest.mark.parametrize("moment", ["start_ack", "admission", "native_dispatch"])
def test_native_dispatch_uses_original_tighter_bound_at_each_handoff(
    monkeypatch: pytest.MonkeyPatch, moment: str
) -> None:
    """Slow ACK, write admission and native handoff are checked before the ABI."""
    fake, _, _, boundary = _native(monkeypatch)
    dispatch = Mock(return_value=123)
    if moment == "start_ack":
        boundary.emit = lambda event: (
            setattr(fake, "now", 3) if event["event"] == "call_start" else None
        )
    elif moment == "admission":
        fake.native._write_admission = lambda: setattr(fake, "now", 3)
    else:
        original = NativeObservation.raw

        def delayed(native: Any, function: Any, *args: Any) -> Any:
            fake.now = 3
            return original(native, function, *args)

        monkeypatch.setattr(NativeObservation, "raw", delayed)
    with pytest.raises(RuntimeError, match=r"Native (start logging|dispatch) deadline"):
        fake.native.raw(dispatch)
    dispatch.assert_not_called()
    boundary.emit = lambda _event: None
    boundary.close()


def test_reentrant_evidence_cannot_remove_write_admission_silently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A callback that revokes a pending admission causes failure before dispatch."""
    fake, _, _, boundary = _native(monkeypatch)
    admission = Mock()
    fake.native._write_admission = admission

    def revoke(event: dict[str, Any]) -> None:
        if event["event"] == "call_start":
            fake.native._write_admission = None

    boundary.emit = revoke
    dispatch = Mock()
    with pytest.raises(ValueError, match="Missing native write admission"):
        fake.native.raw(dispatch)
    admission.assert_not_called()
    dispatch.assert_not_called()
    boundary.close()


def test_native_primary_exception_keeps_failed_end_annotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed ABI dispatch stays primary despite a second evidence exception."""
    fake, _, _, boundary = _native(monkeypatch)
    first = OSError("native dispatch first")

    def lose_end(event: dict[str, Any]) -> None:
        if event["event"] == "call_end":
            raise ValueError("end lost")

    boundary.emit = lose_end
    with pytest.raises(OSError, match="native dispatch first") as caught:
        fake.native.raw(Mock(side_effect=first))
    assert caught.value is first
    assert "Native evidence failed: ValueError" in first.__notes__
    boundary.emit = lambda _event: None
    boundary.close()
    discard_traceback(first)
