"""Owned paint readiness preserves stale/failure evidence without cropping."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from scripts import panel_matrix_reference as reference
from scripts.panel_probe_oracle import Pixels, color_bounds

BORDER = bytes((22, 155, 180))
WIDTH, HEIGHT = 24, 10


def frame(left: int) -> bytes:
    """Place a ten-pixel panel at an authored integer horizontal offset."""
    return b"".join(
        BORDER
        if left <= x < left + 10 and (x in (left, left + 9) or y in (2, 7))
        else bytes((1, 2, 3))
        for y in range(HEIGHT)
        for x in range(WIDTH)
    )


class Source:
    """Deterministic raster stream, with no native calls."""

    monitors: ClassVar[list[dict[str, int]]] = [
        {},
        {"left": 0, "top": 0, "width": 100, "height": 100},
    ]

    def __init__(self, frames: list[bytes]) -> None:
        self.frames = frames
        self.index = 0

    def grab(self, _area: object) -> SimpleNamespace:
        """Return the next authored fake raster."""
        rgb = self.frames[min(self.index, len(self.frames) - 1)]
        self.index += 1
        return SimpleNamespace(width=WIDTH, height=HEIGHT, rgb=rgb)

    def close(self) -> None:
        """No native resource exists."""


class Guard:
    """Inert owned guard for pure timing tests."""

    def __init__(self, *_args: object) -> None:
        pass

    def check(self) -> None:
        """Keep ownership constant in the pure fixture."""


def setup(monkeypatch: pytest.MonkeyPatch, frames: list[bytes]) -> dict[str, Any]:
    """Select bounded fake clock, foreground and screen samples."""
    owner = {"root_hwnd": 12, "pid": 1, "allowed_rect": [0, 0, WIDTH, HEIGHT]}
    monkeypatch.setattr(
        reference,
        "ViewportOrigin",
        lambda *_args: SimpleNamespace(observe=lambda _manifest: {"stable": True}),
    )
    monkeypatch.setattr(
        reference, "verify_calibration", lambda *_args: {"checked": True}
    )
    clock = [0.0]
    monkeypatch.setattr(reference.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        reference.time,
        "sleep",
        lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )
    monkeypatch.setattr(reference, "NativeGuard", Guard)
    monkeypatch.setattr(
        reference,
        "NativeWindow",
        lambda: SimpleNamespace(foreground=lambda _pids: owner),
    )
    monkeypatch.setattr(reference.mss, "MSS", lambda: Source(frames))
    return {**owner, "owned_process_ids": [1]}


def test_eleven_pixel_stale_locator_is_not_selected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The observed11px startup shift must settle before locating an entry point."""
    manifest = setup(monkeypatch, [frame(11), frame(0), frame(0), frame(0)])
    image = reference.stable_owned_root(manifest, tmp_path / "readiness")
    bounds = color_bounds(image, BORDER)
    assert bounds == (0, 0, 10, 10)
    assert (image.width, image.height) == (WIDTH, HEIGHT)
    report = json.loads((tmp_path / "readiness/readiness.json").read_text())
    assert [o["stable_count"] for o in report["observations"]] == [1, 1, 2, 3]
    assert (tmp_path / "readiness/root-first.png").exists()
    assert (tmp_path / "readiness/root-last.png").exists()
    assert (tmp_path / "readiness/root-first.png").read_bytes() != (
        tmp_path / "readiness/root-last.png"
    ).read_bytes()


def test_failed_readiness_retains_first_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No stable three-frame sequence produces failure evidence, not a crop."""
    manifest = setup(monkeypatch, [frame(i % 2) for i in range(16)])
    with pytest.raises(RuntimeError, match="did not settle"):
        reference.stable_owned_root(manifest, tmp_path / "failed")
    report = json.loads((tmp_path / "failed/readiness.json").read_text())
    assert len(report["observations"]) == 16
    assert report["first_failure"] is not None
    assert (tmp_path / "failed/root-first.png").exists()
    assert (tmp_path / "failed/root-last.png").exists()


def test_full_root_keeps_inexact_color_edge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A partially blended edge pixel survives regardless of exact cyan bounds."""
    rgb = bytes((21, 154, 179)) + frame(0)[3:]
    manifest = setup(monkeypatch, [rgb] * 3)
    image = reference.stable_owned_root(manifest, tmp_path / "full")
    assert image == Pixels(WIDTH, HEIGHT, rgb)
    assert image.rgb[:3] == bytes((21, 154, 179))


def test_missing_observer_is_not_zero_box_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reference needs initialized fractional content-box observations."""
    monkeypatch.setattr(
        reference, "snapshots", lambda _path: [{"observer_ready": False}]
    )
    with pytest.raises(RuntimeError, match="observer not ready"):
        reference.latest_authored_state(tmp_path)


def test_three_fresh_state_brackets_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reference accepts only three root samples with fresh equal pre/post states."""
    manifest = setup(monkeypatch, [frame(0)] * 3)
    sequence = [0]

    def snapshot(_path: Path) -> dict[str, Any]:
        sequence[0] += 1
        return {"sequence": sequence[0], "state": {"target": {"vertical": 763.2}}}

    monkeypatch.setattr(reference, "latest_authored_state", snapshot)
    image = reference.stable_owned_root(
        manifest, tmp_path / "reference", tmp_path / "states"
    )
    assert image.rgb == frame(0)
    report = json.loads((tmp_path / "reference/readiness.json").read_text())
    assert len(report["observations"]) == 3
    assert all(
        o["after"]["sequence"] > o["before"]["sequence"] for o in report["observations"]
    )
    assert report["observations"][-1]["stable_count"] == 3


def test_selected_native_bounds_preserve_color_clipped_edges() -> None:
    """A root image retains the top/left pixel that exact-color bounds missed."""
    image = Pixels(6, 5, bytes(range(90)))
    selected = reference.selected_from_root(image, [50, 50, 56, 55], [51, 51, 55, 54])
    assert (selected.width, selected.height) == (4, 3)
    assert selected.rgb[:3] == image.rgb[21:24]
    with pytest.raises(ValueError, match="outside"):
        reference.selected_from_root(image, [50, 50, 56, 55], [49, 50, 55, 54])


@pytest.mark.parametrize("change_at", [2, 3])
def test_native_parent_geometry_change_rejects_stable_pixels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change_at: int
) -> None:
    """Parent DPI changes within or between observations invalidate root evidence."""
    manifest = setup(monkeypatch, [frame(0)] * 3)
    count = [0]
    sequence = [0]

    def observe(_manifest: object) -> dict[str, Any]:
        count[0] += 1
        return {
            "hwnd": 4,
            "screen_client_rect": [0, 0, WIDTH, HEIGHT],
            "native_geometry": {
                "parent_chain": [
                    {"hwnd": 12, "dpi": 120 if count[0] >= change_at else 96}
                ]
            },
        }

    def snapshot(_path: Path) -> dict[str, Any]:
        sequence[0] += 1
        return {"sequence": sequence[0], "state": {"target": {"vertical": 0}}}

    monkeypatch.setattr(
        reference, "ViewportOrigin", lambda *_args: SimpleNamespace(observe=observe)
    )
    monkeypatch.setattr(reference, "latest_authored_state", snapshot)
    with pytest.raises(RuntimeError, match="origin changed"):
        reference.stable_owned_root(manifest, tmp_path / "changed", tmp_path / "states")
    report = json.loads((tmp_path / "changed/readiness.json").read_text())
    assert "origin changed" in report["first_failure"]
    assert (tmp_path / "changed/root-first.png").exists()
    assert (tmp_path / "changed/root-last.png").exists()
