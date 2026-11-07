"""Clipping diagnostics never count as complete capture qualification."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from scripts import panel_feasibility as supervisor
from scripts.panel_probe_oracle import Pixels

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("failure", [None, "incomplete", "restore", "pixels", "image"])
def test_diagnostic_requires_safe_restore_without_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    """Only a completed, restored experiment is accepted as diagnostic evidence."""
    directory = tmp_path / "probe"
    directory.mkdir()
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "clipping_diagnostic_completed": failure != "incomplete",
                "restored": failure != "restore",
            }
        ),
        encoding="utf-8",
    )
    if failure == "image":
        (directory / "stitched.png").touch()
    before = Pixels(2, 2, bytes(12))
    restored = Pixels(2, 2, bytes([1]) * 12) if failure == "pixels" else before
    monkeypatch.setattr(
        supervisor,
        "load_pixels",
        lambda path: restored if path.name == "restore.png" else before,
    )
    result: dict[str, Any] = {
        "clipping_diagnostic": True,
        "helper": {"exit_code": 0},
        "checkpoint_passed": False,
    }
    if failure is not None:
        with pytest.raises(RuntimeError, match="Clipping diagnostic"):
            supervisor.verify_result(result, tmp_path, tmp_path, {})
    else:
        supervisor.verify_result(result, tmp_path, tmp_path, {})
        assert result["diagnostic_completed"] is True
    assert result["checkpoint_passed"] is False
