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
    assert actual.size() == image.size()
    assert actual.pixelColor(0, 0) == image.pixelColor(0, 0)
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


@pytest.mark.parametrize(
    "kind", ["dimensions", "corrupt", "large", "truncated", "empty"]
)
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
    elif kind == "large":
        destination.path.write_bytes(
            bytes(FrameFiles.ENCODER_OVERHEAD + 96 * 48 * 4 + 1)
        )
    elif kind == "truncated":
        assert Documents.image(width=96, height=48).save(str(destination.path))
        destination.path.write_bytes(destination.path.read_bytes()[:33])
    else:
        destination.path.write_bytes(b"")
    with pytest.raises(CaptureError):
        files.consume(destination)
    files.close()
    assert not list(tmp_path.iterdir())


def test_frame_reservation_and_missing_file_fail_without_reusing_pixels(
    tmp_path: Path,
) -> None:
    """A lost frame or unfinished reservation cannot become a stale screenshot."""
    with pytest.raises(ValueError, match="positive"):
        FrameFiles(max_pixels=0)
    files = FrameFiles(max_pixels=100, parent=tmp_path)
    with pytest.raises(CaptureError, match="budget"):
        files.destination(Rect(0, 0, 20, 20))
    destination = files.destination(Rect(0, 0, 10, 10))
    with pytest.raises(CaptureError, match="unavailable"):
        files.consume(destination)
    destination.path.write_bytes(b"pending")
    with pytest.raises(CaptureError, match="previous"):
        files.destination(destination.viewport)
    files.close()
    with pytest.raises(CaptureError, match="unavailable"):
        files.destination(destination.viewport)


def test_unavailable_storage_and_cleanup_error_are_actionable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Report IO failures, retain ownership for retry, and never delete a sibling."""
    files = FrameFiles(max_pixels=100, parent=tmp_path / "missing-parent")
    with pytest.raises(CaptureError, match="prepare"):
        files.destination(Rect(0, 0, 10, 10))
    files.close()
    files = FrameFiles(max_pixels=100, parent=tmp_path)
    destination = files.destination(Rect(0, 0, 10, 10))

    def unavailable(_path: Path) -> None:
        raise OSError("Fixture disk unavailable")

    with monkeypatch.context() as patch:
        patch.setattr("verdiclip.capture.scroll.pixels.shutil.rmtree", unavailable)
        with pytest.raises(CaptureError, match="remove temporary"):
            files.close()
    assert destination.path.parent.exists()
    files.close()
    assert not list(tmp_path.iterdir())
