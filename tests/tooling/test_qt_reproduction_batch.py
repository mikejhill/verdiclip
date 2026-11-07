"""Portable batch admission/ownership controls, never native launches."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from scripts import qt_reproduction_batch as batch


@pytest.fixture
def evidence(tmp_path: Path) -> tuple[Path, list[str]]:
    """Produce a complete deterministic inner result through the real ledger."""
    p = batch.proof
    nodes = p.validate_inputs(p.ROOT)
    epoch = "a" * 32
    ledger = p.TargetLedger(nodes, epoch, tmp_path)
    for index, node in enumerate(nodes):
        stamp = index / 100
        ledger.start(node, stamp)
        for phase in ("setup", "call", "teardown"):
            ledger.report(node, phase, "passed", stamp)
    coverage = (
        "FAIL Required test coverage of 90.0% not reached. Total coverage: 46.67%"
    )
    result = {
        "epoch": epoch,
        "exit_code": 1,
        "target_phases_passed": True,
        "coverage_failure": coverage,
    }
    p.write_json(tmp_path / "pytest-exit.json", result)
    (tmp_path / "pytest.log").write_text(coverage)
    selected = nodes + [f"other{i}" for i in range(3691 - len(nodes))]
    p.write_json(
        tmp_path / "full-collection.json",
        {
            "epoch": epoch,
            "source_sha256": ledger.source_sha256,
            "discovered": 3696,
            "excluded": sorted(p.FullCollection.EXCLUDED),
            "selected": selected,
            "original_nextitem": selected[157],
            "historical_windows_suffix_verified": False,
        },
    )
    p.write_json(tmp_path / "collected.json", nodes)
    p.write_json(
        tmp_path / "runtime.json",
        {
            "python": [3, 13, 17],
            "pyside": "6.11.3",
            "qt": "6.11.3",
            "qpa_override": None,
        },
    )
    p.write_json(
        tmp_path / "receipt.json",
        {
            "epoch": epoch,
            "base": p.BASE,
            "seed": p.SEED,
            "status": "non-reproduction",
            "failure": None,
            "target_reached": True,
            "target_outcome": "passed",
            "cleanup_confirmed": True,
            "native_stack_available": False,
            "debugger_intervention": False,
            "collection_scope": "full-collection-prefix-execution",
            "exit_code": 1,
            "cleanup": {
                "pytest": {
                    "confirmed": True,
                    "errors": [],
                    "remaining_pids": [],
                    "deadline_exceeded": False,
                    "direct_exits": [1],
                }
            },
            "inspection_close_errors": [],
            "target_receipt_sha256": p.digest(tmp_path / "target.json"),
            "pytest_exit": result,
        },
    )
    return tmp_path, nodes


def test_exact_coverage_nonreproduction_admitted_once(
    evidence: tuple[Path, list[str]],
) -> None:
    """Coverage-only failure is retained and cannot replay its session."""
    path, nodes = evidence
    epochs: set[str] = set()
    result = batch.admit(path, 1, nodes, epochs)
    assert result["process_exit"] == 1
    assert "46.67" in result["coverage_failure"]
    with pytest.raises(ValueError, match="session"):
        batch.admit(path, 1, nodes, epochs)


@pytest.mark.parametrize(
    "change",
    [
        "exit",
        "runtime",
        "collection",
        "journal",
        "cleanup",
        "coverage",
        "extra-error",
        "missing",
    ],
)
def test_unexpected_result_never_admitted(
    evidence: tuple[Path, list[str]], change: str
) -> None:
    """No broad nonzero/log match can stand in for exact successful phases."""
    path, nodes = evidence
    if change == "missing":
        (path / "target-done.json").unlink()
    elif change == "extra-error":
        with (path / "pytest.log").open("a") as stream:
            stream.write("\nINTERNALERROR> failure")
    elif change == "journal":
        with (path / "phases.jsonl").open("ab") as stream:
            stream.write(b"{}\n")
    else:
        name = {"runtime": "runtime", "collection": "full-collection"}.get(
            change, "receipt"
        )
        value = json.loads((path / f"{name}.json").read_text())
        if change == "exit":
            value["exit_code"] = 3
        elif change == "runtime":
            value["qt"] = "6.12.0"
        elif change == "collection":
            value["selected"][0] = "wrong"
        elif change == "cleanup":
            value["cleanup_confirmed"] = False
        else:
            value["pytest_exit"]["coverage_failure"] = "unrelated"
        batch.proof.write_json(path / f"{name}.json", value)
    with pytest.raises((ValueError, FileNotFoundError)):
        batch.admit(path, 1, nodes, set())


@pytest.mark.parametrize("failure_at", [None, 0, 3])
def test_batch_stops_without_restarting_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_at: int | None
) -> None:
    """At most ten independent owners, with no attempt after first failure."""
    jobs = []

    def factory() -> Mock:
        job = Mock()
        job.start.return_value = (1, 2)
        job.processes = [2]
        job.handle = 3
        job.wait.return_value = 1
        job.pids.return_value = []
        jobs.append(job)
        return job

    calls = []

    def admit(*_: object) -> dict[str, str]:
        index = len(calls)
        calls.append(index)
        if index == failure_at:
            raise ValueError("unexpected outcome")
        return {"epoch": str(index)}

    monkeypatch.setattr(batch.proof, "validate_inputs", lambda _: [])
    monkeypatch.setattr(batch.proof, "new_job", factory)
    monkeypatch.setattr(batch, "admit", admit)
    monkeypatch.setattr(batch.time, "monotonic", lambda: 0.0)
    cleanup = Mock(return_value={"supervisor": {"confirmed": True}})
    monkeypatch.setattr(batch.proof, "retire", cleanup)
    assert batch.run(tmp_path, tmp_path / "batch") == 1
    assert len(jobs) == (10 if failure_at is None else failure_at + 1)
    assert cleanup.call_count == (0 if failure_at is None else 1)
    assert len(list((tmp_path / "batch").glob("*/batch-verdict.json"))) == len(jobs)
    for job in jobs:
        job.start.assert_called_once()


@pytest.mark.parametrize("phase", ["wait", "read", "close"])
def test_late_evidence_cannot_admit_next_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    """Recheck absolute51s after each potentially slow admission operation."""
    clock = [0.0]
    job = Mock()
    job.start.return_value = (1, 2)
    job.processes = [2]
    job.handle = 3
    job.pids.return_value = []

    def expire() -> int:
        clock[0] = 51.0
        return 1

    job.wait.return_value = 1
    if phase == "wait":
        job.wait.side_effect = lambda *_: expire()
    if phase == "close":
        job.api.CloseHandle.side_effect = lambda *_: expire()

    def admit(*_: object) -> dict[str, str]:
        if phase == "read":
            expire()
        return {}

    factory = Mock(return_value=job)
    monkeypatch.setattr(batch.proof, "new_job", factory)
    monkeypatch.setattr(batch.proof, "validate_inputs", lambda _: [])
    monkeypatch.setattr(batch.proof, "retire", lambda *_: {})
    monkeypatch.setattr(batch, "admit", admit)
    monkeypatch.setattr(batch.time, "monotonic", lambda: clock[0])
    assert batch.run(tmp_path, tmp_path / "batch") == 1
    factory.assert_called_once()


@pytest.mark.parametrize("elapsed", [51.0, 540.0])
def test_source_validation_expiry_prevents_native_job_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    elapsed: float,
) -> None:
    """A source read crossing either absolute boundary cannot begin native work."""
    clock = [0.0]
    calls = [0]

    def validate(_: Path) -> list[str]:
        calls[0] += 1
        if calls[0] == 3:
            clock[0] = elapsed
        return []

    factory = Mock(side_effect=AssertionError("native creation forbidden"))
    monkeypatch.setattr(batch.proof, "validate_inputs", validate)
    monkeypatch.setattr(batch.proof, "new_job", factory)
    monkeypatch.setattr(batch.time, "monotonic", lambda: clock[0])
    assert batch.run(tmp_path, tmp_path / "batch") == 1
    factory.assert_not_called()
    verdict = json.loads((tmp_path / "batch/attempt-01/batch-verdict.json").read_text())
    assert verdict["continued"] is False
    assert "deadline" in verdict["failure"]
    assert verdict["forced_cleanup"] is False
