"""A second interaction never rewrites the first terminal observation."""

from pathlib import Path
from unittest.mock import Mock

import pytest
from tests.capture import test_native_pointer_native as native
from tests.capture.native_pointer_driver import State
from tests.capture.native_pointer_followup import followup
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
        "position_preserved": True,
        "foreign_gui_preserved": True,
        "appearance_preserved": False,
    }
    first[field] = False
    result = followup(driver, peer, Mock(), first, Mock())
    assert not result
    prepare.assert_not_called()


def test_terminal_failure_stays_failed_after_successful_followup(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The real terminal adapter reports the first mismatch before any MOVE."""
    state = State(1, 2, Point(11, 10), 7, (0,) * 6)
    driver = Mock()
    driver.read.return_value = state
    driver.cross_cursor.return_value = 2
    monkeypatch.setattr(native, "copy_appearance", Mock(return_value=object()))
    monkeypatch.setattr(native, "NativeReader", Mock())

    def later(*_args: object) -> bool:
        assert '"appearance_preserved": false' in capsys.readouterr().out
        return True

    second = Mock(side_effect=later)
    monkeypatch.setattr(native, "followup", second)
    with pytest.raises(AssertionError, match="First terminal observation failed"):
        native.terminal(
            Mock(), driver, state, state.point, Mock(), (), Mock(), final=False
        )
    second.assert_called_once()
