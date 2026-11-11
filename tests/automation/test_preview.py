"""Previews show the live document, cut, scaled and gridded as asked."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QImage

from verdiclip.automation.preview import (
    GRID_LINE,
    PREVIEW_MAX_AGE_S,
    Preview,
    PreviewFolder,
    PreviewOptions,
    PreviewRenderer,
    write_png,
)
from verdiclip.automation.protocol import AutomationError, ErrorCode
from verdiclip.document.annotations import RectangleShape
from verdiclip.document.commands import AddAnnotations, SetCrop
from verdiclip.document.document import Document
from verdiclip.document.history import History
from verdiclip.document.style import Color, Style
from verdiclip.geometry import Rect
from verdiclip.render.renderer import Renderer

pytestmark = pytest.mark.usefixtures("qapp")

RED = Style(stroke=Color(255, 0, 0), fill=Color(255, 0, 0), width=2)


@pytest.fixture
def annotated(document: Document) -> Document:
    """The sample document with a filled red box at 100,100 (unsaved edit)."""
    box = RectangleShape(rect=Rect(100, 100, 40, 40), style=RED)
    History(document).execute(AddAnnotations([box], "Rectangle"))
    return document


def render(document: Document, **options: object) -> Preview:
    """Render ``document`` with ``options``."""
    return PreviewRenderer().render(document, PreviewOptions(**options))  # ty: ignore[invalid-argument-type]


def test_default_preview_matches_what_save_writes(annotated: Document) -> None:
    """The preview is the flattened image, unsaved annotations included."""
    preview = render(annotated)

    assert preview.image == Renderer().flatten(annotated)
    assert preview.origin == (0, 0)
    assert preview.scale == 1.0
    assert preview.grid == 0
    assert preview.image.pixelColor(120, 120) == QColor(255, 0, 0)


def test_base_preview_omits_annotations(annotated: Document) -> None:
    """--base shows what lies under the annotations."""
    preview = render(annotated, base=True)

    assert preview.image.pixelColor(120, 120) == QColor("white")


def test_region_is_cut_in_image_coordinates(annotated: Document) -> None:
    """A region renders alone, and origin maps its pixels back to the image."""
    preview = render(annotated, region=Rect(90, 95, 60, 50))

    assert (preview.image.width(), preview.image.height()) == (60, 50)
    assert preview.origin == (90, 95)
    assert preview.image.pixelColor(30, 25) == QColor(255, 0, 0)


def test_region_respects_crop(annotated: Document) -> None:
    """Coordinates stay in original-image pixels after a crop."""
    History(annotated).execute(SetCrop(annotated.crop, Rect(80, 80, 200, 150)))

    whole = render(annotated)
    region = render(annotated, region=Rect(0, 0, 130, 130))

    assert whole.origin == (80, 80)
    assert (whole.image.width(), whole.image.height()) == (200, 150)
    assert whole.image.pixelColor(40, 40) == QColor(255, 0, 0)
    assert region.origin == (80, 80)
    assert (region.image.width(), region.image.height()) == (50, 50)


def test_region_outside_visible_image_is_rejected(annotated: Document) -> None:
    """A region that misses the image is a usage error."""
    with pytest.raises(AutomationError, match="outside") as caught:
        render(annotated, region=Rect(500, 500, 10, 10))

    assert caught.value.code is ErrorCode.BAD_REQUEST


def test_large_images_are_downscaled(annotated: Document) -> None:
    """max_side bounds the longest side and reports the scale."""
    preview = render(annotated, max_side=200)

    assert (preview.image.width(), preview.image.height()) == (200, 150)
    assert preview.scale == 0.5


def test_small_images_and_zero_keep_full_size(annotated: Document) -> None:
    """No downscale when the image fits or the limit is off."""
    assert render(annotated, max_side=400).scale == 1.0
    assert render(annotated, max_side=0).image.width() == 400


@pytest.mark.parametrize("option", ["max_side", "grid"])
def test_negative_options_are_rejected(annotated: Document, option: str) -> None:
    """Negative sizes are usage errors."""
    with pytest.raises(AutomationError, match=option) as caught:
        render(annotated, **{option: -1})

    assert caught.value.code is ErrorCode.BAD_REQUEST


def test_grid_lines_sit_on_image_coordinates(annotated: Document) -> None:
    """Grid lines are drawn at multiples of the step in image pixels."""
    plain = render(annotated, max_side=0)
    gridded = render(annotated, max_side=0, grid=50)

    assert gridded.grid == 50
    assert gridded.image.pixelColor(150, 290) != plain.image.pixelColor(150, 290)
    assert gridded.image.pixelColor(175, 290) == plain.image.pixelColor(175, 290)
    assert gridded.image.pixelColor(390, 250) != plain.image.pixelColor(390, 250)
    assert gridded.image.pixelColor(390, 275) == plain.image.pixelColor(390, 275)


def test_grid_offsets_follow_the_region(annotated: Document) -> None:
    """Lines land on image multiples even when the region starts between them."""
    gridded = render(annotated, region=Rect(30, 30, 200, 200), grid=50)

    # Image x=150 is preview x=120; x=175 (preview 145) has no line
    assert gridded.image.pixelColor(120, 190).red() == GRID_LINE.red()
    assert gridded.image.pixelColor(145, 190) == QColor("white")


def test_dense_grid_is_widened_to_stay_legible(annotated: Document) -> None:
    """A grid too dense for the scale doubles its step until labels fit."""
    preview = render(annotated, max_side=200, grid=10)

    assert preview.grid == 80


def test_preview_folder_names_and_prunes(tmp_path: Path) -> None:
    """Default previews get unique names and expire after a day."""
    folder = PreviewFolder(tmp_path)
    first = folder.new_path("abcdef0123456789", 4)
    second = folder.new_path("abcdef0123456789", 4)
    old = tmp_path / "old.png"
    fresh = tmp_path / "fresh.png"
    keep = tmp_path / "notes.txt"
    for path in (old, fresh, keep):
        path.write_bytes(b"x")
    stale = time.time() - PREVIEW_MAX_AGE_S - 10
    os.utime(old, (stale, stale))
    os.utime(keep, (stale, stale))

    folder.prune()

    assert folder.root == tmp_path
    assert first.parent == tmp_path
    assert first.name.startswith("abcdef01-s4-")
    assert first != second
    assert not old.exists()
    assert fresh.exists()
    assert keep.exists()


def test_prune_tolerates_missing_folder(tmp_path: Path) -> None:
    """Pruning a folder that doesn't exist yet is a no-op."""
    PreviewFolder(tmp_path / "absent").prune(now=time.time())


