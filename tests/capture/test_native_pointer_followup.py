"""A second interaction never rewrites the first terminal observation."""

from pathlib import Path
from unittest.mock import Mock

import pytest
from tests.capture import test_native_pointer_native as native
from tests.capture.native_pointer_driver import State
from tests.capture.native_pointer_followup import followup, handoff_acceptable
from tests.capture.test_native_pointer_setup import fixtures

from verdiclip.geometry import Point


@pytest.mark.parametrize("first_match", [False, True])
def test_second_sample_does_not_change_first(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    first_match: bool,
) -> None:
    """Fixed bounded follow-up control without native execution."""
    driver, peer = fixtures(tmp_path)
    first = {
        "visible": True,
        "cleanup_confirmed": True,
        "terminal_matches_cross_resource": True,
        "position_preserved": True,
        "foreign_gui_preserved": True,
        "appearance_preserved": first_match,
    }
    saved = dict(first)
    expected = Mock()
    driver.read.return_value = State(1, 2, Point(11, 10), 7, (0,) * 6)
    prepare = Mock(return_value=Point(11, 10))
    copy = Mock(return_value=expected)
    monkeypatch.setattr("tests.capture.native_pointer_followup.prepare", prepare)
    monkeypatch.setattr("tests.capture.native_pointer_followup.copy_appearance", copy)
    monkeypatch.setattr("tests.capture.native_pointer_followup.NativeReader", Mock())
    result = followup(driver, peer, Mock(), first, expected)
    assert result
    assert first == saved
    prepare.assert_called_once()
    assert prepare.call_args.kwargs == {"phase": "post-retirement"}
    copy.assert_called_once()
    assert driver.allowed == {7: 8}
    assert '"phase": "subsequent-interaction"' in capsys.readouterr().out


def test_guard_refusal_sends_no_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fixed bounded follow-up control without native execution."""
    driver, peer = fixtures(tmp_path)
    driver.guard.side_effect = RuntimeError("private error")
    prepare = Mock()
    monkeypatch.setattr("tests.capture.native_pointer_followup.prepare", prepare)
    result = followup(
        driver,
        peer,
        Mock(),
        {
            "visible": True,
            "cleanup_confirmed": True,
            "terminal_matches_cross_resource": True,
            "position_preserved": True,
            "foreign_gui_preserved": True,
            "appearance_preserved": False,
        },
        Mock(),
    )
    assert not result
    prepare.assert_not_called()
    driver.move.assert_not_called()


@pytest.mark.parametrize("failure", ["deadline", "receipt-invalid"])
def test_failed_ack_never_observes_or_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Fixed bounded follow-up control without native execution."""
    driver, peer = fixtures(tmp_path)
    prepare = Mock(side_effect=RuntimeError(failure))
    copy = Mock()
    monkeypatch.setattr("tests.capture.native_pointer_followup.prepare", prepare)
    monkeypatch.setattr("tests.capture.native_pointer_followup.copy_appearance", copy)
    result = followup(
        driver,
        peer,
        Mock(),
        {
            "visible": True,
            "cleanup_confirmed": True,
            "terminal_matches_cross_resource": True,
            "position_preserved": True,
            "foreign_gui_preserved": True,
            "appearance_preserved": False,
        },
        Mock(),
    )
    assert not result
    prepare.assert_called_once()
    copy.assert_not_called()


