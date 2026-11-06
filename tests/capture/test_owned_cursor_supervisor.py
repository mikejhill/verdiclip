"""Pure fault controls for the hosted supervisor's retirement boundary."""

from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest
from tests.capture import owned_cursor_supervisor as supervisor
from tests.capture.owned_cursor_observer import FirstRetirement
from tests.capture.test_owned_cursor_observer import BASE


def result(*, cleaned: bool) -> dict[str, object]:
    """Build only the public fixed adapter schema."""
    return {
        "admitted": True,
        "balance_attempted": True,
        "balance_confirmed": True,
        "hwnd_destroyed": cleaned,
        "thread_retired": cleaned,
        "foreign_preserved": True,
        "cleanup_confirmed": cleaned,
        "reason": "completed" if cleaned else "cleanup-unconfirmed",
    }


@pytest.mark.parametrize("cleaned", [False, True])
def test_first_snapshot_requires_confirmed_retirement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    cleaned: bool,
) -> None:
    """Unconfirmed cleanup cannot produce a purported post-retirement sample."""
    child = supervisor.Child(tmp_path)
    child.first = FirstRetirement(BASE)
    process = Mock()
    process.wait.return_value = 0
    child.process = cast("supervisor.subprocess.Popen[bytes]", process)
    monkeypatch.setattr(
        child, "await_file", lambda *_args: {"result": result(cleaned=cleaned)}
    )
    reader = Mock(return_value=BASE)
    monkeypatch.setattr(supervisor, "observe", reader)
    if cleaned:
        child.finish()
        reader.assert_called_once()
        process.wait.assert_called_once_with(timeout=2)
    else:
        with pytest.raises(AssertionError):
            child.finish()
        reader.assert_not_called()
        assert child.first.attempted is False


def test_kill_failure_still_attempts_owned_wait(tmp_path: Path) -> None:
    """Independent bounded reap runs even when kill raises ordinary error."""
    child = supervisor.Child(tmp_path)
    process = Mock()
    process.poll.return_value = None
    process.kill.side_effect = OSError("owned-kill-failed")
    child.process = cast("supervisor.subprocess.Popen[bytes]", process)
    with pytest.raises(OSError, match="owned-kill-failed"):
        child.close()
    process.wait.assert_called_once_with(timeout=2)


def test_kill_control_still_attempts_owned_wait(tmp_path: Path) -> None:
    """Control signals propagate after independent bounded retirement attempt."""
    child = supervisor.Child(tmp_path)
    process = Mock()
    process.poll.return_value = None
    process.kill.side_effect = KeyboardInterrupt
    child.process = cast("supervisor.subprocess.Popen[bytes]", process)
    with pytest.raises(KeyboardInterrupt):
        child.close()
    process.wait.assert_called_once_with(timeout=2)


@pytest.mark.parametrize(
    "payload",
    [
        '{"nonce":"N","phase":"admitted","phase":"admitted"}',
        '{"nonce":"N","phase":"admitted","extra":true}',
        '{"nonce":"N","phase":"retired"}',
        '{"nonce":"N","ready":1}',
        '{"nonce":"N","phase":"admitted"}' + " " * 4096,
    ],
)
def test_protocol_rejects_malformed_metadata(tmp_path: Path, payload: str) -> None:
    """Duplicate, extended, mismatched and oversized messages cannot authorize work."""
    child = supervisor.Child(tmp_path)
    child.nonce = "N"
    (tmp_path / "admitted.json").write_text(payload)
    with pytest.raises(ValueError, match=r"child-(metadata|schema)"):
        child.read("admitted.json")


def test_stale_admission_cannot_authorize_crash(tmp_path: Path) -> None:
    """A completed component cannot masquerade as a forced-death observation."""
    child = supervisor.Child(tmp_path)
    process = Mock()
    process.poll.return_value = None
    child.process = cast("supervisor.subprocess.Popen[bytes]", process)
    child.first = FirstRetirement(BASE)
    (tmp_path / "retired.json").write_text(
        '{"nonce":"' + child.nonce + '","phase":"retired"}'
    )
    with pytest.raises(RuntimeError, match="already retired"):
        child.crash()
    process.kill.assert_not_called()


def test_unattested_child_cannot_start_native_work(tmp_path: Path) -> None:
    """The runtime handshake must pass before any surface authorization."""
    child = supervisor.Child(tmp_path)
    with pytest.raises(RuntimeError, match="runtime-not-attested"):
        child.begin(BASE)
    assert not (tmp_path / "start").exists()
    assert child.first is None


@pytest.mark.parametrize(("exit_code", "marker"), [(0, True), (97, False), (97, True)])
def test_crash_requires_exact_abrupt_exit_and_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exit_code: int, *, marker: bool
) -> None:
    """Normal exit or absent owner-thread proof cannot qualify forced retirement."""
    child = supervisor.Child(tmp_path)
    child.first = FirstRetirement(BASE)
    process = Mock()
    process.poll.return_value = None
    process.wait.return_value = exit_code
    child.process = cast("supervisor.subprocess.Popen[bytes]", process)
    if marker:
        (tmp_path / "crash-entry.json").write_text(
            '{"nonce":"' + child.nonce + '","phase":"crash-entry"}'
        )
    reader = Mock(return_value=BASE)
    monkeypatch.setattr(supervisor, "observe", reader)
    if exit_code == 97 and marker:
        child.crash()
        reader.assert_called_once()
    else:
        with pytest.raises(RuntimeError, match="no proven unbalanced"):
            child.crash()
        reader.assert_not_called()
    process.kill.assert_not_called()
    process.wait.assert_called_once_with(timeout=2)
