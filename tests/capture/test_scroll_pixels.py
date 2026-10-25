"""Private pixel handoff refuses substituted files and encoded allocation surprises."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.capture.test_scroll_core import Documents

from verdiclip.capture.scroll.pixels import FrameDestination, FrameFiles
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Rect


def test_exact_png_is_consumed_and_only_private_directory_is_removed(
    tmp_path: Path,
) -> None:
    """Decoded pixels own their data after their one-file transport is deleted."""
    sibling = tmp_path / "keep.txt"
    sibling.write_text("keep", encoding="utf-8")
    files = FrameFiles(max_pixels=96 * 48, parent=tmp_path)
    destination = files.destination(Rect(0, 0, 96, 48))
    image = Documents.image(width=96, height=48)
    assert image.save(str(destination.path))
    actual = files.consume(destination)
    assert actual == image
    assert not destination.path.exists()
    files.close()
    files.close()
    assert actual == image
    assert list(tmp_path.iterdir()) == [sibling]


def test_substituted_path_is_never_opened_or_removed(tmp_path: Path) -> None:
    """A provider cannot redirect reading/cleanup to another user file."""
    external = tmp_path / "external.png"
    image = Documents.image(width=96, height=48)
    assert image.save(str(external))
    original = external.read_bytes()
    files = FrameFiles(max_pixels=96 * 48, parent=tmp_path)
    destination = files.destination(Rect(0, 0, 96, 48))
    with pytest.raises(CaptureError, match="unrelated"):
        files.consume(FrameDestination(destination.viewport, external))
    files.close()
    assert external.read_bytes() == original


@pytest.mark.parametrize("kind", ["dimensions", "corrupt", "large"])
def test_unusable_encoded_frame_is_refused_before_delivery(
    tmp_path: Path, kind: str
) -> None:
    """Dimensions and encoded size are checked before trusting a decoded bitmap."""
    files = FrameFiles(max_pixels=96 * 48, parent=tmp_path)
    destination = files.destination(Rect(0, 0, 96, 48))
    if kind == "dimensions":
        assert Documents.image(width=2, height=2).save(str(destination.path))
    elif kind == "corrupt":
        destination.path.write_bytes(b"not-png")
    else:
        destination.path.write_bytes(
            bytes(FrameFiles.ENCODER_OVERHEAD + 96 * 48 * 4 + 1)
        )
    with pytest.raises(CaptureError):
        files.consume(destination)
    files.close()
    assert not list(tmp_path.iterdir())
