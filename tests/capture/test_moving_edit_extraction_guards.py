"""Bounded extraction failure evidence, native ownership and clock admission."""

from __future__ import annotations

import ctypes as c
import json
from typing import Any, cast
from unittest.mock import Mock

import pytest
from tests.capture.test_moving_edit_native_extraction import Harness
from tests.capture.test_moving_edit_scroll_contract_extraction import (
    FONT_OWNER,
    focused_receipt,
)

from verdiclip.capture import edit_scroll_native as native_module
from verdiclip.capture.edit_geometry import Budget
from verdiclip.capture.edit_samples import crop, stable_samples
from verdiclip.capture.edit_scroll_contract import (
    SweepClock,
    assemble,
    finite,
    phase_deadline,
    validate_target_setup,
)
from verdiclip.capture.edit_scroll_native import retain_native_diagnostic
from verdiclip.capture.observe_native import Rect
from verdiclip.capture.scroll_stitch import ScrollFrame

# ruff: noqa: ANN401 -- ABI outputs and malformed native evidence remain dynamic.


def test_sampling_deadline_precedes_first_read() -> None:
    """The sampler never starts even its first read at the original deadline."""
    read, check, sleep = Mock(), Mock(), Mock()
    with pytest.raises(RuntimeError, match="Capture readiness deadline"):
        stable_samples(read, check, 2, lambda: 2.0, sleep)
    read.assert_not_called()
    sleep.assert_not_called()
    check.assert_called_once()


@pytest.mark.parametrize("size", [89, 91])
def test_crop_rejects_inexact_root_storage(size: int) -> None:
    """Valid containment cannot admit shorter or longer source storage."""
    with pytest.raises(ValueError, match="Root RGB length mismatch"):
        crop(bytes(size), [10, 20, 16, 25], [10, 21, 13, 24])


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), "1", None])
def test_contract_clock_rejects_coercion_and_nonfinite(value: Any) -> None:
    """Only exact finite numeric inputs may participate in phase arithmetic."""
    with pytest.raises(ValueError, match="Invalid clock"):
        finite(value)


@pytest.mark.parametrize(
    "clock", [(1.0, 0.0, 2.0, 3.0), (0.0, 1.0, 0.0, 3.0), (0.0, 3.0, 2.0, 3.0)]
)
def test_phase_clock_rejects_backward_empty_or_expired_budget(
    clock: tuple[float, float, float, float],
) -> None:
    """Entry cannot precede launch, borrow time or open an empty phase."""
    with pytest.raises(ValueError, match="Invalid phase clock"):
        phase_deadline(*clock)


@pytest.mark.parametrize("deadline", [0, 31])
def test_sweep_rejects_invalid_original_ceiling(deadline: int) -> None:
    """A sweep begins with one bounded positive original deadline."""
    with pytest.raises(ValueError, match="Invalid sweep clock"):
        SweepClock(deadline, lambda: 0.0)


def test_sweep_first_prohibition_is_immutable() -> None:
    """A later symptom cannot replace the reason restoration became unsafe."""
    clock = SweepClock(30, lambda: 0.0)
    clock.prohibit("first")
    clock.prohibit("second")
    assert clock.prohibited == "first"
    with pytest.raises(ValueError, match="first"):
        clock.check()


@pytest.mark.parametrize("fault", ["second_attempt", "short_reserve", "flag"])
def test_sweep_restoration_has_one_unborrowed_reserve(fault: str) -> None:
    """A duplicate entry, late entry or coerced restore flag never authorizes write."""
    now = [0.0]
    clock = SweepClock(30, lambda: now[0])
    if fault == "second_attempt":
        clock.begin_restore()
        action = clock.begin_restore
        reason = "permitted once"
    elif fault == "short_reserve":
        now[0] = 22.001
        action = clock.begin_restore
        reason = "Insufficient restoration reserve"
    else:

        def action() -> None:
            return clock.admit_write(restoring=cast("Any", 1))

        reason = "Invalid restoring flag"
    with pytest.raises(ValueError, match=reason):
        action()
    assert not clock.restoration_write


@pytest.mark.parametrize("fault", ["callback", "regression", "nonfinite", "boolean"])
def test_geometry_budget_latches_original_clock_failure(fault: str) -> None:
    """A recovered callback cannot restore admission after any clock uncertainty."""
    clock = Mock(return_value=1.0)
    budget = Budget(clock)
    if fault == "callback":
        clock.side_effect = OSError("clock lost")
    else:
        clock.return_value = {
            "regression": 0.0,
            "nonfinite": float("nan"),
            "boolean": True,
        }[fault]
    with pytest.raises(RuntimeError, match=r"clock lost|Invalid/regressed clock"):
        budget.check()
    clock.side_effect = None
    clock.return_value = 2.0
    with pytest.raises(RuntimeError, match=r"clock lost|Invalid/regressed clock"):
        budget.check()