def test_control_is_not_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fixed bounded follow-up control without native execution."""
    driver, peer = fixtures(tmp_path)
    monkeypatch.setattr(
        "tests.capture.native_pointer_followup.prepare",
        Mock(side_effect=KeyboardInterrupt),
    )
    with pytest.raises(KeyboardInterrupt):
        followup(
            driver,
            peer,
            Mock(),
            {
                "visible": True,
                "cleanup_confirmed": True,
                "terminal_matches_cross_resource": True,
                "position_preserved": True,
                "foreign_gui_preserved": True,
                "appearance_preserved": False,
            },
            Mock(),
        )


@pytest.mark.parametrize(
    "field", ["visible", "position_preserved", "foreign_gui_preserved"]
)
def test_first_state_failure_refuses_followup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    """Fixed bounded follow-up control without native execution."""
    driver, peer = fixtures(tmp_path)
    prepare = Mock()
    monkeypatch.setattr("tests.capture.native_pointer_followup.prepare", prepare)
    first = {
        "visible": True,
        "cleanup_confirmed": True,
        "terminal_matches_cross_resource": True,
        "position_preserved": True,
        "foreign_gui_preserved": True,
        "appearance_preserved": False,
    }
    first[field] = False
    result = followup(driver, peer, Mock(), first, Mock())
    assert not result
    prepare.assert_not_called()


@pytest.mark.parametrize("final", [False, True])
def test_known_cross_retains_first_mismatch_without_early_movement(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *, final: bool
) -> None:
    """Sessions one to three never receive recovery input; fourth receives one."""
    state = State(1, 2, Point(11, 10), 7, (0,) * 6)
    driver = Mock()
    driver.read.return_value = state
    driver.cross_cursor.return_value = 2
    monkeypatch.setattr(native, "copy_appearance", Mock(return_value=object()))
    monkeypatch.setattr(native, "NativeReader", Mock())
    second = Mock(return_value=True)
    monkeypatch.setattr(native, "followup", second)
    native.terminal(Mock(), driver, state, state.point, Mock(), (), Mock(), final=final)
    receipt = capsys.readouterr().out
    assert '"appearance_preserved": false' in receipt
    assert '"first_handoff_acceptable": true' in receipt
    assert second.call_count == int(final)


@pytest.mark.parametrize(
    "bad", ["unknown", "null-resource", "invisible", "position", "gui"]
)
def test_unqualified_terminal_never_injects_followup(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    """Unknown appearance or any safety-state mismatch stops before interaction."""
    before = State(1, 2, Point(11, 10), 7, (0,) * 6)
    after = State(
        0 if bad == "invisible" else 1,
        0 if bad == "null-resource" else 2,
        Point(12, 10) if bad == "position" else before.point,
        7,
        (1,) * 6 if bad == "gui" else before.gui,
    )
    driver = Mock()
    driver.read.return_value = after
    driver.cross_cursor.return_value = (
        0 if bad == "null-resource" else 3 if bad == "unknown" else 2
    )
    monkeypatch.setattr(native, "copy_appearance", Mock(return_value=object()))
    monkeypatch.setattr(native, "NativeReader", Mock())
    second = Mock(return_value=True)
    monkeypatch.setattr(native, "followup", second)
    with pytest.raises(AssertionError, match="First stationary handoff unavailable"):
        native.terminal(
            Mock(), driver, before, before.point, Mock(), (), Mock(), final=True
        )
    second.assert_not_called()


@pytest.mark.parametrize(
    "key",
    [
        "cleanup_confirmed",
        "visible",
        "position_preserved",
        "foreign_gui_preserved",
        "terminal_matches_cross_resource",
    ],
)
@pytest.mark.parametrize("invalid", [False, None, 1])
def test_handoff_requires_exact_boolean_evidence(key: str, invalid: object) -> None:
    """Truthy/missing or arbitrary cursor evidence cannot qualify a handoff."""
    first: dict[str, object] = {
        "cleanup_confirmed": True,
        "visible": True,
        "position_preserved": True,
        "foreign_gui_preserved": True,
        "appearance_preserved": False,
        "terminal_matches_cross_resource": True,
    }
    first[key] = invalid
    assert not handoff_acceptable(first)


def test_exact_fixture_appearance_does_not_require_cross_identity() -> None:
    """The normal fixture cursor is an independently supported first state."""
    first = {
        "cleanup_confirmed": True,
        "visible": True,
        "position_preserved": True,
        "foreign_gui_preserved": True,
        "appearance_preserved": True,
        "terminal_matches_cross_resource": False,
    }
    assert handoff_acceptable(first)


def test_final_handoff_failure_still_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """A recognized stationary CrossCursor cannot excuse failed subsequent handoff."""
    state = State(1, 2, Point(11, 10), 7, (0,) * 6)
    driver = Mock()
    driver.read.return_value = state
    driver.cross_cursor.return_value = 2
    monkeypatch.setattr(native, "copy_appearance", Mock(return_value=object()))
    monkeypatch.setattr(native, "NativeReader", Mock())
    second = Mock(return_value=False)
    monkeypatch.setattr(native, "followup", second)
    with pytest.raises(AssertionError, match="Subsequent interaction"):
        native.terminal(
            Mock(), driver, state, state.point, Mock(), (), Mock(), final=True
        )
    second.assert_called_once()
