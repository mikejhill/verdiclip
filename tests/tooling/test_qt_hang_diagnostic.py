"""Portable safety checks; no debugger or native process is started."""

import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock

import pytest
from scripts import qt_hang_diagnostic as diagnostic
from scripts.panel_owned_job import OwnedJob


def test_fixed_prefix_and_original_sources() -> None:
    """Check the fixed diagnostic safety boundary."""
    nodes = diagnostic.validate_inputs(diagnostic.ROOT)
    assert len(nodes) == 157
    assert nodes[-1].endswith("::test_deleted_facade_keeps_pending_worker_owned")


def test_stack_only_noninvasive_command(tmp_path: Path) -> None:
    """Check the fixed diagnostic safety boundary."""
    command = diagnostic.debugger_command(diagnostic.SDK_PATHS[0], 1234, tmp_path)
    assert command[1:5] == ["-pv", "-p", "1234", "-sins"]
    assert command[-2:] == ["-c", "~* kn 40; qd"]
    assert "-netsyms:no" in command
    assert "-noshell" in command
    assert str(tmp_path / "empty-symbols") in command


@pytest.mark.parametrize("pid", [True, 0, -1, 2**32, "123"])
def test_invalid_pid_never_constructs_attach(pid: object, tmp_path: Path) -> None:
    """Check the fixed diagnostic safety boundary."""
    with pytest.raises(ValueError, match=r"invalid|unexpected"):
        diagnostic.debugger_command(diagnostic.SDK_PATHS[0], cast("int", pid), tmp_path)


def test_no_arbitrary_debugger(tmp_path: Path) -> None:
    """Check the fixed diagnostic safety boundary."""
    with pytest.raises(ValueError, match=r"invalid|unexpected"):
        diagnostic.debugger_command("cdb.exe", 123, tmp_path)


