"""Keep read-only diagnostic completion separate from native capture success."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from scripts.panel_feasibility import verify_result

if TYPE_CHECKING:
    from pathlib import Path


def test_read_only_completion_never_admits_capture(tmp_path: Path) -> None:
    """A complete no-pattern observation is useful evidence, not capture support."""
    probe = tmp_path / "probe"
    probe.mkdir()
    (probe / "manifest.json").write_text(
        json.dumps({"diagnostic_completed": True, "pattern_found": False}),
        encoding="utf-8",
    )
    result = {
        "read_only_discovery": True,
        "helper": {"exit_code": 0},
        "checkpoint_passed": False,
    }
    verify_result(result, tmp_path, tmp_path / "unused", {})
    assert result["diagnostic_completed"] is True
    assert result["checkpoint_passed"] is False


def test_completed_slow_call_is_not_qualified(tmp_path: Path) -> None:
    """An operation finishing between watchdog polls still violates its deadline."""
    probe = tmp_path / "probe"
    probe.mkdir()
    (probe / "calls.jsonl").write_text(
        json.dumps({"event": "call_end", "seconds": 3.01}) + "\n", encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="Completed native call exceeded"):
        verify_result({"helper": {"exit_code": 0}}, tmp_path, tmp_path, {})


def test_incomplete_discovery_fails(tmp_path: Path) -> None:
    """A missing completion marker cannot silently become a green diagnostic."""
    probe = tmp_path / "probe"
    probe.mkdir()
    (probe / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="experiment incomplete"):
        verify_result(
            {"read_only_discovery": True, "helper": {"exit_code": 0}},
            tmp_path,
            tmp_path,
            {},
        )
