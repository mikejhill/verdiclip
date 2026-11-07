"""Fixed original matrix routing/provenance; no native calls in these tests."""

from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from scripts import panel_feasibility as generic
from scripts import panel_matrix_feasibility as matrix
from scripts import panel_matrix_raw_geometry as contract
from scripts import panel_uia_probe as probe
from scripts.panel_fixture_matrix import render


def initial() -> dict[str, Any]:
    """Authored requests are distinct from retained browser quantization."""
    return {
        "config": asdict(contract.CASE),
        "mode": "scroll",
        "dpr": 1.25,
        "observer_ready": True,
        "state": {"target": dict(contract.HISTORICAL), "outer": {}, "sibling": {}},
    }


def manifest() -> dict[str, Any]:
    """Create through real fixed-source validation, not self-attested fake hashes."""
    return contract.manifest(
        {}, initial(), contract.CASE, "1.25", render(contract.CASE)
    )


def test_original_sources_and_distinct_requested_observed() -> None:
    """Reject mismatched evidence without changing native admission."""
    value = manifest()
    assert value["kind"] == "owned-matrix"
    assert value["matrix_provenance"]["generated_sha256"] == contract.HTML_SHA
    assert value["matrix_provenance"]["requested_initial_scroll"] == {
        "vertical": 119,
        "horizontal": 147,
    }
    assert value["observed_initial_scroll"] == contract.HISTORICAL
    assert value["observed_initial_scroll"] != contract.REQUESTED
    assert "rect" not in value


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kind", "owned-edge"),
        ("fixture_schema", "edge.html"),
        ("dpr", 1),
        ("browser_scale_flag", "1"),
        ("matrix_provenance", {}),
        ("observed_initial_scroll", contract.REQUESTED),
    ],
)
def test_probe_rejects_wrong_manifest_before_native(field: str, value: object) -> None:
    """Reject mismatched evidence without changing native admission."""
    fixture = manifest()
    fixture[field] = value
    instance = Mock()
    instance.report = {}
    with pytest.raises(RuntimeError, match="original matrix"):
        probe.Probe.read_raw_geometry(instance, fixture, Mock(), Mock(), 0)
    instance.validate.assert_not_called()
    instance.emit.assert_not_called()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("scenario", "conversation"),
        ("width", 519),
        ("theme", "light"),
        ("reference_mode", "translate"),
        ("reference_offset", 0),
        ("reference_endpoint", True),
        ("variant", "sticky"),
    ],
)
def test_wrong_case_rejected_before_readiness_or_probe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    field: str,
    value: str | int | bool,  # noqa: FBT001 - parametrized malformed field
) -> None:
    """Reject mismatched evidence without changing native admission."""
    case = replace(contract.CASE, **{field: value})
    readiness, run = Mock(), Mock()
    monkeypatch.setattr(matrix, "prepare_acquisition", readiness)
    monkeypatch.setattr(matrix, "run_probe", run)
    with pytest.raises(RuntimeError, match="provenance"):
        matrix.collect_raw_geometry(
            case,
            "1.25",
            render(case),
            Mock(),
            tmp_path,
            {"acquisition": {}, "oracle_only": {"initial": initial()}},
        )
    readiness.assert_not_called()
    run.assert_not_called()


@pytest.mark.parametrize(
    "mutation", ["config", "mode", "dpr", "vertical", "horizontal", "html"]
)
def test_wrong_oracle_or_asset_never_reaches_probe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: str,
) -> None:
    """Reject mismatched evidence without changing native admission."""
    observed = initial()
    html = render(contract.CASE)
    if mutation in ("vertical", "horizontal"):
        observed["state"]["target"][mutation] += 0.00001
    elif mutation == "config":
        observed["config"]["reference_offset"] = 0
    elif mutation == "html":
        html += " "
    else:
        observed[mutation] = "reference" if mutation == "mode" else 1
    run = Mock()
    monkeypatch.setattr(matrix, "run_probe", run)
    with pytest.raises(RuntimeError):
        matrix.collect_raw_geometry(
            contract.CASE,
            "1.25",
            html,
            Mock(),
            tmp_path,
            {"acquisition": {}, "oracle_only": {"initial": observed}},
        )
    run.assert_not_called()