def test_missing_debugger_skips_without_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Check the fixed diagnostic safety boundary."""
    preflight = tmp_path / "preflight.json"
    preflight.write_text(json.dumps({"status": "skipped"}))
    monkeypatch.setattr(diagnostic, "new_job", lambda: pytest.fail("must not launch"))
    assert diagnostic.supervise(tmp_path, tmp_path, preflight) == 0
    assert json.loads((tmp_path / "receipt.json").read_text())["status"] == "skipped"


def test_bounded_identity_json(tmp_path: Path) -> None:
    """Check the fixed diagnostic safety boundary."""
    path = tmp_path / "identity.json"
    path.write_text(" " * 4097)
    with pytest.raises(ValueError, match="bound"):
        diagnostic.read_json(path, 4096)


@pytest.mark.parametrize(
    "change",
    [
        {"epoch": "wrong"},
        {"pid": True},
        {"pid": 0},
        {"extra": 1},
        {"executable": "other"},
    ],
)
def test_child_identity_must_match(change: dict[str, Any]) -> None:
    """Check the fixed diagnostic safety boundary."""
    value = {
        "epoch": "session",
        "pid": 123,
        "executable": getattr(
            diagnostic.sys, "_base_executable", diagnostic.sys.executable
        ),
    }
    value.update(change)
    with pytest.raises(ValueError, match=r"invalid|unexpected"):
        diagnostic.checked_ready(value, "session")


def test_branch_only_no_ordinary_ci_dispatch() -> None:
    """Check the fixed diagnostic safety boundary."""
    workflow = (
        diagnostic.ROOT / ".github/workflows/qt-hang-diagnostic.yml"
    ).read_text()
    assert "branches: [diagnostic/qt6113-owned-stacks]" in workflow
    assert "pull_request:" not in workflow
    assert "workflow_dispatch:" not in workflow
    assert 'python-version: "3.13.17"' in workflow
    assert "timeout-minutes: 2" in workflow


class Clock:
    """Deterministic nonblocking watchdog clock."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        """Read virtual elapsed time."""
        return self.now

    def sleep(self, seconds: float) -> None:
        """Advance rather than sleeping."""
        self.now += seconds


@pytest.mark.parametrize("stuck", [False, True])
def test_cleanup_terminates_both_before_polling_and_shares_deadline(
    monkeypatch: pytest.MonkeyPatch, *, stuck: bool
) -> None:
    """No per-owner wait or unrelated process termination is permitted."""
    clock = Clock()
    monkeypatch.setattr(diagnostic.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(diagnostic.time, "sleep", clock.sleep)
    events = []
    owners = []
    for name in ("debugger", "pytest"):
        owner = Mock()
        owner.handle = name
        owner.processes = [name + "-child"]
        owner.api.TerminateJobObject.side_effect = lambda handle, _code: (
            events.append(("terminate", handle)) or 1
        )
        owner.api.CloseHandle.return_value = 1
        owner.pids.side_effect = lambda: (
            events.append(("poll", "")) or ([123] if stuck else [])
        )
        owner.wait.return_value = None if stuck else 0
        owners.append((name, owner))
    result = diagnostic.retire(
        cast("list[tuple[str, OwnedJob]]", cast("object", owners)), 0.03
    )
    assert events[:2] == [("terminate", "debugger"), ("terminate", "pytest")]
    assert clock.now <= 0.04
    assert all(value["confirmed"] is not stuck for value in result.values())
    for _, owner in owners:
        assert owner.api.CloseHandle.call_count == 2
        assert all(call.args[1] == 0 for call in owner.wait.call_args_list)


def test_cleanup_close_failure_never_confirms(monkeypatch: pytest.MonkeyPatch) -> None:
    """A natural empty job does not hide failed handle cleanup."""
    clock = Clock()
    monkeypatch.setattr(diagnostic.time, "monotonic", clock.monotonic)
    owner = Mock(handle=1, processes=[2])
    owner.api.TerminateJobObject.return_value = 1
    owner.pids.return_value = []
    owner.wait.return_value = 0
    owner.api.CloseHandle.side_effect = [0, 1]
    result = diagnostic.retire([("pytest", owner)], 1.0)
    assert result["pytest"]["confirmed"] is False
    assert result["pytest"]["errors"] == ["CloseHandle failed"]
    assert owner.api.CloseHandle.call_count == 2


def test_cleanup_post_close_deadline_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Slow final close cannot cross cleanup admission unnoticed."""
    clock = Clock()
    monkeypatch.setattr(diagnostic.time, "monotonic", clock.monotonic)
    owner = Mock(handle=1, processes=[])
    owner.api.TerminateJobObject.return_value = 1
    owner.pids.return_value = []
    owner.api.CloseHandle.side_effect = lambda _handle: clock.sleep(1.0) or 1
    result = diagnostic.retire([("pytest", owner)], 1.0)
    assert result["pytest"]["confirmed"] is False
    assert result["pytest"]["deadline_exceeded"] is True


