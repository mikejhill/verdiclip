"""Protocol-only child fixtures: no desktop, UIA, input, or target operations."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from verdiclip.capture.scroll.uia_process import UiaAction, UiaProcess
from verdiclip.exceptions import CaptureError


@pytest.fixture
def helper(tmp_path: Path) -> Path:
    """Create a fixed local protocol peer with deliberate failure modes."""
    script = tmp_path / "protocol_peer.py"
    script.write_text(
        "import json,sys,time\n"
        "mode=sys.argv[1]\n"
        "request=json.load(sys.stdin)\n"
        "if mode=='hang': time.sleep(30)\n"
        "elif mode=='bad': print('not-json')\n"
        "elif mode=='flood': print('x'*200000)\n"
        "elif mode=='integer': print('{\"number\":'+('1'*5000)+'}')\n"
        "elif mode=='deep': print('['*2000+'0'+']'*2000)\n"
        "elif mode=='exit': sys.exit(3)\n"
        "else:\n"
        " identity='other' if mode=='foreign' else request['request_id']\n"
        " result={'request_id':identity,'ok':mode!='unsupported'}\n"
        " result['result']={'observed':True}\n"
        " print(json.dumps(result))\n",
        encoding="utf-8",
    )
    return script


def test_unqualified_backend_cannot_launch_a_helper(helper: Path) -> None:
    """Native support cannot silently become enabled through ordinary UI use."""
    client = UiaProcess(helper_command=(sys.executable, str(helper), "success"))
    with pytest.raises(CaptureError, match="preservation is verified"):
        client.start(UiaAction.STEP, {})
    assert not client.busy
    client.deleteLater()


def test_success_releases_slot_before_callback_retry(
    qtbot: QtBot, helper: Path
) -> None:
    """Completed work can chain safely without duplicate delivery or a busy race."""
    client = UiaProcess(
        helper_command=(sys.executable, str(helper), "success"), qualified=True
    )
    results: list[object] = []

    def receive(_request: object, reply: object) -> None:
        assert not client.busy
        results.append(reply)
        if len(results) == 1:
            client.start(UiaAction.OBSERVE, {})

    client.completed.connect(receive)
    client.start(UiaAction.INSPECT, {})
    qtbot.waitUntil(lambda: len(results) == 2)
    assert not client.busy
    client.deleteLater()


@pytest.mark.parametrize(
    "mode", ["bad", "foreign", "unsupported", "exit", "flood", "integer", "deep"]
)
def test_invalid_response_fails_once_and_allows_retry(
    qtbot: QtBot, helper: Path, mode: str
) -> None:
    """Malformed, unrelated, failed and oversized replies cannot look successful."""
    client = UiaProcess(
        helper_command=(sys.executable, str(helper), mode), qualified=True
    )
    successes: list[object] = []
    failures: list[str] = []
    client.completed.connect(lambda _request, reply: successes.append(reply))
    client.failed.connect(lambda _request, message: failures.append(message))
    with qtbot.waitSignal(client.failed):
        client.start(UiaAction.INSPECT, {})
    qtbot.waitUntil(lambda: not client.busy)
    assert len(failures) == 1
    assert not successes
    client.deleteLater()


def test_silent_provider_deadline_kills_only_owned_child(
    qtbot: QtBot, helper: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hung external provider cannot leave progress or its process stuck."""
    monkeypatch.setattr(UiaProcess, "DEADLINE_MS", 100)
    client = UiaProcess(
        helper_command=(sys.executable, str(helper), "hang"), qualified=True
    )
    with qtbot.waitSignal(client.failed) as failed:
        client.start(UiaAction.OBSERVE, {})
    assert "timed out" in failed.args[1]
    qtbot.waitUntil(lambda: not client.busy)
    client.deleteLater()


def test_cancel_rejects_callbacks_and_pending_request(
    qtbot: QtBot, helper: Path
) -> None:
    """Repeated cancellation emits no extra failure or stale completion."""
    client = UiaProcess(
        helper_command=(sys.executable, str(helper), "hang"), qualified=True
    )
    failures: list[str] = []
    client.failed.connect(lambda _request, message: failures.append(message))
    client.start(UiaAction.STEP, {})
    with pytest.raises(CaptureError, match="busy"):
        client.start(UiaAction.STEP, {})
    client.cancel()
    client.cancel()
    qtbot.waitUntil(lambda: not client.busy)
    assert len(failures) == 1
    assert "cancelled" in failures[0]
    client.deleteLater()


def test_missing_executable_does_not_leak_busy_state(
    qtbot: QtBot, tmp_path: Path
) -> None:
    """Failed startup also has a terminal cleanup path even without finished."""
    client = UiaProcess(helper_command=(str(tmp_path / "missing.exe"),), qualified=True)
    with qtbot.waitSignal(client.failed):
        client.start(UiaAction.INSPECT, {})
    qtbot.waitUntil(lambda: not client.busy)
    client.deleteLater()


@pytest.mark.skipif(sys.platform != "win32", reason="Checks the installed Windows host")
def test_default_host_has_no_profile_or_policy_bypass() -> None:
    """Inspect the fixed Windows host arguments without launching any helper."""
    command = UiaProcess._default_command()
    assert Path(command[0]).name == "powershell.exe"
    assert "-NoProfile" in command
    assert "-NonInteractive" in command
    assert "-Mta" in command
    assert "-ExecutionPolicy" not in command
    assert Path(command[-1]).name == "uia_helper.ps1"


def test_oversized_request_does_not_reserve_child(helper: Path) -> None:
    """Provider request data is bounded before child creation or busy state."""
    client = UiaProcess(helper_command=(sys.executable, str(helper)), qualified=True)
    with pytest.raises(CaptureError, match="size limit"):
        client.start(UiaAction.INSPECT, {"data": "x" * UiaProcess.MAX_BYTES})
    assert not client.busy
    client.deleteLater()


def test_busy_notification_can_cancel_before_actual_dispatch(
    qtbot: QtBot, helper: Path
) -> None:
    """A reentrant owner cancellation cannot launch a stale queued child."""
    client = UiaProcess(
        helper_command=(sys.executable, str(helper), "success"), qualified=True
    )
    client.busy_changed.connect(lambda busy: client.cancel() if busy else None)
    with qtbot.waitSignal(client.failed) as failed:
        request = client.start(UiaAction.STEP, {})
    assert failed.args[0] is request
    assert not client.busy
    client.deleteLater()
