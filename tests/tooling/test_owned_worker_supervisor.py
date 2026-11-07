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
