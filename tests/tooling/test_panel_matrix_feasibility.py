"""Matrix orchestration stays owned, isolated, and honest about raw evidence."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from scripts import panel_feasibility
from scripts import panel_matrix_feasibility as matrix
from scripts.panel_fixture_matrix import MatrixCase
from scripts.panel_owned_job import OwnedJob
from scripts.panel_probe_oracle import Pixels


def test_acquisition_has_no_oracle_geometry() -> None:
    """DOM extents and body crop never cross into the capture manifest."""
    owner = {"root_hwnd": 12, "pid": 5, "allowed_rect": [0, 0, 800, 600]}
    manifest = matrix.acquisition_manifest(owner, [20, 30, 340, 340], [5, 6])
    assert set(manifest) == {
        "root_hwnd",
        "pid",
        "allowed_rect",
        "kind",
        "owned_process_ids",
        "click",
    }
    assert manifest["click"] == [180, 185]


def test_entry_border_outside_owned_window_rejected() -> None:
    """A fixture entry point is not permission to capture outside its window."""
    with pytest.raises(RuntimeError, match="wholly"):
        matrix.acquisition_manifest(
            {"allowed_rect": [0, 0, 10, 10]}, [2, 2, 20, 20], [1]
        )


def test_only_two_representative_cases() -> None:
    """The initial batch is intentionally not the full future matrix."""
    assert set(matrix.CASES) == {"conversation-light100", "table-dark125"}


def test_first_failure_and_cleanup_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failed launch still closes both owned jobs and the fixture server."""
    closed = []

    class Job:
        def close(self) -> dict[str, bool]:
            closed.append("job")
            return {"confirmed": True}

    class Server:
        def __init__(self, *_args: object) -> None:
            pass

        def close(self) -> None:
            closed.append("server")

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("Original launch failure")

    monkeypatch.setattr(matrix, "NativeWindow", lambda: None)
    monkeypatch.setattr(matrix, "OwnedJob", Job)
    monkeypatch.setattr(matrix, "FixtureServer", Server)
    monkeypatch.setattr(matrix, "launch", fail)
    with pytest.raises(RuntimeError, match="session failed"):
        matrix.session(MatrixCase("conversation"), "1", tmp_path / "case")
    receipt = json.loads((tmp_path / "case/receipt.json").read_text())
    assert "Original launch failure" in receipt["first_failure"]
    assert closed == ["job", "job", "server"]
    assert receipt["product_capture_qualified"] is False


def test_raw_completes_before_reference_planning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raw failure cannot trigger reference renders or oracle-derived inputs."""
    called = []

    def fail(*_args: object) -> dict[str, Any]:
        called.append("raw")
        raise RuntimeError("raw failed")

    def forbidden(*_args: object) -> None:
        pytest.fail("Reference planning ran before raw success")

    monkeypatch.setattr(matrix, "session", fail)
    monkeypatch.setattr(matrix, "reference_offsets", forbidden)
    with pytest.raises(RuntimeError, match="raw failed"):
        matrix.collect_batch("conversation-light100", tmp_path, {}, 0)
    assert called == ["raw"]


def reference_log() -> list[dict[str, Any]]:
    """Minimal complete guarded lifecycle with finite common-clock timestamps."""
    records: list[dict[str, Any]] = [{"event": "process", "monotonic": 0.0}]
    for index, operation in enumerate(
        ["reference.setup", "reference.final_guard", "reference.source.close"]
    ):
        records.extend(
            [
                {
                    "event": "call_start",
                    "operation": operation,
                    "monotonic": index * 0.2 + 0.1,
                },
                {
                    "event": "call_end",
                    "operation": operation,
                    "monotonic": index * 0.2 + 0.2,
                },
            ]
        )
    return records


@pytest.mark.parametrize(
    "fault", ["slow", "cleanup", "missing_end", "wrong_pair", "late_setup", "nonfinite"]
)
def test_completed_reference_log_rejections(fault: str) -> None:
    """Zero exit cannot excuse overdue calls, cleanup failures or invalid evidence."""
    records = reference_log()
    now = 1.0
    if fault == "slow":
        for record in records[2:]:
            record["monotonic"] += 3
        now = 4
    elif fault == "cleanup":
        records.append(
            {"event": "cleanup_error", "operation": "failure", "monotonic": 0.8}
        )
    elif fault == "missing_end":
        records.pop()
    elif fault == "wrong_pair":
        records[2]["operation"] = "other"
    elif fault == "late_setup":
        for record in records[1:]:
            record["monotonic"] += 3
        now = 4
    else:
        records[1]["monotonic"] = float("nan")
    with pytest.raises(RuntimeError, match=r"timed out|pairing|incomplete|invalid"):
        matrix.validate_reference_log(records, 0, now, finished=True)


def test_completed_reference_log_valid() -> None:
    """A complete on-time guarded lifecycle remains admissible."""
    matrix.validate_reference_log(reference_log(), 0, 1, finished=True)


@pytest.mark.parametrize("last_clock", [1.1, 30.0])
def test_reference_zero_exit_final_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, last_clock: float
) -> None:
    """The final clock check happens after complete-log admission, before return."""

    class Job:
        def start(self, *_args: object) -> tuple[int, int]:
            return (1, 2)

        def wait(self, *_args: object) -> int:
            return 0

    times = iter([0.0, 1.0, last_clock])
    monkeypatch.setattr(matrix.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(matrix, "events", lambda _path: reference_log())
    if last_clock >= 30:
        with pytest.raises(RuntimeError, match="total deadline"):
            matrix.run_reference(cast("OwnedJob", Job()), {}, tmp_path)
    else:
        result = matrix.run_reference(cast("OwnedJob", Job()), {}, tmp_path)
        assert result["exit_code"] == 0


def test_entry_readiness_uses_bounded_root_only_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Entry location consumes a helper raster with no oracle DOM input."""
    sent = []

    def capture(
        _job: OwnedJob, manifest: dict[str, Any], _output: Path
    ) -> dict[str, int]:
        sent.append(manifest)
        return {"exit_code": 0}

    monkeypatch.setattr(matrix, "run_reference", capture)
    monkeypatch.setattr(
        matrix, "load_pixels", lambda _path: Pixels(2, 2, matrix.BORDER * 4)
    )
    receipt: dict[str, Any] = {
        "acquisition": {
            "root_hwnd": 12,
            "pid": 1,
            "allowed_rect": [0, 0, 2, 2],
            "kind": "owned-edge",
            "owned_process_ids": [1],
        },
        "oracle_only": {},
    }
    matrix.prepare_acquisition(cast("OwnedJob", object()), tmp_path, receipt)
    assert sent[0]["capture_mode"] == "entry-readiness"
    assert "state_directory" not in sent[0]
    assert "content_rect" not in sent[0]
    assert receipt["acquisition"]["click"] == [1, 1]


