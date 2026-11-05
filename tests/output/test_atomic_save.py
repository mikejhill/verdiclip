"""Failed encoding/commit must preserve an existing output file byte for byte."""

from __future__ import annotations

from pathlib import Path
from typing import override

import pytest
from PySide6.QtCore import QIODevice, QSaveFile
from PySide6.QtGui import QImage, QImageWriter

from verdiclip.exceptions import DeliveryError
from verdiclip.output.delivery import ImageDelivery
from verdiclip.settings import OutputSettings


def test_encoding_failure_preserves_previous_file(
    sample_image: QImage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed encoder never exposes a partial replacement as the final image."""
    destination = tmp_path / "existing.png"
    original = b"Original file must survive"
    destination.write_bytes(original)

    class FailingWriter(QImageWriter):
        @override
        def write(self, image: QImage) -> bool:
            """Simulate a partial encoded stream and then a write failure."""
            del image
            self.device().write(b"Partial image stream")
            return False

    monkeypatch.setattr("verdiclip.output.delivery.QImageWriter", FailingWriter)
    with pytest.raises(DeliveryError, match="Could not save"):
        ImageDelivery(OutputSettings()).save(sample_image, destination)
    assert destination.read_bytes() == original


def test_commit_failure_preserves_previous_file(
    sample_image: QImage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An encoded temp file is not a successful save until replacement commits."""
    destination = tmp_path / "existing.png"
    original = b"Original destination"
    destination.write_bytes(original)

    class FailedCommit(QSaveFile):
        @override
        def commit(self) -> bool:
            """Simulate a disk/rename error, leaving the original file intact."""
            self.cancelWriting()
            return super().commit()

    monkeypatch.setattr("verdiclip.output.delivery.QSaveFile", FailedCommit)
    with pytest.raises(DeliveryError, match="Could not save"):
        ImageDelivery(OutputSettings()).save(sample_image, destination)
    assert destination.read_bytes() == original


def test_unopenable_destination_does_not_use_direct_write_fallback(
    sample_image: QImage, tmp_path: Path
) -> None:
    """A directory at the destination is an error, not a reason to bypass safety."""
    destination = tmp_path / "existing.png"
    destination.mkdir()
    marker = destination / "keep.txt"
    marker.write_text("Keep", encoding="utf-8")
    with pytest.raises(DeliveryError, match="Could not save"):
        ImageDelivery(OutputSettings()).save(sample_image, destination)
    assert marker.read_text(encoding="utf-8") == "Keep"


def test_temp_file_unavailable_refuses_destructive_fallback(
    sample_image: QImage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An existing writable file is preserved when no atomic temp can be made."""
    destination = tmp_path / "existing.png"
    destination.write_bytes(b"Keep original")

    class NoTemporaryFile(QSaveFile):
        @override
        def open(self, mode: QIODevice.OpenModeFlag) -> bool:
            """Model a directory denying temp creation but allowing file writes."""
            del mode
            if self.directWriteFallback():
                destination.write_bytes(b"Destructive partial replacement")
            return False

    monkeypatch.setattr("verdiclip.output.delivery.QSaveFile", NoTemporaryFile)
    with pytest.raises(DeliveryError, match="Could not save"):
        ImageDelivery(OutputSettings()).save(sample_image, destination)
    assert destination.read_bytes() == b"Keep original"


def test_atomic_save_creates_missing_nested_folders(
    sample_image: QImage, tmp_path: Path
) -> None:
    """The user's configured nested destination does not need pre-creation."""
    destination = tmp_path / "captures" / "project" / "today" / "image.png"
    assert (
        ImageDelivery(OutputSettings()).save(sample_image, destination) == destination
    )
    assert not QImage(str(destination)).isNull()


def test_atomic_jpeg_save_respects_quality(
    sample_image: QImage, tmp_path: Path
) -> None:
    """Changing export quality must affect encoded output, not just preferences."""
    low = tmp_path / "low.jpg"
    high = tmp_path / "high.jpg"
    ImageDelivery(OutputSettings(jpeg_quality=10)).save(sample_image, low)
    ImageDelivery(OutputSettings(jpeg_quality=95)).save(sample_image, high)
    assert not QImage(str(low)).isNull()
    assert not QImage(str(high)).isNull()
    assert high.read_bytes() != low.read_bytes()
    assert high.stat().st_size > low.stat().st_size
