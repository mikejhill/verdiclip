"""Every row, including thin boundaries, is checked outside candidate stitching."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from scripts import panel_feasibility as supervisor
from scripts.panel_probe_oracle import ROW_COUNT, ROW_HEIGHT, Pixels, row_color

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, Any], Path, dict[str, Pixels]]:
    """Provide independent synthetic pixels rather than invoking the stitcher."""
    (tmp_path / "probe").mkdir()
    (tmp_path / "probe/manifest.json").write_text(
        json.dumps({"full_sweep_passed": True, "first_failure": None}), encoding="utf-8"
    )
    rows = [
        bytes((255, 255, 255)) if y % ROW_HEIGHT == 0 else row_color(y // ROW_HEIGHT)
        for y in range(ROW_COUNT * ROW_HEIGHT)
    ]
    image = Pixels(32, len(rows), b"".join(row * 32 for row in rows))
    before = Pixels(32, 80, bytes(32 * 80 * 3))
    files = {"stitched.png": image, "before.png": before, "restore.png": before}
    monkeypatch.setattr(supervisor, "load_pixels", lambda path: files[path.name])
    return (
        {"dpi_x": 96, "dpi_y": 96, "content_rect": [10, 20, 42, 100]},
        tmp_path,
        files,
    )


def test_every_row_checked(
    complete: tuple[dict[str, Any], Path, dict[str, Pixels]],
) -> None:
    """Both the first white line and final colored row are included."""
    fixture, path, _ = complete
    result = supervisor.full_sweep_oracle(fixture, path)
    assert result["passed"] is True
    assert result["all_scanlines_checked"] == ROW_COUNT * ROW_HEIGHT
    assert result["product_integration_qualified"] is False


@pytest.mark.parametrize("row", [0, 31, 34, 1456])
def test_boundary_or_interior_error_fails(
    complete: tuple[dict[str, Any], Path, dict[str, Pixels]], row: int
) -> None:
    """A one-pixel row error cannot hide between the interior color samples."""
    fixture, path, files = complete
    image = files["stitched.png"]
    rgb = bytearray(image.rgb)
    index = (row * image.width + image.width - 12) * 3
    rgb[index] ^= 1
    files["stitched.png"] = Pixels(image.width, image.height, bytes(rgb))
    result = supervisor.full_sweep_oracle(fixture, path)
    assert result["passed"] is False
    assert result["mismatching_scanlines"] == [row]


def test_restore_pixels_required(
    complete: tuple[dict[str, Any], Path, dict[str, Pixels]],
) -> None:
    """A good composite does not excuse incomplete restoration."""
    fixture, path, files = complete
    files["restore.png"] = Pixels(32, 80, bytes([1]) * (32 * 80 * 3))
    assert supervisor.full_sweep_oracle(fixture, path)["passed"] is False


def test_wrong_extent_fails(
    complete: tuple[dict[str, Any], Path, dict[str, Pixels]],
) -> None:
    """A missing final row is never padded to make an apparently complete output."""
    fixture, path, files = complete
    image = files["stitched.png"]
    files["stitched.png"] = Pixels(32, image.height - 1, image.rgb[:-96])
    assert supervisor.full_sweep_oracle(fixture, path)["passed"] is False


def test_unqualified_dpi_fails(
    complete: tuple[dict[str, Any], Path, dict[str, Pixels]],
) -> None:
    """Do not silently extrapolate this exact color-boundary oracle to other DPI."""
    fixture, path, _ = complete
    fixture["dpi_y"] = 120
    assert supervisor.full_sweep_oracle(fixture, path)["passed"] is False
