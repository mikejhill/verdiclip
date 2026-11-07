"""A frozen exact corridor supplies evidence, never native completion proof."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import mss
import pytest
from scripts.panel_full_sweep import FullSweep
from scripts.panel_seam_candidate import SeamCandidate, SeamSweep
from scripts.panel_uia_probe import Probe
from tests.tooling.test_panel_full_sweep import VIEWPORT, FakeProbe

from verdiclip.capture.scroll_stitch import ScrollFrame, ScrollStitcher, StitchError


def frame(offset: int, height: int = 64, width: int = 3) -> ScrollFrame:
    """Unique complete scanlines provide independently known document coordinates."""
    return ScrollFrame(
        width,
        height,
        b"".join(
            bytes((row % 256, row // 256, 100)) * width
            for row in range(offset, offset + height)
        ),
    )


def replace_rows(
    source: ScrollFrame, start: int, stop: int, color: bytes
) -> ScrollFrame:
    """Replace complete rows to expose ignored versus verified regions explicitly."""
    stride = source.width * 3
    return ScrollFrame(
        source.width,
        source.height,
        source.rgb[: start * stride]
        + color * source.width * (stop - start)
        + source.rgb[stop * stride :],
    )


def test_all_document_rows_once_with_short_final_step() -> None:
    """The inherited midpoint splice preserves all rows without gaps or padding."""
    candidate = SeamCandidate()
    steps = [candidate.append(frame(offset)).new_rows for offset in (0, 32, 64, 75)]
    assert steps == [64, 32, 32, 11]
    image = candidate.finish()
    assert image.rgb == frame(0, 139).rgb
    assert (image.width, image.height, image.frame_count) == (3, 139, 4)


def test_clipped_discarded_edges_do_not_replace_source_pixels() -> None:
    """Corrupted inner viewport edges are discarded by the unchanged midpoint splice."""
    first = replace_rows(frame(0), 61, 64, b"bad")
    middle = replace_rows(replace_rows(frame(32), 0, 3, b"bad"), 61, 64, b"bad")
    last = replace_rows(frame(64), 0, 3, b"bad")
    old = ScrollStitcher()
    old.append(first)
    with pytest.raises(StitchError):
        old.append(middle)
    candidate = SeamCandidate()
    for viewport in (first, middle, last):
        candidate.append(viewport)
    assert candidate.finish().rgb == frame(0, 128).rgb


def test_inside_corridor_change_rejects_atomically() -> None:
    """One differing color channel inside the corridor must still reject."""
    candidate = SeamCandidate()
    candidate.append(frame(0))
    prior = candidate.finish()
    changed = bytearray(frame(32).rgb)
    changed[16 * 9] += 1
    with pytest.raises(StitchError, match="absent or ambiguous"):
        candidate.append(ScrollFrame(3, 64, bytes(changed)))
    assert candidate.finish() == prior
    assert candidate.append(frame(32)).new_rows == 32


@pytest.mark.parametrize("kind", ["blank", "periodic"])
def test_blank_and_periodic_corridors_reject(kind: str) -> None:
    """Unchanged/ambiguous content cannot be resolved by a preferred displacement."""
    if kind == "blank":
        first = second = ScrollFrame(3, 64, b"\xff" * 64 * 9)
    else:
        rows = [bytes((row % 8, 20, 30)) * 3 for row in range(100)]
        first = ScrollFrame(3, 64, b"".join(rows[:64]))
        second = ScrollFrame(3, 64, b"".join(rows[4:68]))
    candidate = SeamCandidate()
    candidate.append(first)
    with pytest.raises(StitchError):
        candidate.append(second)


def test_stationary_corridor_is_not_motion_evidence() -> None:
    """Even a unique translated equality needs observable same-position change."""
    # Exactly 25 blank rows produce only d=1's 24-row translated corridor.
    first = replace_rows(frame(0), 19, 44, b"\xff\xff\xff")
    second = replace_rows(frame(200), 19, 43, b"\xff\xff\xff")
    candidate = SeamCandidate()
    candidate.append(first)
    with pytest.raises(StitchError, match="Stationary"):
        candidate.append(second)


@pytest.mark.parametrize(("width", "height"), [(1025, 64), (3, 513), (3, 47)])
def test_experimental_work_limits_precede_mutation(width: int, height: int) -> None:
    """Small fixture-specific bounds prevent quadratic search on huge images."""
    candidate = SeamCandidate()
    with pytest.raises(StitchError, match=r"limit|short"):
        candidate.append(frame(0, height, width))
    with pytest.raises(StitchError, match="No frames"):
        candidate.finish()


def test_retained_outside_corridor_change_is_explicit_limitation() -> None:
    """Acceptance does not assert that unseen retained regions stayed unchanged."""
    first, second = frame(0), replace_rows(frame(32), 29, 31, b"bad")
    candidate = SeamCandidate()
    candidate.append(first)
    assert candidate.append(second).new_rows == 32
    image = candidate.finish()
    assert image.rgb != frame(0, 96).rgb
    assert image.rgb == first.rgb[: 48 * 9] + second.rgb[16 * 9 :]


def test_periodic_alias_still_requires_external_native_displacement() -> None:
    """A unique local match cannot detect an entire skipped repeating period."""
    rows = [bytes((row % 200, 40, 70)) * 3 for row in range(700)]
    candidate = SeamCandidate()
    candidate.append(ScrollFrame(3, 264, b"".join(rows[:264])))
    step = candidate.append(ScrollFrame(3, 264, b"".join(rows[332:596])))
    assert step.new_rows == 132
    assert step.new_rows != 332  # The caller's native-displacement gate must reject.


def test_sticky_content_outside_corridor_can_duplicate() -> None:
    """This documented limitation must not become an unsupported rejection claim."""
    first = replace_rows(frame(0), 29, 31, b"bad")
    second = replace_rows(frame(32), 29, 31, b"bad")
    candidate = SeamCandidate()
    candidate.append(first)
    candidate.append(second)
    assert candidate.finish().rgb.count(b"bad") == 12  # Two 2-row, 3-pixel copies.


def test_factory_default_is_strict_and_candidate_is_explicit() -> None:
    """Ordinary full sweeps never silently adopt the fixture-only evidence rule."""
    strict = object.__new__(FullSweep)
    alternate = object.__new__(SeamSweep)
    assert type(strict._make_stitcher()) is ScrollStitcher
    assert type(alternate._make_stitcher()) is SeamCandidate
    assert strict._make_stitcher() is not strict._make_stitcher()
    assert SeamSweep.run is FullSweep.run


@pytest.mark.parametrize("corrupt", ["", "bottom-repeat", "top-repeat", "restore"])
def test_seam_sweep_retains_native_completion_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corrupt: str
) -> None:
    """The alternate matcher cannot bypass endpoint, top or full-pixel restoration."""
    monkeypatch.setattr("scripts.panel_full_sweep.time.monotonic", lambda: 0)
    probe = FakeProbe(tmp_path)
    source = object.__new__(mss.MSS)
    before = probe.frame(source, "before")
    probe.corrupt = corrupt
    sweep = SeamSweep(cast("Probe", probe), source, VIEWPORT, before, 0)
    if corrupt:
        with pytest.raises(RuntimeError, match="changed"):
            sweep.run()
        assert not probe.report.get("full_sweep_passed")
        assert not (tmp_path / "stitched.png").exists()
    else:
        sweep.run()
        assert probe.report["full_sweep_passed"] is True
        assert probe.report["restored"] is True
        assert (tmp_path / "stitched.png").exists()
        assert probe.requests[-3:] == [100, 0, probe.initial.vertical]
