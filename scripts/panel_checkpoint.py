"""Admission for the synthetic native checkpoint, never product qualification."""

from __future__ import annotations

from collections.abc import Mapping

EXPECTED_FRAMES = frozenset({"before", "top", "mid", "bottom", "restore"})


def checkpoint_passed(oracle: Mapping[str, object]) -> bool:
    """Reject incomplete or contradictory independent synthetic evidence."""
    checks = oracle.get("frame_checks")
    shifts = oracle.get("top_mid_interior_displacements")
    if not isinstance(checks, dict) or set(checks) != EXPECTED_FRAMES:
        return False
    if any(
        not isinstance(check, dict) or check.get("matches") is not True
        for check in checks.values()
    ):
        return False
    if (
        not isinstance(shifts, list)
        or len(shifts) != 1
        or type(shifts[0]) is not int
        or shifts[0] <= 0
    ):
        return False
    return all(
        oracle.get(key) is True
        for key in (
            "complete",
            "sibling_outer_unchanged",
            "horizontal_unchanged",
            "original_state_restored",
            "original_pixels_restored",
        )
    )
