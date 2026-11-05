"""Mutation diagnostics retain failure evidence without changing test outcomes."""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from tests import mutation_support as support


@pytest.fixture(autouse=True)
def receipt_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """Direct hook tests model a successfully initialized statistics session."""
    monkeypatch.setattr(support, "_stats_receipt_ready", True)


@pytest.mark.parametrize("phase", [None, "stats", "some_mutant"])
@pytest.mark.parametrize("failed", [False, True])
def test_only_failed_statistics_reports_are_retained(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, phase: str | None, *, failed: bool
) -> None:
    """Ordinary tests and mutant verdicts do not acquire diagnostic IO."""
    path = tmp_path / "receipt.jsonl"
    monkeypatch.setattr(support, "STATS_FAILURES", path)
    if phase is None:
        monkeypatch.delenv("MUTANT_UNDER_TEST", raising=False)
    else:
        monkeypatch.setenv("MUTANT_UNDER_TEST", phase)
    report = cast(
        pytest.TestReport,
        SimpleNamespace(
            nodeid="test_example",
            when="call",
            failed=failed,
            longreprtext="actual failure",
        ),
    )
    support.pytest_runtest_logreport(report)
    assert path.exists() is (phase == "stats" and failed)
    if path.exists():
        data = json.loads(path.read_text())
        assert {key: data[key] for key in ("nodeid", "when", "failure")} == {
            "nodeid": "test_example",
            "when": "call",
            "failure": "actual failure",
        }


def test_collection_failure_and_bounded_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Collection errors are retained and repeated output remains bounded."""
    path = tmp_path / "receipt.jsonl"
    monkeypatch.setattr(support, "STATS_FAILURES", path)
    monkeypatch.setenv("MUTANT_UNDER_TEST", "stats")
    report = pytest.CollectReport(
        nodeid="module", outcome="failed", longrepr="x" * 100000, result=[]
    )
    support.pytest_collectreport(report)
    assert len(json.loads(path.read_text())["failure"]) == 8192
    path.write_text("x" * support.FAILURE_LIMIT)
    support.pytest_collectreport(report)
    assert path.stat().st_size == support.FAILURE_LIMIT


def test_receipt_io_failure_does_not_replace_original_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unwritable optional destination does not throw over a pytest failure."""
    monkeypatch.setenv("MUTANT_UNDER_TEST", "stats")
    monkeypatch.setattr(
        support, "STATS_FAILURES", tmp_path / "missing" / "receipt.jsonl"
    )
    report = cast(
        pytest.TestReport,
        SimpleNamespace(
            nodeid="test", when="call", failed=True, longreprtext="original"
        ),
    )
    support.retain_stats_failure(report)
    assert report.failed


def test_real_pytest_failure_keeps_exit_code_and_child_receipt(tmp_path: Path) -> None:
    """The actual public pytest hook persists a failure outside captured stdout."""
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "test_example.py").write_text(
        "def test_failure():\n    assert False, 'statistics-proof'\n"
    )
    environment = os.environ.copy()
    environment.update(
        MUTANT_UNDER_TEST="stats",
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        PYTHONPATH=str(Path(__file__).resolve().parents[2]),
        PYTEST_ADDOPTS="",
        GITHUB_WORKSPACE=str(tmp_path),
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "tests.mutation_support",
            "-c",
            "pytest.ini",
            "test_example.py",
            "-q",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    receipt = json.loads((tmp_path / "mutation-stats-failures.jsonl").read_text())
    assert receipt["nodeid"] == "test_example.py::test_failure"
    assert receipt["when"] == "call"
    assert "statistics-proof" in receipt["failure"]


def test_new_statistics_session_clears_previous_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Cached diagnostic text is never represented as a new session failure."""
    path = tmp_path / "receipt.jsonl"
    path.write_text("previous failure")
    monkeypatch.setattr(support, "STATS_FAILURES", path)
    monkeypatch.setenv("MUTANT_UNDER_TEST", "stats")
    support.pytest_sessionstart()
    assert path.read_text() == ""


def test_failed_reset_disables_appending(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Failure to establish a new receipt must not mix old and new evidence."""
    monkeypatch.setattr(support, "STATS_FAILURES", tmp_path / "missing" / "receipt")
    monkeypatch.setenv("MUTANT_UNDER_TEST", "stats")
    support.pytest_sessionstart()
    assert not support._stats_receipt_ready


def test_failure_formatting_cannot_replace_original_outcome(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A broken exception renderer remains optional evidence, not an internal error."""

    class Broken:
        failed = True
        nodeid = "test"
        when = "call"

        @property
        def longreprtext(self) -> str:
            raise RuntimeError("renderer failed")

    monkeypatch.setenv("MUTANT_UNDER_TEST", "stats")
    path = tmp_path / "receipt"
    monkeypatch.setattr(support, "STATS_FAILURES", path)
    support.retain_stats_failure(cast(pytest.TestReport, Broken()))
    assert not path.exists()


def test_receipt_is_not_restored_mutant_cache() -> None:
    """Workflow uploads the current-workspace receipt, not a restored cached file."""
    workflow = (
        Path(__file__).resolve().parents[2] / ".github/workflows/mutation.yml"
    ).read_text()
    assert "            mutation-stats-failures.jsonl" in workflow
    assert "mutants/mutation-stats-failures.jsonl" not in workflow
