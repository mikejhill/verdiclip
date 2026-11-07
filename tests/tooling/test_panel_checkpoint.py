"""False-green regression checks for synthetic native checkpoint admission."""

from __future__ import annotations

from typing import Any

import pytest
from scripts.panel_checkpoint import EXPECTED_FRAMES, checkpoint_passed


@pytest.fixture
def evidence() -> dict[str, Any]:
    """Independent synthetic success, explicitly not product qualification."""
    return {
        "complete": True,
        "frame_checks": {name: {"matches": True} for name in EXPECTED_FRAMES},
        "top_mid_displacements": [120],
        "sibling_outer_unchanged": True,
        "horizontal_unchanged": True,
        "original_state_restored": True,
        "original_pixels_restored": True,
        "product_viewport_qualified": False,
    }


def test_synthetic_success_is_not_product_qualification(
    evidence: dict[str, Any],
) -> None:
    """Only the first native content-oracle checkpoint is admitted."""
    assert checkpoint_passed(evidence)
    assert evidence["product_viewport_qualified"] is False


@pytest.mark.parametrize("name", sorted(EXPECTED_FRAMES))
def test_missing_or_mismatching_frame_fails(
    evidence: dict[str, Any], name: str
) -> None:
    """No missing endpoint, midpoint, initial or restoration frame can pass."""
    evidence["frame_checks"][name]["matches"] = False
    assert not checkpoint_passed(evidence)
    del evidence["frame_checks"][name]
    assert not checkpoint_passed(evidence)


@pytest.mark.parametrize("shifts", [[], [0], [-1], [1, 2], [True], [1.5], None])
def test_missing_ambiguous_or_invalid_overlap_fails(
    evidence: dict[str, Any], shifts: object
) -> None:
    """A single positive integer pixel displacement is required."""
    evidence["top_mid_displacements"] = shifts
    assert not checkpoint_passed(evidence)


@pytest.mark.parametrize(
    "key",
    [
        "complete",
        "sibling_outer_unchanged",
        "horizontal_unchanged",
        "original_state_restored",
        "original_pixels_restored",
    ],
)
def test_invariant_failure_or_absence_fails(evidence: dict[str, Any], key: str) -> None:
    """Helper success cannot conceal failed independent invariants."""
    evidence[key] = False
    assert not checkpoint_passed(evidence)
    del evidence[key]
    assert not checkpoint_passed(evidence)


@pytest.mark.parametrize("checks", [{}, None, {"top": {"matches": True}}])
def test_incomplete_evidence_fails(evidence: dict[str, Any], checks: object) -> None:
    """Empty and truncated receipts fail closed."""
    evidence["frame_checks"] = checks
    assert not checkpoint_passed(evidence)
