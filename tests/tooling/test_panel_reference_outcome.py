"""Failure retention cannot turn a failed or incomplete helper into success."""

import json
from pathlib import Path
from typing import Any

import pytest
from scripts import panel_reference_outcome as outcome
from scripts.panel_reference_outcome import child_failure, retain_failure


def test_original_failure_and_all_supervisor_errors_survive(tmp_path: Path) -> None:
    """Missing final guard and failed cleanup remain independent of root cause."""
    folder = tmp_path / "reference"
    folder.mkdir()
    (folder / "readiness.json").write_text(
        json.dumps(
            {
                "root_rect": [0, 0, 10, 10],
                "observations": [],
                "first_failure": "Predicted calibration pixels differ",
            }
        )
    )
    records = [
        {"event": "cleanup_error", "operation": "close failed"},
        {"event": "call_end", "operation": "reference.source.close"},
    ]
    result = retain_failure(
        tmp_path,
        {"allowed_rect": [0, 0, 10, 10]},
        records,
        1,
        "Reference final guard/cleanup evidence missing",
    )
    assert result == "Predicted calibration pixels differ"
    saved = json.loads((tmp_path / "reference-outcome.json").read_text())
    assert (
        saved["supervisor_failure"] == "Reference final guard/cleanup evidence missing"
    )
    assert saved["cleanup_errors"] == ["close failed"]
    assert saved["exit_code"] == 1
    assert saved["final_guard_end_recorded"] is False
    assert saved["success_admitted"] is False


@pytest.mark.parametrize("payload", ["", "{", "{}", '{"root_rect":[0,0,10,10]}'])
def test_malformed_or_incomplete_readiness_rejected(
    tmp_path: Path, payload: str
) -> None:
    """Malformed and schema-incomplete files never replace supervisor evidence."""
    path = tmp_path / "readiness.json"
    path.write_text(payload)
    failure, _status = child_failure(path, [0, 0, 10, 10], 1)
    assert failure is None


def test_running_or_missing_readiness_is_not_finalized(tmp_path: Path) -> None:
    """Only a completed child's finalized bounded JSON can supply root cause."""
    missing = tmp_path / "missing.json"
    failure, status = child_failure(missing, [0, 0, 10, 10], None)
    assert failure is None
    assert "not finalized" in status
    failure, status = child_failure(missing, [0, 0, 10, 10], 1)
    assert failure is None
    assert "unavailable" in status


def test_nonzero_exit_without_child_cause_stays_failure(tmp_path: Path) -> None:
    """No readiness is not evidence of success, even with a clean call log."""
    result = retain_failure(tmp_path, {"allowed_rect": [0, 0, 10, 10]}, [], 7, None)
    assert result == "Reference helper failed with exit 7"
    assert (
        json.loads((tmp_path / "reference-outcome.json").read_text())[
            "success_admitted"
        ]
        is False
    )


def test_recursive_readiness_cannot_mask_supervisor(tmp_path: Path) -> None:
    """Deep malformed JSON is diagnostic rejection, not a new primary crash."""
    folder = tmp_path / "reference"
    folder.mkdir()
    (folder / "readiness.json").write_text("[" * 2000 + "0" + "]" * 2000)
    result = retain_failure(
        tmp_path, {"allowed_rect": [0, 0, 10, 10]}, [], 1, "native timeout"
    )
    assert result == "native timeout"


def test_failed_outcome_write_preserves_supervisor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An I/O failure while retaining diagnostics cannot mask initial rejection."""

    def fail(*_args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(outcome, "write", fail)
    result = outcome.retain_failure(
        tmp_path, {"allowed_rect": [0, 0, 10, 10]}, [], 0, "missing final guard"
    )
    assert result.startswith("missing final guard;")
    assert "outcome retention failed: OSError" in result


def test_cleanup_details_are_bounded(tmp_path: Path) -> None:
    """Retained summaries are bounded while the original call log stays separate."""
    records: list[dict[str, Any]] = [
        {"event": "cleanup_error", "operation": "x" * 10000} for _ in range(20)
    ]
    records.append({"event": "call_end", "operation": {"unknown": "value"}})
    retain_failure(
        tmp_path, {"allowed_rect": [0, 0, 10, 10]}, records, 1, "cleanup failed"
    )
    saved = json.loads((tmp_path / "reference-outcome.json").read_text())
    assert len(saved["cleanup_errors"]) == outcome.MAX_CLEANUP_ERRORS
    assert all(
        len(value) == outcome.MAX_FAILURE_LENGTH for value in saved["cleanup_errors"]
    )
