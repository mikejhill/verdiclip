"""Fixed event-readiness protocol controls; no native input or Windows execution."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from tests.capture.native_pointer_driver import Driver, State
from tests.capture.native_pointer_setup import prepare, read, wait_receipt
from tests.capture.scroll_peer import ScrollPeer

from verdiclip.geometry import Point, Rect


def fixtures(tmp_path: Path) -> tuple[Mock, ScrollPeer]:
    """Private identity and fixed native guards, no process is launched."""
    driver = Mock(spec=Driver)
    driver.fixture, driver.fixture_pid = 7, 8
    driver.read.return_value = State(1, 1, Point(10, 10), 7, (0,) * 6)
    driver.client.return_value = Rect(0, 0, 20, 20)
    driver.api = Mock()
    driver.api.WindowFromPoint.return_value = 9
    peer = ScrollPeer(tmp_path, provider="winforms", native_pointer_cursor=True)
    peer.identity = (8, 11, 7)
    peer.process = Mock()
    peer.process.pid = 8
    peer.process.poll.return_value = None
    return driver, peer


def test_one_move_only_after_armed_then_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Arming precedes one distinct-point MOVE and immutable event receipt."""
    driver, peer = fixtures(tmp_path)
    sequence: list[str] = []

    def receipt(
        path: Path, expected: dict[str, object], deadline: float, pause: object
    ) -> None:
        del deadline, pause
        sequence.append(path.name)
        assert expected["nonce"] == peer.nonce
        assert expected["pid"] == 8
        assert expected["hwnd"] == 7
        if path.name == "pointer-setup-armed.json":
            driver.move.assert_not_called()
        else:
            driver.move.assert_called_once_with(Point(11, 10))

    monkeypatch.setattr("tests.capture.native_pointer_setup.wait_receipt", receipt)
    target = prepare(driver, peer, Mock())
    assert target == Point(11, 10)
    assert sequence == ["pointer-setup-armed.json", "pointer-setup-ready.json"]
    request = json.loads((tmp_path / "pointer-setup-request.json").read_text())
    assert request["target_hwnd"] == 9
    assert request["x"] == 11


def test_arm_failure_never_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No input is injected when the owned observer cannot arm."""
    driver, peer = fixtures(tmp_path)
    monkeypatch.setattr(
        "tests.capture.native_pointer_setup.wait_receipt",
        Mock(side_effect=RuntimeError("arm")),
    )
    with pytest.raises(RuntimeError, match="arm"):
        prepare(driver, peer, Mock())
    driver.move.assert_not_called()


def test_ready_failure_never_repeats_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing handler acknowledgment never causes another MOVE or normalization."""
    driver, peer = fixtures(tmp_path)
    monkeypatch.setattr(
        "tests.capture.native_pointer_setup.wait_receipt",
        Mock(side_effect=[None, RuntimeError("ready")]),
    )
    with pytest.raises(RuntimeError, match="ready"):
        prepare(driver, peer, Mock())
    driver.move.assert_called_once()


def test_protocol_cannot_rearm(tmp_path: Path) -> None:
    """A prior request prevents another setup MOVE on the same fixture."""
    driver, peer = fixtures(tmp_path)
    (tmp_path / "pointer-setup-request.json").write_text("{}")
    with pytest.raises(RuntimeError, match="already-used"):
        prepare(driver, peer, Mock())
    driver.move.assert_not_called()


@pytest.mark.parametrize("payload", ['{"x":1,"x":2}', "[]", "x" * 4097])
def test_read_rejects_ambiguous_or_oversized(tmp_path: Path, payload: str) -> None:
    """Bound actual bytes, not merely a preceding stat size."""
    path = tmp_path / "receipt.json"
    path.write_text(payload)
    with pytest.raises((ValueError, TypeError, RuntimeError)):
        read(path)


