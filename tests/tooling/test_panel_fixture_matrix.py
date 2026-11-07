"""Portable authored-page boundaries; no browser or foreign code injection."""

from __future__ import annotations

import json
from typing import cast

import pytest
from scripts.panel_fixture_matrix import MatrixCase, initial_cases, render


def configuration(page: str) -> dict[str, object]:
    """Read inert authored configuration without executing page code."""
    start = '<script type="application/json" id="configuration">'
    return cast(
        "dict[str, object]",
        json.loads(page.split(start, maxsplit=1)[1].split("</script>", maxsplit=1)[0]),
    )


def test_initial_cases_cover_first_representatives() -> None:
    """Theme/scenario combinations are deterministic; scale remains native-owned."""
    cases = initial_cases()
    assert len(cases) == 4
    assert {(c.scenario, c.theme) for c in cases} == {
        ("conversation", "light"),
        ("conversation", "dark"),
        ("table", "light"),
        ("table", "dark"),
    }
    for case in cases:
        page = render(case)
        assert configuration(page)["reference_offset"] is None
        assert "MATRIX_CONFIGURATION" not in page
        assert "https://" not in page
        assert "expected_ids" in page
        assert "getBoundingClientRect" in page


@pytest.mark.parametrize("variant", ["sticky", "streaming"])
def test_authored_negative_variants(variant: str) -> None:
    """Negative changes are authored in the owned page, never injected later."""
    page = render(MatrixCase("conversation", variant=variant))
    assert configuration(page)["variant"] == variant
    assert "Stationary interior warning" in page
    assert "stream_tick" in page


def test_fractional_fixed_reference_config() -> None:
    """Reference supports fractional CSS offsets without using matcher geometry."""
    page = render(MatrixCase("table", "dark", width=520, reference_offset=131.2))
    config = configuration(page)
    assert config["reference_offset"] == 131.2
    assert "-reference" in page
    assert "translateY" in page
    assert "scrollIntoView" not in page


@pytest.mark.parametrize(
    "case",
    [
        MatrixCase("foreign"),
        MatrixCase("table", "unknown"),
        MatrixCase("table", variant="unknown"),
        MatrixCase("table", width=0),
        MatrixCase("table", width=901),
        MatrixCase("table", reference_offset=-1),
        MatrixCase("table", reference_offset=float("nan")),
        MatrixCase("table", reference_offset=float("inf")),
        MatrixCase("table", reference_offset=8001),
        MatrixCase("table", variant="streaming", reference_offset=0),
    ],
)
def test_invalid_config_rejected(case: MatrixCase) -> None:
    """No unbounded or dynamic reference case is rendered."""
    with pytest.raises(ValueError, match=r"Unknown|Invalid|Reference"):
        render(case)
