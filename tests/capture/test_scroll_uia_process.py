"""Protocol-only child fixtures: no desktop, UIA, input, or target operations."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from pathlib import Path, PureWindowsPath
from textwrap import indent
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject
from pytestqt.qtbot import QtBot
from tests.capture.uia_test_cleanup import wait_idle

from verdiclip.capture.scroll import uia_process, uia_worker
from verdiclip.capture.scroll.uia_diagnostics import WorkerTrace
from verdiclip.capture.scroll.uia_pipe import UiaPipe
from verdiclip.capture.scroll.uia_process import UiaAction, UiaProcess
from verdiclip.capture.scroll.uia_worker import UiaWorkerLease
from verdiclip.exceptions import CaptureError


def reply_observer_ms(*, operations: int = 1) -> int:
    """Observe the unchanged worker budgets; never define its phase deadlines."""
    return UiaProcess.STARTUP_MS + operations * UiaProcess.DEADLINE_MS + 1000


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell pipe protocol")
def test_powershell_warm_pipe_accepts_repeated_variable_length_requests(
    qtbot: QtBot, tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """The real host's private pipe works without UIA, a window or input hardware."""
    peer = tmp_path / "pipe-peer.ps1"
    peer.write_text(
        "param([string]$PipeName)\n"
        "$clock=[Diagnostics.Stopwatch]::StartNew()\n"
        "function Write-Phase([string]$phase) {\n"
        " try { [IO.File]::AppendAllText((Join-Path $PSScriptRoot 'phases.log'),"
        "($phase+':'+$clock.ElapsedMilliseconds+[Environment]::NewLine)) } catch {}\n"
        "}\n"
        "Write-Phase 'entered'\n"
        "[Console]::Error.WriteLine('VERDICLIP_UIA_PHASE=script_entered')\n"
        "$channel=[IO.Pipes.NamedPipeClientStream]::new('.', $PipeName,"
        "[IO.Pipes.PipeDirection]::InOut)\n"
        "$channel.Connect(4000)\n"
        "[Console]::Error.WriteLine('VERDICLIP_UIA_PHASE=pipe_connected')\n"
        "$reader=[IO.StreamReader]::new($channel,[Text.UTF8Encoding]::new($false))\n"
        "$writer=[IO.StreamWriter]::new($channel,[Text.UTF8Encoding]::new($false))\n"
        "$writer.AutoFlush=$true\n"
        "[Console]::Error.WriteLine('VERDICLIP_UIA_PHASE=runtime_ready')\n"
        "$writer.WriteLine((@{ready=$true;protocol=1}|ConvertTo-Json -Compress))\n"
        "Write-Phase 'waiting'\n"
        "while($null -ne ($line=$reader.ReadLine())) {\n"
        " Write-Phase 'received'\n"
        " $request=$line|ConvertFrom-Json\n"
        " Write-Phase 'parsed'\n"
        " $writer.WriteLine((@{request_id=$request.request_id;ok=$true;result=@{}}|"
        "ConvertTo-Json -Compress))\n"
        " Write-Phase 'replied'\n"
        "}\n",
        encoding="utf-8",
    )
    host = (
        Path(os.environ["SYSTEMROOT"])
        / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    client = UiaProcess(
        helper_command=(
            str(host),
            "-NoProfile",
            "-NonInteractive",
            "-Mta",
            "-File",
            str(peer),
        ),
        qualified=True,
        use_local_pipe=True,
    )
    failures: list[str] = []
    replies: list[object] = []
    client.failed.connect(lambda _request, error: failures.append(error))
    client.completed.connect(lambda _request, reply: replies.append(reply))

    def idle() -> bool:
        return not client.busy

    try:
        for length in (120, 344, 450, 4096):
            before = len(replies)
            client.start(UiaAction.SAMPLE, {"fixture": "x" * length})
            # The first request includes cold startup before its operation budget.
            # Wait for the production receipt/error rather than an earlier test cap.
            qtbot.waitUntil(
                lambda previous=before: len(replies) > previous or bool(failures),
                timeout=reply_observer_ms(),
            )
            if failures:
                try:
                    phases = (tmp_path / "phases.log").read_text(encoding="utf-8")
                except OSError as error:
                    phases = f"phase log unavailable: {type(error).__name__}"
                pytest.fail(f"{length=}; {failures=}; {client.diagnostics=}; {phases=}")
    finally:
        client.cancel()
        try:
            qtbot.waitUntil(idle)
        finally:
            diagnostics = client.diagnostics
            if diagnostics is not None:
                request.node.user_properties.append(
                    ("uia_diagnostics", json.dumps(asdict(diagnostics)))
                )
            client.deleteLater()


@pytest.fixture
def helper(tmp_path: Path) -> Path:
    """Create a fixed local protocol peer with deliberate failure modes."""
    script = tmp_path / "protocol_peer.py"
    code = (
        "import json,sys,time\n"
        "mode=sys.argv[1]\n"
        "if mode=='startup_hang': time.sleep(30)\n"
        "ready={'ready':True,'protocol':0 if mode=='unsupported_ready' else 1}\n"
        "print('not-json' if mode=='bad_ready' else json.dumps(ready),flush=True)\n"
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
        " print(json.dumps(result))\n"
    )
    header, _, body = code.partition("request=json.load(sys.stdin)\n")
    script.write_text(
        header
        + "for line in sys.stdin:\n request=json.loads(line)\n"
        + indent(body, " ")
        + " sys.stdout.flush()\n",
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
        snapshot = client.diagnostics
        assert snapshot is not None
        assert snapshot.action_timings[len(results)].succeeded == 1
        results.append(reply)
        if len(results) == 1:
            client.start(UiaAction.OBSERVE, {})

    client.completed.connect(receive)
    client.start(UiaAction.INSPECT, {})
    qtbot.waitUntil(lambda: len(results) == 2, timeout=reply_observer_ms(operations=2))
    assert not client.busy
    snapshot = client.diagnostics
    assert snapshot is not None
    assert snapshot.first_request.action is UiaAction.INSPECT
    assert snapshot.first_request.outcome == "success"
    assert [item.succeeded for item in snapshot.action_timings] == [1, 1, 0, 0, 0]
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
    with qtbot.waitSignal(client.failed, timeout=reply_observer_ms()):
        client.start(UiaAction.INSPECT, {})
    wait_idle(qtbot, client)
    assert len(failures) == 1
    assert not successes
    client.deleteLater()


def test_silent_provider_deadline_kills_only_owned_child(
    qtbot: QtBot, helper: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hung external provider cannot leave progress or its process stuck."""
    monkeypatch.setattr(UiaProcess, "DEADLINE_MS", 100)
    monkeypatch.setattr(UiaProcess, "STARTUP_MS", 100)
    client = UiaProcess(
        helper_command=(sys.executable, str(helper), "hang"), qualified=True
    )
    with qtbot.waitSignal(client.failed, timeout=reply_observer_ms()) as failed:
        client.start(UiaAction.OBSERVE, {})
    assert "timed out" in failed.args[1]
    wait_idle(qtbot, client)
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
    wait_idle(qtbot, client)
    assert len(failures) == 1
    assert "cancelled" in failures[0]
    client.deleteLater()


def test_missing_executable_does_not_leak_busy_state(
    qtbot: QtBot, tmp_path: Path
) -> None:
    """Failed startup also has a terminal cleanup path even without finished."""
    client = UiaProcess(helper_command=(str(tmp_path / "missing.exe"),), qualified=True)
    with qtbot.waitSignal(client.failed, timeout=reply_observer_ms()):
        client.start(UiaAction.INSPECT, {})
    wait_idle(qtbot, client)
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


def test_default_command_uses_exact_private_script_invocation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Check Windows command construction on every CI host without execution."""
    monkeypatch.setattr(uia_process, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setenv("SYSTEMROOT", str(tmp_path))
    host = tmp_path / "System32/WindowsPowerShell/v1.0/powershell.exe"
    helper = Path(uia_process.__file__).with_name("uia_helper.ps1")
    monkeypatch.setattr(Path, "is_file", lambda path: path in (host, helper))
    assert UiaProcess._default_command() == (
        str(host),
        "-NoProfile",
        "-NonInteractive",
        "-Mta",
        "-File",
        str(helper),
    )


def test_default_command_without_systemroot_uses_absolute_windows_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An absent environment variable must not choose a relative helper host."""
    monkeypatch.setattr(uia_process, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.delenv("SYSTEMROOT", raising=False)
    monkeypatch.setattr(Path, "is_file", lambda _path: True)
    host = PureWindowsPath(UiaProcess._default_command()[0])
    assert host.is_absolute()
    assert str(host).casefold() == (
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe".casefold()
    )


@pytest.mark.parametrize("missing", ["host", "helper"])
def test_either_missing_default_command_prerequisite_refuses_execution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, missing: str
) -> None:
    """A missing host or script fails before any helper can be reserved."""
    monkeypatch.setattr(uia_process, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setenv("SYSTEMROOT", str(tmp_path))
    host = tmp_path / "System32/WindowsPowerShell/v1.0/powershell.exe"
    helper = Path(uia_process.__file__).with_name("uia_helper.ps1")
    absent = host if missing == "host" else helper
    monkeypatch.setattr(Path, "is_file", lambda path: path != absent)
    with pytest.raises(CaptureError, match="helper is unavailable"):
        UiaProcess._default_command()


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
    with qtbot.waitSignal(client.failed, timeout=reply_observer_ms()) as failed:
        request = client.start(UiaAction.STEP, {})
    assert failed.args[0] is request
    assert not client.busy
    client.deleteLater()


def test_warm_worker_reuses_pid_and_cancellation_ends_scope(
    qtbot: QtBot, helper: Path
) -> None:
    """Repeated observations do not spawn a host or compile bindings per frame."""
    client = UiaProcess(
        helper_command=(sys.executable, str(helper), "success"), qualified=True
    )
    with qtbot.waitSignal(client.completed, timeout=reply_observer_ms()):
        client.start(UiaAction.INSPECT, {})
    first_pid = client.worker_pid
    assert first_pid is not None
    assert first_pid > 0
    with qtbot.waitSignal(client.completed, timeout=reply_observer_ms()):
        client.start(UiaAction.OBSERVE, {})
    assert client.worker_pid == first_pid
    client.cancel()
    wait_idle(qtbot, client)
    assert client.worker_pid is None
    client.deleteLater()


def test_isolated_mutation_uses_distinct_children_and_retires_both(
    qtbot: QtBot, helper: Path
) -> None:
    """Real protocol processes prove separation and cleanup without desktop APIs."""
    command = (sys.executable, str(helper), "success")
    client = UiaProcess(
        helper_command=command,
        native_helper_command=command,
        qualified=True,
        use_local_pipe=False,
    )
    cleanup: list[tuple[int, ...]] = []
    client.register_scope_cleanup(lambda: cleanup.append(client.worker_pids))
    try:
        with qtbot.waitSignal(client.completed, timeout=reply_observer_ms()):
            client.start(UiaAction.INSPECT, {})
        managed = client.worker_pid
        with qtbot.waitSignal(client.completed, timeout=reply_observer_ms()):
            client.start(UiaAction.STEP, {})
        native = client.worker_pid
        assert managed is not None
        assert native is not None
        assert managed != native
        assert set(client.worker_pids) == {managed, native}
        with qtbot.waitSignal(client.completed, timeout=reply_observer_ms()):
            client.start(UiaAction.OBSERVE, {})
        assert client.worker_pid == managed
        assert cleanup == []
        with qtbot.waitSignal(client.scope_closed, timeout=reply_observer_ms()):
            client.cancel()
        assert not client.busy
        assert client.worker_pid is None
        assert client.worker_pids == ()
        assert cleanup == [()]
        assert set(client.diagnostics_for_workers) == {"managed", "native"}
        assert all(
            trace.cleanup == "confirmed"
            for trace in client.diagnostics_for_workers.values()
        )
    finally:
        client.cancel()
        wait_idle(qtbot, client, timeout=reply_observer_ms())
        client.deleteLater()


@pytest.mark.parametrize("native_mode", ["success", "hang", "startup_hang"])
def test_shutdown_retires_both_protocol_children_without_an_event_loop_wait(
    qtbot: QtBot, helper: Path, tmp_path: Path, native_mode: str
) -> None:
    """Synchronous shutdown covers warm, active and pre-handshake child ownership."""
    client = UiaProcess(
        helper_command=(sys.executable, str(helper), "success"),
        native_helper_command=(sys.executable, str(helper), native_mode),
        qualified=True,
        use_local_pipe=False,
    )
    private = tmp_path / "owned-frame"
    private.write_bytes(b"synthetic")
    cleanup: list[tuple[int, ...]] = []

    def retire() -> None:
        cleanup.append(client.worker_pids)
        private.unlink()

    client.register_scope_cleanup(retire)
    try:
        with qtbot.waitSignal(client.completed, timeout=reply_observer_ms()):
            client.start(UiaAction.INSPECT, {})
        if native_mode == "success":
            with qtbot.waitSignal(client.completed, timeout=reply_observer_ms()):
                client.start(UiaAction.STEP, {})
            assert not client.busy  # Both children are warm, not outstanding work.
        else:
            client.start(UiaAction.STEP, {})
            qtbot.waitUntil(
                lambda: (
                    len(client.worker_pids) == 2
                    and client.diagnostics_for_workers["native"].process_state
                    == "Running"
                    and (
                        native_mode == "startup_hang"
                        or client.diagnostics_for_workers["native"].request_sent
                    )
                ),
                timeout=reply_observer_ms(),
            )
        assert len(set(client.worker_pids)) == 2
        assert cleanup == []
        client.shutdown()
        # No qtbot wait/processEvents after shutdown: it must retire synchronously.
        assert client.worker_pid is None
        assert client.worker_pids == ()
        assert not client.busy
        assert cleanup == [()]
        assert not private.exists()
        assert all(
            trace.cleanup == "confirmed"
            for trace in client.diagnostics_for_workers.values()
        )
        with pytest.raises(CaptureError, match="shut down"):
            client.start(UiaAction.INSPECT, {})
    finally:
        client.shutdown()
        client.deleteLater()


def test_warm_worker_idle_expiry_releases_child_and_allows_fresh_start(
    qtbot: QtBot, helper: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No helper remains indefinitely while target confirmation or review is idle."""
    monkeypatch.setattr(UiaWorkerLease, "IDLE_MS", 100)
    client = UiaProcess(
        helper_command=(sys.executable, str(helper), "success"), qualified=True
    )
    with qtbot.waitSignal(client.completed, timeout=reply_observer_ms()):
        client.start(UiaAction.INSPECT, {})
    qtbot.waitUntil(lambda: client.worker_pid is None)
    assert not client.busy
    with qtbot.waitSignal(client.completed, timeout=reply_observer_ms()):
        client.start(UiaAction.OBSERVE, {})
    client.cancel()
    wait_idle(qtbot, client)
    client.deleteLater()


def test_destroyed_ui_owner_releases_application_lease_after_child(
    qtbot: QtBot, helper: Path
) -> None:
    """Transient UI destruction cannot recursively destroy a running helper."""
    app = QCoreApplication.instance()
    assert app is not None
    before = {id(lease) for lease in app.findChildren(UiaWorkerLease)}
    owner = QObject()
    client = UiaProcess(
        owner, helper_command=(sys.executable, str(helper), "success"), qualified=True
    )
    with qtbot.waitSignal(client.completed, timeout=reply_observer_ms()):
        client.start(UiaAction.INSPECT, {})
    new_leases = [
        lease for lease in app.findChildren(UiaWorkerLease) if id(lease) not in before
    ]
    assert len(new_leases) == 1
    with qtbot.waitSignal(new_leases[0].destroyed):
        owner.deleteLater()


def test_worker_absolute_lifetime_interrupts_a_silent_request(
    qtbot: QtBot, helper: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An active request cannot extend the capture worker's absolute lifetime."""
    monkeypatch.setattr(UiaWorkerLease, "LIFETIME_MS", 100)
    client = UiaProcess(
        helper_command=(sys.executable, str(helper), "hang"), qualified=True
    )
    with qtbot.waitSignal(client.failed, timeout=reply_observer_ms()) as failed:
        client.start(UiaAction.OBSERVE, {})
    assert "lifetime limit" in failed.args[1]
    wait_idle(qtbot, client)
    assert client.worker_pid is None
    client.deleteLater()


def test_first_operation_has_its_own_deadline_after_ready(
    qtbot: QtBot, helper: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A long startup allowance cannot extend the first dispatched native action."""
    monkeypatch.setattr(UiaProcess, "STARTUP_MS", 5000)
    monkeypatch.setattr(UiaProcess, "DEADLINE_MS", 100)
    client = UiaProcess(
        helper_command=(sys.executable, str(helper), "hang"), qualified=True
    )
    with qtbot.waitSignal(client.failed, timeout=reply_observer_ms()) as failed:
        client.start(UiaAction.STEP, {})
    assert "operation timed out" in failed.args[1]
    wait_idle(qtbot, client)
    client.deleteLater()


@pytest.mark.parametrize("error_type", [RuntimeError, ValueError])
def test_cleanup_callback_failure_cannot_strand_other_resources(
    qtbot: QtBot, helper: Path, error_type: type[Exception]
) -> None:
    """A callback error cannot stop remaining cleanup or its terminal receipt."""
    client = UiaProcess(
        helper_command=(sys.executable, str(helper), "success"), qualified=True
    )
    cleaned: list[str] = []
    failures: list[str] = []

    def broken() -> None:
        raise error_type("Fixture cleanup failure")

    client.cleanup_failed.connect(failures.append)
    client.register_scope_cleanup(broken)
    client.register_scope_cleanup(lambda: cleaned.append("second resource"))
    # This integration fixture starts a real protocol-only child. Its observer
    # must cover the unchanged 8s startup + 3s operation policy, rather than
    # timing out at pytest-qt's default 5s before startup has expired.
    with qtbot.waitSignal(
        client.completed,
        timeout=reply_observer_ms(),
    ):
        client.start(UiaAction.INSPECT, {})
    with qtbot.waitSignal(client.scope_closed):
        client.cancel()
    wait_idle(qtbot, client)
    assert cleaned == ["second resource"]
    assert len(failures) == 1
    assert error_type.__name__ in failures[0]
    assert client.worker_pid is None
    client.deleteLater()


@pytest.mark.parametrize("mode", ["bad_ready", "unsupported_ready"])
def test_bad_startup_handshake_cannot_dispatch_an_operation(
    qtbot: QtBot, helper: Path, mode: str
) -> None:
    """Unreadable or incompatible workers fail visibly and release their child."""
    client = UiaProcess(
        helper_command=(sys.executable, str(helper), mode), qualified=True
    )
    with qtbot.waitSignal(client.failed, timeout=reply_observer_ms()) as failed:
        client.start(UiaAction.INSPECT, {})
    assert "startup" in failed.args[1] or "protocol" in failed.args[1]
    wait_idle(qtbot, client)
    assert client.worker_pid is None
    client.deleteLater()


@pytest.mark.parametrize("missing", ["platform", "host"])
def test_missing_native_prerequisites_fail_without_creating_worker(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    """An unsupported OS or incomplete installation cannot silently start capture."""
    client = UiaProcess(qualified=True)
    with monkeypatch.context() as patch:
        if missing == "platform":
            patch.setattr("verdiclip.capture.scroll.uia_process.sys.platform", "other")
        else:
            patch.setattr("verdiclip.capture.scroll.uia_process.sys.platform", "win32")
            patch.setattr(Path, "is_file", lambda _path: False)
        with pytest.raises(CaptureError, match=r"requires Windows|unavailable"):
            client.start(UiaAction.INSPECT, {})
    assert client.worker_pid is None
    client.deleteLater()


def test_pipe_admission_failure_retires_endpoint_and_scope(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failed listen never launches a child or leaves capture resources reserved."""
    endpoints: list[UiaPipe] = []
    retired: list[UiaPipe] = []
    cleaned: list[bool] = []
    original_close = UiaPipe.close

    def reject(pipe: UiaPipe) -> None:
        endpoints.append(pipe)
        raise CaptureError("fixture listen rejection")

    def close(pipe: UiaPipe) -> None:
        retired.append(pipe)
        original_close(pipe)

    def no_child(_lease: UiaWorkerLease) -> None:
        pytest.fail("Admission failure must not create a process")

    monkeypatch.setattr(UiaPipe, "listen", reject)
    monkeypatch.setattr(UiaPipe, "close", close)
    monkeypatch.setattr(UiaWorkerLease, "_new_process", no_child)
    client = UiaProcess(helper_command=("unused",), qualified=True, use_local_pipe=True)
    client.register_scope_cleanup(lambda: cleaned.append(True))
    with pytest.raises(CaptureError, match="fixture listen rejection"):
        client.start(UiaAction.INSPECT, {})
    assert len(endpoints) == 1
    assert retired == endpoints
    assert cleaned == [True]
    assert not client.busy
    assert client.worker_pid is None
    client.cancel()
    assert cleaned == [True]
    with qtbot.waitSignal(endpoints[0].destroyed):
        pass
    client.deleteLater()


@pytest.mark.usefixtures("qapp")
@pytest.mark.parametrize("before_destroyed_signal", [False, True])
def test_unexpected_process_destruction_keeps_cleanup_unconfirmed(
    *, before_destroyed_signal: bool
) -> None:
    """A late finish callback cannot touch a deleted wrapper or claim child exit."""
    lease = UiaWorkerLease()
    cleaned: list[bool] = []
    failures: list[str] = []
    lease.register_cleanup(lambda: cleaned.append(True))
    lease.cleanup_failed.connect(failures.append)
    process = lease._new_process()
    if before_destroyed_signal:
        process.destroyed.disconnect()
    process.deleteLater()
    QCoreApplication.sendPostedEvents(process, QEvent.Type.DeferredDelete)
    lease._finished(process)
    lease.cancel()
    assert lease.busy
    assert lease.worker_pid is None
    assert lease.diagnostics.cleanup == "unconfirmed"
    assert len(failures) == 1
    assert cleaned == []
    lease.deleteLater()


@pytest.mark.usefixtures("qapp")
def test_pipe_admission_and_cleanup_failures_are_both_retained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cleanup exception cannot hide admission failure or skip independent cleanup."""
    admission = CaptureError("fixture admission")
    cleanup = RuntimeError("fixture close")
    cleaned: list[bool] = []
    endpoints: list[UiaPipe] = []
    original_close = UiaPipe.close

    def reject(pipe: UiaPipe) -> None:
        endpoints.append(pipe)
        raise admission

    def fail_close(_pipe: UiaPipe) -> None:
        raise cleanup

    monkeypatch.setattr(UiaPipe, "listen", reject)
    monkeypatch.setattr(UiaPipe, "close", fail_close)
    client = UiaProcess(helper_command=("unused",), qualified=True, use_local_pipe=True)
    client.register_scope_cleanup(lambda: cleaned.append(True))
    closed: list[bool] = []
    client.scope_closed.connect(lambda: closed.append(True))
    with pytest.raises(CaptureError, match="cleanup is unconfirmed") as error:
        client.start(UiaAction.INSPECT, {})
    cause = error.value.__cause__
    assert isinstance(cause, ExceptionGroup)
    assert cause.exceptions == (admission, cleanup)
    assert closed == []
    assert cleaned == [True]
    assert client.busy
    assert client.diagnostics is not None
    assert client.diagnostics.cleanup == "unconfirmed"
    client.cancel()
    assert client.diagnostics.cleanup == "unconfirmed"
    assert client.worker_pid is None
    # Test-only endpoint was never listening; retire the fake failure's object.
    original_close(endpoints[0])
    client.deleteLater()


@pytest.mark.parametrize(
    ("mode", "outcome", "sent"),
    [
        ("startup_hang", "startup_timeout", False),
        ("hang", "operation_timeout", True),
        ("bad", "failure", True),
        ("foreign", "failure", True),
        ("unsupported", "failure", True),
        ("exit", "failure", True),
    ],
)
def test_retained_terminal_evidence_for_real_protocol_peer(  # noqa: PLR0913 - case + fixtures
    qtbot: QtBot,
    helper: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    outcome: str,
    *,
    sent: bool,
) -> None:
    """Actual process failures/timeouts remain once-only after child cleanup."""
    monkeypatch.setattr(UiaProcess, "STARTUP_MS", 500 if not sent else 5000)
    monkeypatch.setattr(UiaProcess, "DEADLINE_MS", 100)
    client = UiaProcess(
        helper_command=(sys.executable, str(helper), mode),
        qualified=True,
    )
    try:
        with qtbot.waitSignal(client.failed, timeout=reply_observer_ms()):
            client.start(UiaAction.INSPECT, {})
        wait_idle(qtbot, client)
        client.cancel()
        receipt = client.diagnostics
        assert receipt is not None
        assert receipt.first_request.outcome == outcome
        action = receipt.action_timings[0]
        assert action.accepted == 1
        assert action.sent == int(sent)
        assert action.failed + action.timed_out == 1
        assert action.succeeded == action.cancelled == 0
        assert (action.max_sent_to_terminal_ms is not None) is sent
        assert not receipt.timing_unavailable
    finally:
        client.shutdown()
        client.deleteLater()


@pytest.mark.parametrize("after_send", [False, True])
def test_retained_cancellation_before_and_after_send(
    qtbot: QtBot,
    helper: Path,
    *,
    after_send: bool,
) -> None:
    """A late peer callback or second cancel cannot replace the first outcome."""
    client = UiaProcess(
        helper_command=(sys.executable, str(helper), "hang"),
        qualified=True,
    )
    try:
        client.start(UiaAction.SAMPLE, {})
        if after_send:
            qtbot.waitUntil(
                lambda: (
                    client.diagnostics is not None
                    and client.diagnostics.first_request.request_sent_ms is not None
                ),
                timeout=reply_observer_ms(),
            )
        client.cancel()
        client.cancel()
        wait_idle(qtbot, client)
        receipt = client.diagnostics
        assert receipt is not None
        assert receipt.first_request.outcome == "cancelled"
        action = receipt.action_timings[2]
        assert action.cancelled == action.accepted == 1
        assert action.sent == int(after_send)
        assert action.failed == action.timed_out == action.succeeded == 0
    finally:
        client.shutdown()
        client.deleteLater()


def test_failed_diagnostic_clock_does_not_block_protocol_success(
    qtbot: QtBot,
    helper: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fault only the diagnostic clock, never the production deadline clock."""

    def broken_clock() -> float:
        raise RuntimeError("Private diagnostic failure")

    monkeypatch.setattr(
        uia_worker, "WorkerTrace", lambda _clock: WorkerTrace(broken_clock)
    )
    client = UiaProcess(
        helper_command=(sys.executable, str(helper), "success"),
        qualified=True,
    )
    try:
        with qtbot.waitSignal(client.completed, timeout=reply_observer_ms()):
            client.start(UiaAction.INSPECT, {})
        receipt = client.diagnostics
        assert receipt is not None
        assert receipt.timing_unavailable
        assert receipt.first_request.outcome == "success"
        assert receipt.first_request.terminal_ms is None
        assert receipt.action_timings[0].succeeded == 1
    finally:
        client.shutdown()
        client.deleteLater()
