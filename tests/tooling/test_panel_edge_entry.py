"""Retain the original owned border-oracle raster without changing its decision."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, Mock

import pytest
from scripts import panel_feasibility as module
from scripts.panel_probe_oracle import Pixels


def setup_capture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, border: bool
) -> tuple[Any, Any, Mock, Mock, bytes]:
    """One already-owned foreground and exactly one predetermined native grab."""
    rgb = bytes((23, 197, 233) if border else (0, 0, 0)) * 100
    owner = {"root_hwnd": 123, "pid": 456, "allowed_rect": [10, 20, 20, 30]}
    foreground = Mock(return_value=owner)
    monkeypatch.setattr(
        module, "NativeWindow", lambda: SimpleNamespace(foreground=foreground)
    )
    monkeypatch.setattr(Path, "is_file", lambda _self: True)
    state = {
        "sequence": 7,
        "dpr": 1,
        "user_agent": "fixture",
        "state": {
            "target": {"border": [1, 1], "viewport_width": 8, "viewport_height": 8}
        },
    }
    monkeypatch.setattr(module, "wait_snapshot", Mock(return_value=state))
    source = MagicMock()
    source.grab.return_value = SimpleNamespace(width=10, height=10, rgb=rgb)
    source.__enter__.return_value = source
    monkeypatch.setattr(module.mss, "MSS", Mock(return_value=source))
    monkeypatch.setattr(
        module.time, "sleep", Mock(side_effect=AssertionError("No delay"))
    )
    job = SimpleNamespace(
        start=Mock(return_value=(456, object())), pids=Mock(return_value=[456])
    )
    server = SimpleNamespace(output=tmp_path / "states", url="http://127.0.0.1/owned/")
    return job, server, source, foreground, rgb


def test_missing_border_retains_original_owned_pixels_before_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Absent border remains the same failure with inspectable original evidence."""
    job, server, source, foreground, rgb = setup_capture(
        monkeypatch, tmp_path, border=False
    )
    original = module.color_bounds

    def checked_bounds(image: Pixels, color: bytes) -> tuple[int, int, int, int]:
        """Evidence must exist before the first and only border assertion."""
        assert (tmp_path / "edge-entry.png").exists()
        assert (tmp_path / "edge-entry.json").exists()
        return original(image, color)

    monkeypatch.setattr(module, "color_bounds", checked_bounds)
    with pytest.raises(RuntimeError, match="Authored fixture border not visible"):
        module.edge_fixture(job, server, tmp_path, "1")
    saved = module.load_pixels(tmp_path / "edge-entry.png")
    assert saved == Pixels(10, 10, rgb)
    record = json.loads((tmp_path / "edge-entry.json").read_text())
    assert record["requested_rect"] == [10, 20, 20, 30]
    assert record["captured_dimensions"] == [10, 10]
    assert record["root_hwnd"] == 123
    assert record["pid"] == 456
    assert record["rgb_sha256"] == hashlib.sha256(rgb).hexdigest()
    assert record["border_oracle_evaluated"] is False
    assert record["foreground_rechecked_after_capture"] is False
    assert record["physical_monitor_dpi_measured"] is False
    assert record["observer_dpi_awareness"] == "per-monitor-v2"
    assert record["authored_sequence"] == 7
    source.grab.assert_called_once()
    foreground.assert_called_once_with([456])
    source.__exit__.assert_called_once()
    assert not list(tmp_path.glob("*profile*"))


def test_valid_border_preserves_existing_selection_and_single_capture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The diagnostic never substitutes its bounds for the existing border oracle."""
    job, server, source, foreground, _ = setup_capture(
        monkeypatch, tmp_path, border=True
    )
    result = module.edge_fixture(job, server, tmp_path, "1")
    assert result["pixel_border_rect"] == [10, 20, 20, 30]
    assert result["content_rect"] == [11, 21, 19, 29]
    source.grab.assert_called_once()
    foreground.assert_called_once()
    assert (tmp_path / "edge-entry.png").exists()


def test_primary_border_failure_survives_persistence_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A failed PNG write is secondary when the same original grab lacks its border."""
    job, server, source, _, _ = setup_capture(monkeypatch, tmp_path, border=False)
    monkeypatch.setattr(
        module.mss.tools, "to_png", Mock(side_effect=OSError("disk full"))
    )
    with pytest.raises(RuntimeError, match="border not visible") as caught:
        module.edge_fixture(job, server, tmp_path, "1")
    assert "disk full" in caught.value.__notes__[0]
    source.grab.assert_called_once()
    source.__exit__.assert_called_once()


def test_success_is_not_admitted_when_evidence_write_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A successful border assertion cannot silently lose required diagnostics."""
    job, server, source, _, _ = setup_capture(monkeypatch, tmp_path, border=True)
    monkeypatch.setattr(module, "write", Mock(side_effect=OSError("json write failed")))
    with pytest.raises(OSError, match="json write failed"):
        module.edge_fixture(job, server, tmp_path, "1")
    source.grab.assert_called_once()
    source.__exit__.assert_called_once()


def test_evidence_pixel_limit_precedes_png_allocation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Diagnostic encoding has a fixed bound, independent of arbitrary geometry."""
    monkeypatch.setattr(module, "MAX_ENTRY_PIXELS", 3)
    png = Mock()
    monkeypatch.setattr(module.mss.tools, "to_png", png)
    with pytest.raises(RuntimeError, match="pixel bound"):
        module.retain_edge_entry(tmp_path, Pixels(2, 2, bytes(12)), {}, {}, 0)
    png.assert_not_called()


def test_all_edge_fixture_callers_upload_only_exact_entry_filenames() -> None:
    """All three native jobs retain the entry files without browser-profile globs."""
    workflow = (
        module.ROOT / ".github/workflows/automatic-panel-feasibility.yml"
    ).read_text()
    for job, end in [
        ("provider", "owned-helper"),
        ("browser-wheel", "stable-identity"),
        ("stable-identity", "owned-paint-control"),
    ]:
        section = workflow.split("  " + job + ":\n", 1)[1].split("  " + end + ":\n", 1)[
            0
        ]
        assert section.count("receipts/**/edge-entry.json") == 1
        assert section.count("receipts/**/edge-entry.png") == 1
        assert "receipts/**/*.png" not in section
        assert "receipts/**/*.json" not in section
        assert "edge-profile" not in section