def test_fixed_source_hash_cannot_be_self_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject mismatched evidence without changing native admission."""
    original = Path.read_bytes

    def read(path: Path) -> bytes:
        return b"changed" if path.name == "matrix.html" else original(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    with pytest.raises(RuntimeError, match="provenance changed"):
        contract.provenance()


@pytest.mark.parametrize("drift", ["none", "before", "after", "root", "slow"])
def test_one_read_only_probe_and_independent_full_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    drift: str,
) -> None:
    """Reject mismatched evidence without changing native admission."""
    observed = initial()
    changed = deepcopy(observed)
    changed["state"]["target"]["vertical"] += 0.01
    receipt = {"acquisition": {}, "oracle_only": {"initial": observed}}
    monkeypatch.setattr(matrix, "prepare_acquisition", Mock())
    run, reference = (
        Mock(return_value={"exit_code": 0, "seconds": 1, "timed_out": False}),
        Mock(return_value={}),
    )
    monkeypatch.setattr(matrix, "run_probe", run)
    monkeypatch.setattr(matrix, "run_reference", reference)
    verify = Mock()
    monkeypatch.setattr(matrix, "verify_diagnostic", verify)
    monkeypatch.setattr(
        matrix,
        "events",
        Mock(
            return_value=[
                {"event": "call_end", "seconds": 3 if drift == "slow" else 0.1}
            ]
        ),
    )
    monkeypatch.setattr(
        matrix,
        "snapshots",
        Mock(
            side_effect=[
                [changed if drift == "before" else observed],
                [changed if drift == "after" else observed],
            ]
        ),
    )
    monkeypatch.setattr(
        matrix,
        "load_pixels",
        Mock(side_effect=[b"root", b"changed" if drift == "root" else b"root"]),
    )
    if drift == "none":
        matrix.collect_raw_geometry(
            contract.CASE, "1.25", render(contract.CASE), Mock(), tmp_path, receipt
        )
        assert receipt["full_root_unchanged"] is True
    else:
        with pytest.raises(
            RuntimeError, match=r"pose drift|root pixels|native call duration"
        ):
            matrix.collect_raw_geometry(
                contract.CASE, "1.25", render(contract.CASE), Mock(), tmp_path, receipt
            )
    assert run.call_count == (0 if drift == "before" else 1)
    if drift == "slow":
        reference.assert_not_called()
        verify.assert_not_called()
    elif run.called:
        assert run.call_args.kwargs == {"experiment": "raw-geometry"}
        assert reference.call_args.args[1]["capture_mode"] == "entry-readiness"


def test_generic_edge_cli_has_no_raw_route(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject mismatched evidence without changing native admission."""
    monkeypatch.setattr(
        generic.sys,
        "argv",
        [
            "probe",
            "--case",
            "edge-125",
            "--output",
            "unused",
            "--raw-geometry-diagnostic",
        ],
    )
    with pytest.raises(SystemExit):
        generic.parse_arguments()


@pytest.mark.parametrize(
    "duration", [3, 3.001, -1, float("nan"), float("inf"), None, True, "1"]
)
def test_completed_native_duration_rejected(duration: object) -> None:
    """Zero exit cannot admit slow or malformed completed guard/COM calls."""
    with pytest.raises(RuntimeError, match="native call duration"):
        matrix.validate_raw_completion(
            {"exit_code": 0, "seconds": 4, "timed_out": False},
            [{"event": "call_end", "slot": 43, "seconds": duration}],
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("seconds", 30),
        ("seconds", 30.001),
        ("seconds", -1),
        ("seconds", float("nan")),
        ("seconds", float("inf")),
        ("seconds", None),
        ("seconds", True),
        ("timed_out", True),
        ("timed_out", 0),
        ("timed_out", None),
        ("exit_code", 1),
        ("exit_code", False),
    ],
)
def test_completed_helper_admission_rejected(field: str, value: object) -> None:
    """Total helper timing and timeout are strict independent completion gates."""
    helper = {"exit_code": 0, "seconds": 1, "timed_out": False}
    helper[field] = value
    with pytest.raises(RuntimeError, match="helper completion"):
        matrix.validate_raw_completion(helper, [{"event": "call_end", "seconds": 0.1}])


def test_nested_completed_calls_remain_independent() -> None:
    """Nested wrapper and slot durations need no single pending-call overwrite."""
    matrix.validate_raw_completion(
        {"exit_code": 0, "seconds": 29.999, "timed_out": False},
        [
            {"event": "call_start", "raw_geometry": "property"},
            {"event": "call_start", "slot": 10},
            {"event": "call_end", "slot": 10, "seconds": 2.99},
            {"event": "call_end", "raw_geometry": "property", "seconds": 2.999},
        ],
    )
    with pytest.raises(RuntimeError, match="evidence missing"):
        matrix.validate_raw_completion(
            {"exit_code": 0, "seconds": 1, "timed_out": False}, []
        )