def test_prune_never_fails_the_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A preview that can't be deleted is skipped."""
    old = tmp_path / "old.png"
    old.write_bytes(b"x")
    os.utime(old, (0, 0))

    def refuse(_self: Path, *, missing_ok: bool = False) -> None:
        del missing_ok
        raise PermissionError

    monkeypatch.setattr(Path, "unlink", refuse)
    PreviewFolder(tmp_path).prune()

    assert old.exists()


def test_prune_tolerates_unreadable_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A folder that can't be listed is left alone."""

    def refuse(_self: Path, _pattern: str) -> list[Path]:
        raise PermissionError

    monkeypatch.setattr(Path, "glob", refuse)
    PreviewFolder(tmp_path).prune()


def test_default_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Previews live under LOCALAPPDATA, or the temp folder without it."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert PreviewFolder().root == tmp_path / "VerdiClip" / "automation"
    monkeypatch.setenv("LOCALAPPDATA", "")
    assert PreviewFolder.default_root().name == "VerdiClip-automation"
    monkeypatch.delenv("LOCALAPPDATA")
    assert PreviewFolder.default_root().name == "VerdiClip-automation"


def test_write_png(tmp_path: Path, sample_image: QImage) -> None:
    """The PNG lands where asked, creating folders, at an absolute path."""
    target = tmp_path / "deep" / "preview.png"

    written = write_png(sample_image, target)

    assert written == target.resolve()
    assert QImage(str(written)) == sample_image.convertToFormat(
        QImage(str(written)).format()
    )


def test_write_png_failures(tmp_path: Path, sample_image: QImage) -> None:
    """Unwritable targets are reported as failures."""
    blocker = tmp_path / "file"
    blocker.write_bytes(b"x")
    folder = tmp_path / "is-a-folder.png"
    folder.mkdir()

    with pytest.raises(AutomationError, match="Cannot create") as no_parent:
        write_png(sample_image, blocker / "preview.png")
    with pytest.raises(AutomationError, match="Cannot write") as no_file:
        write_png(sample_image, folder)

    assert no_parent.value.code is ErrorCode.FAILED
    assert no_file.value.code is ErrorCode.FAILED
