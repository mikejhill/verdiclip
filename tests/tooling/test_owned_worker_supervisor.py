"""Portable external watchdog gates; no native processes are started."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from scripts import owned_worker_supervisor as supervisor


def proof(path: Path) -> None:
    """Write a bounded complete fixture receipt."""
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "passed": True,
                "cases": [
                    {"experiment": case, "passed": True, "thread_finished": True}
                    for case in supervisor.CASES
                ],
            }
        )
    )


@pytest.mark.parametrize(
    ("exit_code", "members", "expected"),
    [
        (0, [], None),
        (None, [], "proof-timeout"),
        (9, [], "proof-exit-failed"),
        (0, [77], "proof-surviving-descendants"),
    ],
)
def test_supervisor_requires_exit_and_natural_empty_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exit_code: int | None,
    members: list[int],
    expected: str | None,
) -> None:
    """Forced retirement cannot turn timeout or lingering children into success."""
    executable = tmp_path / ".venv/Scripts/python.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()
    output = tmp_path / "evidence"
    job = Mock()
    job.start.return_value = (12, 34)
    job.wait.return_value = exit_code
    job.pids.return_value = members
    job.close.return_value = {"confirmed": True}
    monkeypatch.setattr(supervisor, "OwnedJob", lambda: job)
    monkeypatch.setattr(
        supervisor, "drain_naturally", lambda *_: {"natural_empty": not members}
    )
    read = Mock(return_value=True)
    monkeypatch.setattr(supervisor, "read_proof", read)
    result = supervisor.supervise(output, tmp_path)
    receipt = json.loads((output / "supervisor.json").read_text())
    assert receipt["failure"] == expected
    assert result == int(expected is not None)
    assert receipt["passed"] == (expected is None)
    job.close.assert_called_once()
    assert job.start.call_args.args[0][0] == str(executable)
    assert job.wait.call_args.args[0] == 34
    assert 0 <= job.wait.call_args.args[1] <= 45
    if expected is not None:
        read.assert_not_called()
    else:
        read.assert_called_once()


def test_missing_runtime_retains_failure_receipt(tmp_path: Path) -> None:
    """Setup failure still creates a supervisor artifact."""
    output = tmp_path / "evidence"
    assert supervisor.supervise(output, tmp_path) == 1
    receipt = json.loads((output / "supervisor.json").read_text())
    assert receipt["passed"] is False
    assert "missing" in receipt["failure"]


def test_bounded_proof_receipt_requires_all_thread_completions(tmp_path: Path) -> None:
    """A terminal result without thread retirement cannot pass the wrapper."""
    path = tmp_path / "proof/receipt.json"
    proof(path)
    assert supervisor.read_proof(path)
    value = json.loads(path.read_text())
    value["cases"][0]["thread_finished"] = False
    path.write_text(json.dumps(value))
    assert not supervisor.read_proof(path)
    path.write_bytes(b" " * (supervisor.MAX_RECEIPT_BYTES + 1))
    with pytest.raises(ValueError, match="bound"):
        supervisor.read_proof(path)


def test_cleanup_exception_preserves_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cleanup error supplements rather than overwrites the original timeout."""
    executable = tmp_path / ".venv/Scripts/python.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()
    job = Mock()
    job.start.return_value = (12, 34)
    job.wait.return_value = None
    job.close.side_effect = OSError("cleanup failed")
    monkeypatch.setattr(supervisor, "OwnedJob", lambda: job)
    output = tmp_path / "evidence"
    assert supervisor.supervise(output, tmp_path) == 1
    receipt = json.loads((output / "supervisor.json").read_text())
    assert receipt["failure"] == "proof-timeout"
    assert receipt["cleanup"]["confirmed"] is False


