"""Pure controls for immutable first-sample cursor evidence."""

from dataclasses import replace

import pytest
from tests.capture.owned_cursor_child import validate_result
from tests.capture.owned_cursor_observer import FirstRetirement, Snapshot, during_checks

BASE = Snapshot(1, 7, (10, 20), 9, 11, 12, (0, 9, 9, 0, 0, 0))


@pytest.mark.parametrize(
    "field", ["flags", "shape", "position", "foreground", "thread", "process", "gui"]
)
def test_adverse_first_sample_cannot_be_replaced(field: str) -> None:
    """Later recovery cannot erase any first-sample state change."""
    changed = {
        "flags": 0,
        "shape": 8,
        "position": (11, 20),
        "foreground": 10,
        "thread": 13,
        "process": 14,
        "gui": (0, 9, 9, 99, 0, 0),
    }
    latch = FirstRetirement(BASE)
    after = replace(BASE, **{field: changed[field]})
    latch.record(lambda: after)
    first = latch.summary()
    with pytest.raises(RuntimeError, match="already-observed"):
        latch.record(lambda: BASE)
    assert latch.summary() == first
    assert any(
        value is False
        for key, value in first.items()
        if key != "rendered_pointer_verified"
    )


def test_failed_first_observation_is_unknown_not_retryable() -> None:
    """Read failure consumes the one verdict boundary without exposing text."""
    latch = FirstRetirement(BASE)

    def failed() -> Snapshot:
        raise OSError("private error must not enter summary")

    with pytest.raises(OSError, match="private error"):
        latch.record(failed)
    assert latch.summary()["logical_visibility_restored"] is None
    assert "private" not in str(latch.summary())
    with pytest.raises(RuntimeError, match="already-observed"):
        latch.record(lambda: BASE)


def test_equal_global_snapshot_does_not_claim_rendered_proof() -> None:
    """Matching logical metadata never establishes visible pixels."""
    latch = FirstRetirement(BASE)
    latch.record(lambda: BASE)
    result = latch.summary()
    assert result["global_shape_restored"] is True
    assert result["foreign_gui_preserved"] is True
    assert result["rendered_pointer_verified"] is False
    assert "shape" not in result


@pytest.mark.parametrize("invalid", [None, {}, {"reason": []}, {"path": "private"}])
def test_child_result_rejects_unbounded_or_unknown_schema(invalid: object) -> None:
    """No unknown result payload is ever admitted to the reduced receipt."""
    with pytest.raises(ValueError, match="invalid-component"):
        validate_result(invalid)


def test_control_signal_consumes_first_observation() -> None:
    """Control flow propagates without granting a second restoration sample."""
    latch = FirstRetirement(BASE)

    def interrupted() -> Snapshot:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        latch.record(interrupted)
    with pytest.raises(RuntimeError, match="already-observed"):
        latch.record(lambda: BASE)
    assert latch.summary()["first_observation_available"] is False


@pytest.mark.parametrize("shape", [0, 8])
def test_hidden_different_handle_allowed_only_during_ownership(shape: int) -> None:
    """Natural thread ownership changes do not establish terminal restoration."""
    during = replace(BASE, flags=0, shape=shape)
    assert all(during_checks(BASE, during).values())
    latch = FirstRetirement(BASE)
    latch.record(lambda: replace(BASE, shape=shape))
    assert latch.summary()["global_shape_restored"] is False


@pytest.mark.parametrize("flags", [1, 2, 3, 4])
def test_only_exact_hidden_flag_qualifies_during(flags: int) -> None:
    """Touch/pen suppression, unknown state and showing are never hiding proof."""
    assert during_checks(BASE, replace(BASE, flags=flags))["logical_hidden"] is False


@pytest.mark.parametrize("field", ["foreground", "thread", "process"])
def test_during_foreign_identity_remains_required(field: str) -> None:
    """Removing the cross-owner shape gate does not weaken foreign identity."""
    during = replace(BASE, flags=0, **{field: 99})
    assert not all(during_checks(BASE, during).values())