@pytest.mark.parametrize(
    ("scenario", "expected", "debug_starts"),
    [
        ("early", 0, 0),
        ("timeout", 1, 1),
        ("debug-fail", 1, 1),
        ("launch-expired", 1, 0),
        ("target-exited", 1, 0),
        ("attached-finish", 0, 1),
        ("debug-timeout", 1, 1),
        ("debug-launch-expired", 1, 1),
        ("cleanup-failed", 1, 0),
        ("late-hash", 1, 0),
        ("late-receipt", 1, 0),
        ("late-debug-wait", 1, 1),
        ("late-debug-membership", 1, 1),
    ],
)
def test_supervisor_deadline_and_attachment_lifecycle(  # noqa: C901, PLR0915 - fixed adversarial lifecycle table
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    expected: int,
    debug_starts: int,
) -> None:
    """Exercise control flow without launching native processes or Qt."""
    clock = Clock()
    monkeypatch.setattr(diagnostic.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(diagnostic.time, "sleep", lambda _seconds: clock.sleep(0.5))
    monkeypatch.setattr(diagnostic, "validate_inputs", lambda _source: [])
    hashes = []

    def hash_file(_path: Path) -> str:
        hashes.append(1)
        if len(hashes) == 2 and scenario == "late-hash":
            clock.now = 15.0
        return "hash"

    monkeypatch.setattr(diagnostic, "digest", hash_file)
    original_write = diagnostic.write_json

    def write_receipt(path: Path, value: object) -> None:
        if scenario == "late-receipt" and path.name == "receipt.json":
            clock.now = max(clock.now, 15.0)
        original_write(path, value)

    monkeypatch.setattr(diagnostic, "write_json", write_receipt)
    preflight = tmp_path / "preflight.json"
    preflight.write_text(
        json.dumps(
            {
                "status": "available",
                "path": diagnostic.SDK_PATHS[0],
                "sha256": "hash",
                "signature_status": "Valid",
                "machine": "AMD64",
            }
        )
    )
    owners = []
    starts = []
    for name in ("pytest", "debugger"):
        owner = Mock(handle=name, processes=[name + "-handle"])
        owner.terminated = False
        owners.append(owner)
    target, debugger = owners

    def target_start(command: list[str], _cwd: Path) -> tuple[int, int]:
        starts.append("pytest")
        diagnostic.write_json(
            tmp_path / "ready.json",
            {
                "epoch": command[-1],
                "pid": 123,
                "executable": getattr(
                    diagnostic.sys, "_base_executable", diagnostic.sys.executable
                ),
            },
        )
        if scenario == "launch-expired":
            clock.now = 15.0
        return 12, 13

    def debugger_start(_command: list[str], _cwd: Path) -> tuple[int, int]:
        starts.append("debugger")
        if scenario == "debug-launch-expired":
            clock.now = 15.0
        return 14, 15

    target.start.side_effect = target_start
    debugger.start.side_effect = debugger_start
    ending = (
        1.0
        if scenario in {"early", "cleanup-failed"}
        else 10.0
        if scenario == "attached-finish"
        else 100.0
    )
    target.wait.side_effect = lambda _handle, _seconds: (
        0 if target.terminated or clock.now >= ending else None
    )
    target.pids.side_effect = lambda: (
        [] if target.terminated or clock.now >= ending else [123]
    )
    debugger.wait.side_effect = lambda _handle, _seconds: (
        7 if scenario == "debug-fail" and not debugger.terminated else 0
    )
    if scenario == "debug-timeout":
        debugger.wait.side_effect = lambda _handle, _seconds: (
            0 if debugger.terminated else None
        )
    debugger.pids.return_value = []
    delayed = []

    def delay_once() -> None:
        if not delayed:
            clock.sleep(3.0)
            delayed.append(True)

    if scenario == "late-debug-wait":
        debugger.wait.side_effect = lambda _handle, _seconds: delay_once() or 0
    if scenario == "late-debug-membership":
        debugger.pids.side_effect = lambda: delay_once() or []
    for owner in owners:
        owner.api.CloseHandle.return_value = 1
        owner.api.TerminateJobObject.side_effect = lambda _handle, _code, owner=owner: (
            setattr(owner, "terminated", True) or 1
        )
    if scenario == "cleanup-failed":
        target.api.CloseHandle.return_value = 0
    monkeypatch.setattr(diagnostic, "new_job", Mock(side_effect=owners))
    observer = Mock()
    observer.snapshot.return_value = {
        "exited": scenario == "target-exited",
        "image_status": "available",
        "image": getattr(diagnostic.sys, "_base_executable", diagnostic.sys.executable),
    }
    observer.close.return_value = []
    monkeypatch.setattr(diagnostic, "new_observer", Mock(return_value=observer))
    result = diagnostic.supervise(tmp_path, tmp_path, preflight)
    receipt = json.loads((tmp_path / "receipt.json").read_text())
    assert result == expected
    assert starts.count("pytest") == 1
    assert starts.count("debugger") == debug_starts
    assert receipt["cleanup_confirmed"] is (scenario != "cleanup-failed")
    assert clock.now <= 15.5
    if expected:
        assert receipt["failure"]
    assert all(owner.api.CloseHandle.call_count == 2 for owner in owners)