def test_raster_overlap_cannot_choose_disagreeing_native_delta() -> None:
    """Unique actual overlap at 127 rows still loses to independent native128."""
    rows = [bytes([row % 256, row // 256, 71]) * 493 for row in range(621)]
    frames = tuple(
        ScrollFrame(493, 413, b"".join(rows[start : start + 413]))
        for start in (0, 127, 207)
    )
    with pytest.raises(ValueError, match="Native and raster displacement disagree"):
        assemble(frames, (128, 80))


@pytest.mark.parametrize("fault", ["missing", "nested_type", "nested_field"])
def test_setup_receipt_requires_outer_and_exact_nested_mapping(fault: str) -> None:
    """Unknown/missing receipt structure cannot confer focused-child authority."""
    receipt = focused_receipt(FONT_OWNER)
    value: Any = receipt
    if fault == "missing":
        value = None
    elif fault == "nested_type":
        receipt["target_setup"] = []
    else:
        receipt["target_setup"].pop("hide_caret")
    with pytest.raises(
        ValueError, match=r"Missing focused-child|Invalid focused-child setup schema"
    ):
        validate_target_setup(value, FONT_OWNER)


@pytest.mark.parametrize(
    "fault",
    [
        "text_length",
        "foreground",
        "visible",
        "dpi",
        "parent",
        "window",
        "hit",
        "monitor",
    ],
)
def test_native_snapshot_rejects_actual_sdk_boundary_drift(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """Each native result is read by the unmodified snapshot guard, without writes."""
    harness = Harness(monkeypatch)
    native = harness.native
    if fault == "text_length":
        harness.text = ""
        reason = "Frozen EDIT text length changed"
    elif fault == "foreground":
        native.user.GetForegroundWindow = lambda: 101
        reason = "Root foreground/visibility changed"
    elif fault == "visible":
        native._visible.return_value = False
        reason = "Root foreground/visibility changed"
    elif fault == "dpi":
        native.user.GetDpiForWindow = lambda _hwnd: 120
        reason = "Root DPI changed"
    elif fault == "parent":
        native.user.GetParent = lambda _hwnd: 999
        reason = "Child HWND/UIA/process association changed"
    elif fault == "window":

        def wrong_window(_hwnd: int, output: Any) -> int:
            rect = c.cast(output, c.POINTER(Rect)).contents
            rect.left, rect.top, rect.right, rect.bottom = 59, 60, 570, 473
            return 1

        native.user.GetWindowRect = wrong_window
        reason = "Frozen client/root mapping changed"
    elif fault == "hit":
        native.user.WindowFromPoint = lambda _point: 100
        reason = "Client point ownership changed"
    else:
        native._monitors.return_value = [[1, 0, 0, 572, 768, True]]
        reason = "Full root client not wholly on one monitor"
    with pytest.raises(ValueError, match=reason) as caught:
        native.snapshot()
    assert native.unsafe_write_reason is not None
    assert native.budget.failure is caught.value
    assert harness.writes == []
    native.close()


def test_first_visible_line_guard_uses_message_output_not_scroll_info(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An independently bad EM_GETFIRSTVISIBLELINE fails after valid SIF_ALL."""
    harness = Harness(monkeypatch)
    original = harness.send

    def bad_first(*args: Any) -> int:
        result = original(*args)
        if args[1] == 0xCE:
            c.cast(args[-1], c.POINTER(c.c_size_t)).contents.value = 14
        return result

    harness.native.user.SendMessageTimeoutW = bad_first
    with pytest.raises(
        ValueError, match="First visible line outside fixed vertical range"
    ):
        harness.native.snapshot()
    assert harness.native.unsafe_write_reason is not None
    assert harness.writes == []
    harness.native.close()


def test_native_start_logger_cannot_consume_tighter_call_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A three-second start record forbids dispatch under a longer session clock."""
    harness = Harness(monkeypatch)
    native = harness.native
    dispatch = Mock()

    def delay(event: dict[str, Any]) -> None:
        if event["event"] == "call_start":
            harness.now = 3

    native.edit_emit = delay
    with pytest.raises(RuntimeError, match="Native start logging >=3s"):
        native.raw(dispatch)
    dispatch.assert_not_called()
    assert native.unsafe_write_reason is not None
    native.close()


def test_missing_pattern_after_seek_start_cannot_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pattern lost during evidence admission does not reach a COM write."""
    harness = Harness(monkeypatch)
    native = harness.native

    def lose_pattern(event: dict[str, Any]) -> None:
        if event["event"] == "seek_start":
            native.pattern = None

    native.edit_emit = lose_pattern
    with pytest.raises(ValueError, match="Missing retained ScrollPattern"):
        native.seek(0, admit_write=lambda: None)
    assert harness.writes == []
    assert native.unsafe_write_reason is not None
    native.close()


def test_com_adapter_cannot_report_success_without_consuming_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed COM boundary is rejected even when HRESULT claims success."""
    harness = Harness(monkeypatch)
    native = harness.native
    native.pattern.call = Mock(return_value=0)
    admission = Mock()
    with pytest.raises(RuntimeError, match="Native write admission was not consumed"):
        native.seek(0, admit_write=admission)
    admission.assert_not_called()
    assert harness.writes == []
    assert native.unsafe_write_reason is not None
    assert native._write_admission is None
    native.close()


def test_normal_seek_does_not_ignore_fully_read_native_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the isolated restore primitive may handle a completed-state mismatch."""
    harness = Harness(monkeypatch)
    harness.position = 3
    with pytest.raises(
        ValueError, match="Native line/position/percentage disagreement"
    ):
        harness.native.seek(0, admit_write=lambda: None)
    assert harness.writes == []
    assert harness.native.unsafe_write_reason is not None
    harness.native.close()


@pytest.mark.parametrize("with_primary", [False, True])
def test_immediate_owned_release_preserves_primary_and_is_exactly_once(
    monkeypatch: pytest.MonkeyPatch, *, with_primary: bool
) -> None:
    """An owned release error cannot replace an active acquisition failure."""
    native = Harness(monkeypatch).native
    release = Mock(side_effect=OSError("release lost"))
    owner = native.own(release)
    if with_primary:
        primary = ValueError("acquisition first")
        try:
            raise primary
        except ValueError:
            native._release_now(owner)
        assert primary.__notes__ == ["Observation cleanup failed: OSError"]
    else:
        with pytest.raises(OSError, match="release lost"):
            native._release_now(owner)
    assert native.unsafe_write_reason == "release lost"
    native.close()
    release.assert_called_once()


def test_close_without_primary_attempts_all_owners_and_then_raises_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cleanup ordering is complete and idempotent even without an active error."""
    native = Harness(monkeypatch).native
    good = Mock()
    later = Mock(side_effect=ValueError("later close"))
    first = OSError("first close")
    native.own(good)
    native.own(later)
    native.own(Mock(side_effect=first))
    with pytest.raises(OSError, match="first close") as caught:
        native.close()
    assert caught.value is first
    assert first.__notes__ == ["Observation cleanup failed: ValueError"]
    native.close()
    good.assert_called_once()
    later.assert_called_once()
    assert native._owners == []


def test_snapshot_serialization_error_latches_after_native_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Output serialization cannot fail and leave later write admission healthy."""
    harness = Harness(monkeypatch)
    native = harness.native
    with monkeypatch.context() as patch:
        patch.setattr(
            native_module.json, "dumps", Mock(side_effect=OSError("serialize failed"))
        )
        with pytest.raises(OSError, match="serialize failed") as caught:
            native.snapshot()
    assert any(message[1] == 0xD6 for message in harness.sent)
    assert native.budget.failure is caught.value
    assert native.unsafe_write_reason == "serialize failed"
    native.close()


@pytest.mark.parametrize("kind", ["depth", "sequence", "mapping", "key"])
def test_failure_diagnostic_bounds_do_not_expose_unbounded_evidence(kind: str) -> None:
    """Failure-only diagnostics bound nesting, sequence size, mapping size and keys."""
    value: Any
    if kind == "depth":
        value = 1
        for _ in range(14):
            value = [value]
        expected = "depth-bound"
    elif kind == "sequence":
        value = [1] * 65
        expected = "sequence-bound"
    elif kind == "mapping":
        value = {str(index): 1 for index in range(129)}
        expected = "mapping-bound"
    else:
        value = {"x" * 129: 1}
        expected = "mapping-bound"
    result = native_module._diagnostic_value(value)
    encoded = json.dumps(result, allow_nan=False)
    assert expected in encoded
    assert len(encoded) < 200


def test_oversized_frozen_diagnostic_trims_sections_in_defined_order() -> None:
    """Bounded-size formatting drops large frozen sections without losing schema."""
    error = OSError("native failure")
    section = {str(index): "x" * 256 for index in range(128)}
    cast("Any", error)._native_diagnostic_frozen = {
        "schema": "owned-edit-scroll-native-diagnostic-v1",
        "frozen_baseline": section,
        "last_successful_snapshot": section,
        "current": section,
    }
    retain_native_diagnostic(error)
    diagnostic = cast("Any", error).native_diagnostic
    assert diagnostic["schema"] == "owned-edit-scroll-native-diagnostic-v1"
    for key in ("frozen_baseline", "last_successful_snapshot", "current"):
        assert diagnostic[key] == {
            "status": "unavailable",
            "reason": "diagnostic-size-bound",
        }
    assert len(json.dumps(diagnostic).encode()) < 24_576


@pytest.mark.parametrize("stage", ["freeze", "format"])
def test_diagnostic_infrastructure_failure_never_replaces_original(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    """A failed copy or encoder remains supplemental to the native failure."""
    native = Harness(monkeypatch).native
    primary = OSError("native first")
    if stage == "freeze":
        monkeypatch.setattr(
            native_module.copy, "deepcopy", Mock(side_effect=ValueError("copy lost"))
        )
    else:
        native._freeze_diagnostic(primary)
        monkeypatch.setattr(
            native_module.json, "dumps", Mock(side_effect=ValueError("encode lost"))
        )
    native.retain_diagnostic(primary)
    assert primary.args == ("native first",)
    assert primary.__notes__ == ["Native diagnostic unavailable: ValueError"]
    assert not hasattr(primary, "native_diagnostic")
    native.close()