def test_zero_exit_observed_after_deadline_is_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Callback ordering cannot admit an expired success."""
    executable = tmp_path / ".venv/Scripts/python.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()
    job = Mock()
    job.start.return_value = (12, 34)
    job.wait.return_value = 0
    job.close.return_value = {"confirmed": True}
    monkeypatch.setattr(supervisor, "OwnedJob", lambda: job)
    now = iter([0, 1, 46])
    monkeypatch.setattr(supervisor.time, "monotonic", lambda: next(now))
    output = tmp_path / "evidence"
    assert supervisor.supervise(output, tmp_path) == 1
    receipt = json.loads((output / "supervisor.json").read_text())
    assert receipt["failure"] == "proof-deadline-exceeded"
    job.pids.assert_not_called()


@pytest.mark.parametrize("finished", [44.999, 45.0, 46.0])
def test_evidence_read_cannot_cross_admission_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    finished: float,
) -> None:
    """Valid evidence arriving exactly at or after the deadline cannot pass."""
    executable = tmp_path / ".venv/Scripts/python.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()
    job = Mock()
    job.start.return_value = (12, 34)
    job.wait.return_value = 0
    job.pids.return_value = []
    job.close.return_value = {"confirmed": True}
    monkeypatch.setattr(supervisor, "OwnedJob", lambda: job)
    read = Mock(return_value=True)
    monkeypatch.setattr(supervisor, "read_proof", read)
    now = iter([0, 1, 44, finished])
    monkeypatch.setattr(supervisor.time, "monotonic", lambda: next(now))
    output = tmp_path / "evidence"
    assert supervisor.supervise(output, tmp_path) == int(finished >= 45)
    read.assert_called_once()
    receipt = json.loads((output / "supervisor.json").read_text())
    assert receipt["passed"] == (finished < 45)
    expected = "proof-deadline-exceeded" if finished >= 45 else None
    assert receipt["failure"] == expected


@pytest.mark.parametrize("leaks", [False, True])
def test_natural_drain_observes_without_terminating(
    monkeypatch: pytest.MonkeyPatch,
    *,
    leaks: bool,
) -> None:
    """A naturally exiting residual passes; an actual leak reaches the same deadline."""
    now = [0.0]
    monkeypatch.setattr(supervisor.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        supervisor.time, "sleep", lambda delay: now.__setitem__(0, now[0] + delay)
    )
    observer = Mock()
    observer.handles = {}
    observer.open.side_effect = lambda pid: observer.handles.update({pid: 88})
    observer.snapshot.side_effect = lambda pid: {
        "pid": pid,
        "image": "owned.exe",
        "exited": True,
    }
    observer.close.return_value = []
    monkeypatch.setattr(supervisor, "ResidualObserver", lambda _: observer)
    job = Mock()
    job.pids.return_value = [77] if leaks else []
    result = supervisor.drain_naturally(job, [77], 0.02)
    assert result["natural_empty"] is not leaks
    assert result["initial_pids"] == [77]
    assert result["final_pids"] == ([77] if leaks else [])
    assert result["initial_witnesses"] == [
        {"pid": 77, "image": "owned.exe", "exited": True}
    ]
    observer.close.assert_called_once()
    job.close.assert_not_called()


@pytest.mark.parametrize("failure", ["reuse", "query", "open"])
def test_residual_identity_uncertainty_closes_handle_and_fails(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """A PID must still belong to the owned job before identity is inspected."""
    monkeypatch.setattr(supervisor.ctypes, "get_last_error", lambda: 5, raising=False)
    job = Mock()
    job.handle = 1
    job.api.OpenProcess.return_value = 0 if failure == "open" else 88
    job.api.IsProcessInJob.return_value = failure != "query"
    observer = supervisor.ResidualObserver(job)
    monkeypatch.setattr(supervisor, "ResidualObserver", lambda _: observer)
    result = supervisor.drain_naturally(job, [77], supervisor.time.monotonic() + 1)
    assert result["natural_empty"] is False
    assert result["errors"]
    job.api.QueryFullProcessImageNameW.assert_not_called()
    if failure == "open":
        job.api.CloseHandle.assert_not_called()
    else:
        job.api.CloseHandle.assert_called_once_with(88)
    assert not observer.handles


def test_final_residual_snapshot_cannot_cross_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slow kernel evidence after natural emptiness cannot extend admission."""
    now = [0.0]
    monkeypatch.setattr(supervisor.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(supervisor.time, "sleep", lambda _: None)
    observer = Mock()
    observer.handles = {}
    observer.open.side_effect = lambda pid: observer.handles.update({pid: 88})
    calls = [0]

    def snapshot(pid: int) -> dict[str, int]:
        calls[0] += 1
        if calls[0] == 2:
            now[0] = 45.0
        return {"pid": pid, "exited": True}

    observer.snapshot.side_effect = snapshot
    observer.close.return_value = []
    monkeypatch.setattr(supervisor, "ResidualObserver", lambda _: observer)
    job = Mock()
    job.pids.return_value = []
    result = supervisor.drain_naturally(job, [77], 45)
    assert result["final_pids"] == []
    assert result["natural_empty"] is False
    observer.close.assert_called_once()


def test_all_residual_handles_close_after_one_close_error() -> None:
    """One close failure cannot prevent attempting the other pinned handles."""
    job = Mock()
    observer = supervisor.ResidualObserver(job)
    observer.handles = {1: 10, 2: 20}
    job.api.CloseHandle.side_effect = [OSError("close failed"), True]
    assert observer.close()
    assert job.api.CloseHandle.call_count == 2
    assert not observer.handles


def test_residual_snapshot_pins_same_job_image_and_exit() -> None:
    """Read-only handle identity is checked before kernel image and exit reads."""
    job = Mock()
    job.handle = 1
    job.api.OpenProcess.return_value = 88

    def membership(_handle: int, _job: int, member: object) -> bool:
        member._obj.value = 1  # ty: ignore[unresolved-attribute] - ctypes byref
        return True

    def image(_handle: int, _flags: int, buffer: object, _size: object) -> bool:
        buffer.value = "C:\\Windows\\owned.exe"  # ty: ignore[unresolved-attribute]
        return True

    job.api.IsProcessInJob.side_effect = membership
    job.api.QueryFullProcessImageNameW.side_effect = image
    job.api.WaitForSingleObject.return_value = 0
    job.api.GetExitCodeProcess.return_value = True
    observer = supervisor.ResidualObserver(job)
    observer.open(77)
    witness = observer.snapshot(77)
    assert witness == {
        "pid": 77,
        "image": "C:\\Windows\\owned.exe",
        "exited": True,
        "exit_code": 0,
        "raw_exit_code": 0,
        "wait_result": 0,
    }
    job.api.OpenProcess.assert_called_once_with(0x00101000, 0, 77)
    job.api.WaitForSingleObject.assert_called_once_with(88, 0)
    assert observer.close() == []


@pytest.mark.parametrize("wait_result", [0, supervisor.WAIT_TIMEOUT])
def test_image_error_preserves_live_or_exited_partial_witness(
    monkeypatch: pytest.MonkeyPatch,
    wait_result: int,
) -> None:
    """Retain exit evidence and exact image errors while failing closed."""
    calls: list[str] = []
    job = Mock()
    job.handle = 1
    job.api.OpenProcess.return_value = 88

    def membership(_handle: int, _job: int, member: object) -> bool:
        member._obj.value = 1  # ty: ignore[unresolved-attribute] - ctypes byref
        return True

    def wait(_handle: int, _timeout: int) -> int:
        calls.append("wait")
        return wait_result

    def exit_code(_handle: int, code: object) -> bool:
        calls.append("exit")
        code._obj.value = 0 if wait_result == 0 else 259  # ty: ignore[unresolved-attribute]
        return True

    def image(*_args: object) -> bool:
        calls.append("image")
        return False

    def last_error() -> int:
        calls.append("GetLastError")
        return 31

    def close(_handle: int) -> bool:
        calls.append("close")
        return True

    job.api.IsProcessInJob.side_effect = membership
    job.api.WaitForSingleObject.side_effect = wait
    job.api.GetExitCodeProcess.side_effect = exit_code
    job.api.QueryFullProcessImageNameW.side_effect = image
    job.api.CloseHandle.side_effect = close
    monkeypatch.setattr(supervisor.ctypes, "get_last_error", last_error, raising=False)
    observer = supervisor.ResidualObserver(job)
    monkeypatch.setattr(supervisor, "ResidualObserver", lambda _: observer)
    result = supervisor.drain_naturally(job, [77], supervisor.time.monotonic() + 1)
    assert result["natural_empty"] is False
    assert result["errors"]
    assert result["initial_witnesses"] == [
        {
            "pid": 77,
            "image": None,
            "wait_result": wait_result,
            "exited": wait_result == 0,
            "raw_exit_code": 0 if wait_result == 0 else 259,
            "exit_code": 0 if wait_result == 0 else None,
            "error": {"operation": "QueryFullProcessImageNameW", "winerror": 31},
        }
    ]
    assert calls == ["wait", "exit", "image", "GetLastError", "close"]
    assert not observer.handles