def test_late_ready_is_not_accepted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A newly available reply cannot extend the original deadline."""
    monkeypatch.setattr(
        "tests.capture.native_pointer_setup.time.monotonic", Mock(side_effect=[1, 3])
    )
    monkeypatch.setattr(
        "tests.capture.native_pointer_setup.read", lambda _path: {"count": 1}
    )
    with pytest.raises(RuntimeError, match="deadline"):
        wait_receipt(tmp_path / "ready", {"count": 1}, 2, Mock())


def test_bool_is_not_integer_event_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """JSON bool equality cannot pass the count field schema."""
    monkeypatch.setattr("tests.capture.native_pointer_setup.time.monotonic", lambda: 1)
    monkeypatch.setattr(
        "tests.capture.native_pointer_setup.read", lambda _path: {"count": True}
    )
    with pytest.raises(RuntimeError, match="receipt-invalid"):
        wait_receipt(tmp_path / "ready", {"count": 1}, 2, Mock())


def test_fixture_observer_is_post_handler_readiness_only() -> None:
    """Source contract: observe after normal handler; no input/cursor-state writes."""
    source = Path(__file__).with_name("native_pointer_fixture.cs").read_text()
    assert source.index("base.WndProc(ref message)") < source.index("Count=1")
    assert "message.WParam == Handle" in source
    assert "WindowFromPoint(point) != Handle" in source
    for forbidden in (
        "SetCursor(",
        "ShowCursor(",
        "SendInput(",
        "SetCapture(",
        "SetCursorPos(",
    ):
        assert forbidden not in source


def test_fixture_receipt_follows_observer_detach_and_exact_read() -> None:
    """No prefix-only request or still-installed observer can qualify readiness."""
    source = Path(__file__).with_name("native_pointer_setup.ps1").read_text()
    assert "$stream.Length" in source
    assert "while ($offset -lt $length)" in source
    assert "$stream.ReadByte() -ne -1" in source
    assert source.index("$State.observer.ReleaseHandle()") < source.index("$receipt=@{")
    assert "observer.Handle -ne [IntPtr]::Zero" in source


@pytest.mark.parametrize("field", ["handler_completed", "assigned_cursor_observed"])
@pytest.mark.parametrize("invalid", [False, 1, None])
def test_first_unqualified_observation_never_waits_for_better(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, field: str, invalid: object
) -> None:
    """Completed zero-result handling is separate from a qualified first cursor read."""
    expected: dict[str, object] = {
        "handler_completed": True,
        "assigned_cursor_observed": True,
        "count": 1,
    }
    observe = Mock(side_effect=[{**expected, field: invalid}, expected])
    pause = Mock()
    monkeypatch.setattr("tests.capture.native_pointer_setup.read", observe)
    monkeypatch.setattr("tests.capture.native_pointer_setup.time.monotonic", lambda: 1)
    with pytest.raises(RuntimeError, match="receipt-invalid"):
        wait_receipt(tmp_path / "ready", expected, 2, pause)
    observe.assert_called_once()
    pause.assert_not_called()


def test_completed_zero_result_protocol_is_accepted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """LRESULT is deliberately not a completion or cursor-shape oracle."""
    expected: dict[str, object] = {
        "handler_completed": True,
        "assigned_cursor_observed": True,
        "count": 1,
    }
    monkeypatch.setattr("tests.capture.native_pointer_setup.read", lambda _: expected)
    monkeypatch.setattr("tests.capture.native_pointer_setup.time.monotonic", lambda: 1)
    wait_receipt(tmp_path / "ready", expected, 2, Mock())


def test_native_first_cursor_read_is_latched_after_handler() -> None:
    """Never infer handler completion from Result or poll for a favorable cursor."""
    source = Path(__file__).with_name("native_pointer_fixture.cs").read_text()
    assert "message.Result" not in source
    assert source.index("base.WndProc(ref message)") < source.index(
        "HandlerCompleted=true"
    )
    assert (
        source.index("Count=1")
        < source.index("armed=false")
        < source.index("GetCursorInfo(ref info)")
    )
    assert source.count("GetCursorInfo(ref info)") == 1
    assert "info.Flags == 1" in source
    assert "info.Cursor == expectedCursor" in source
    assert "assignedCursor == IntPtr.Zero" in source
    script = Path(__file__).with_name("native_pointer_setup.ps1").read_text()
    assert "::new($Viewer.Handle,$x,$y,$Viewer.Cursor.Handle)" in script
    assert (
        "assigned_cursor_observed=[bool]$State.observer.AssignedCursorObserved"
        in script
    )