def test_matrix_calls_real_browser_experiment_routing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise actual run_probe allowlist; fake only native execution/verification."""
    launched: list[list[str]] = []

    class Job:
        def start(self, arguments: list[str], _directory: Path) -> tuple[int, int]:
            launched.append(arguments)
            return 101, 202

        def wait(self, _handle: int, _milliseconds: int) -> int:
            return 0

    # No replacement of run_probe/probe_arguments: the old caller typo must fail
    # at the real fixed allowlist before any simulated helper launch.
    assert matrix.run_probe is panel_feasibility.run_probe
    monkeypatch.setattr(
        matrix, "prepare_acquisition", lambda _job, _output, _receipt: None
    )
    monkeypatch.setenv("PYTHONPATH", os.environ.get("PYTHONPATH", ""))
    monkeypatch.setattr(
        matrix, "verify_raw", lambda _output, _initial: {"verified": True}
    )
    receipt: dict[str, Any] = {
        "acquisition": {"kind": "owned-edge", "click": [50, 60], "pid": 123},
        "oracle_only": {"initial": {}},
    }
    matrix.collect_session(
        MatrixCase("conversation"), cast("OwnedJob", Job()), tmp_path, receipt
    )
    assert len(launched) == 1
    assert "--browser-sequence" in launched[0]
    assert "--settle-discovery" in launched[0]
    assert "--full-sweep" not in launched[0]
    assert receipt["helper"]["exit_code"] == 0
    assert receipt["raw_diagnostic"] == {"verified": True}


def test_actual_endpoint_763_2_is_not_rounded_to_763() -> None:
    """Authoritative settled endpoint is distinct from integer DOM extent math."""
    target = {
        "vertical": 119.2,
        "horizontal": 147.2,
        "viewport_width": 502,
        "viewport_height": 292,
        "extent_width": 1081,
        "extent_height": 1055,
        "rect": [44.4, 55.6, 564.4, 365.6],
        "border": [2, 2],
        "fractional_border": [1.6] * 4,
        "content_box": {"x": 0, "y": 0, "width": 502.4, "height": 292.8},
    }
    raw: dict[str, Any] = {
        "root_rect": [50, 50, 1000, 700],
        "initial": {
            "observer_ready": True,
            "dpr": 1.25,
            "state": {"target": target},
            "config": {
                "scenario": "table",
                "theme": "dark",
                "variant": "static",
                "width": 520,
            },
        },
    }
    observed = deepcopy(raw)
    observed["initial"].update(mode="scroll-reference", endpoint_limit=763.2)
    observed["initial"]["state"]["target"]["vertical"] = 763.2
    observed["initial"]["config"].update(reference_offset=None, reference_endpoint=True)
    matrix.verify_reference_identity(raw, observed, None)
    observed["initial"]["endpoint_limit"] = 763
    with pytest.raises(RuntimeError, match="actual settled endpoint"):
        matrix.verify_reference_identity(raw, observed, None)
