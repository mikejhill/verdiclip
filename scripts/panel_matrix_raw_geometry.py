"""Fixed provenance for the retained twenty-second table-dark125 experiment.

DOM state is an independent fixture oracle, never native geometry evidence.
This module does not choose a capture rectangle or change an authored pose.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any

from scripts.panel_fixture_matrix import MatrixCase, render

DPR = 1.25
CASE = MatrixCase("table", "dark", width=520)
SCHEMA = "owned-matrix-table-dark125-v1"
ASSET_SHA = "74c3475eb9d074e34384c3a72640feff01f7a5d869e960626f28e8c8bccf886e"
GENERATOR_SHA = "1eed702aa71ce8d429607e3a708d97bd6094d4147538d6d7ce6a4b2e2536ec87"
HTML_SHA = "01f3ab130dfd4606cb51a0e11299501322521e50ffaa945029662744fa6b0e5a"
REQUESTED = {"vertical": 119, "horizontal": 147}
HISTORICAL = {"vertical": 119.19999694824219, "horizontal": 147.1999969482422}
Json = dict[str, Any]


def provenance() -> Json:
    """Compare actual fixed sources to retained hashes, not self-reported data."""
    scripts = Path(__file__).parent
    hashes = {
        "asset_sha256": hashlib.sha256(
            (scripts / "panel_fixture/matrix.html").read_bytes()
        ).hexdigest(),
        "generator_sha256": hashlib.sha256(
            (scripts / "panel_fixture_matrix.py").read_bytes()
        ).hexdigest(),
        "generated_sha256": hashlib.sha256(render(CASE).encode()).hexdigest(),
    }
    if hashes != {
        "asset_sha256": ASSET_SHA,
        "generator_sha256": GENERATOR_SHA,
        "generated_sha256": HTML_SHA,
    }:
        raise RuntimeError("Original matrix asset/generator provenance changed")
    return {
        **hashes,
        "baseline": "panel-twenty-second-evidence/table-dark125/raw",
        "baseline_source_head": "007990db82b51776f2ecde276e0f9c8f49c10e89",
        "baseline_browser": {
            "Chromium": "156.0.8078.80",
            "Microsoft Edge": "156.0.4314.58",
        },
        "baseline_browser_binary_sha256": None,
        "browser_binary_identity_equivalence_claimed": False,
        "config": asdict(CASE),
        "requested_initial_scroll": dict(REQUESTED),
        "historical_observed_scroll": dict(HISTORICAL),
    }


def validate_manifest(fixture: Json) -> None:
    """Both supervisor and helper reject generic Edge or reference fixtures."""
    if (
        fixture.get("kind") != "owned-matrix"
        or fixture.get("fixture_schema") != SCHEMA
        or fixture.get("dpr") != DPR
        or fixture.get("browser_scale_flag") != "1.25"
        or fixture.get("matrix_provenance") != provenance()
        or fixture.get("observed_initial_scroll") != HISTORICAL
    ):
        raise RuntimeError("Raw geometry requires original matrix table-dark125")


def manifest(
    owner: Json, initial: Json, case: MatrixCase, scale: str, html: str
) -> Json:
    """Record requested and actually observed offsets without rounding either."""
    target = initial.get("state", {}).get("target", {})
    observed = {key: target.get(key) for key in HISTORICAL}
    if (
        case != CASE
        or scale != "1.25"
        or hashlib.sha256(html.encode()).hexdigest() != HTML_SHA
        or initial.get("config") != asdict(CASE)
        or initial.get("mode") != "scroll"
        or initial.get("observer_ready") is not True
        or any(
            type(value) not in (int, float) or not math.isfinite(value)
            for value in observed.values()
        )
    ):
        raise RuntimeError("Matrix raw fixture/profile/pose provenance mismatch")
    result = {
        **owner,
        "kind": "owned-matrix",
        "fixture_schema": SCHEMA,
        "dpr": initial.get("dpr"),
        "browser_scale_flag": scale,
        "matrix_provenance": provenance(),
        "observed_initial_scroll": observed,
    }
    validate_manifest(result)
    return result
