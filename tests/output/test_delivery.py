"""Test real image files, clipboard delivery, and dialog-free PDF printing."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QGuiApplication, QImage
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import QApplication

from verdiclip.exceptions import DeliveryError
from verdiclip.output.delivery import ImageDelivery
from verdiclip.settings import ImageFormat, OutputSettings


class TestImageDelivery:
    """Observable clipboard, file, and printer results."""

    @pytest.mark.parametrize(
        "extension", ["png", "jpg", "bmp"], ids=["png", "jpg", "bmp"]
    )
    def test_save(
        self, tmp_path: Path, output_settings: OutputSettings, extension: str
    ) -> None:
        """Files retain dimensions and opaque formats composite alpha onto white."""
        image = QImage(20, 10, QImage.Format.Format_ARGB32)
        image.fill(QColor(255, 0, 0, 0))
        path = tmp_path / "nested" / f"image.{extension}"
        delivery = ImageDelivery(output_settings)

        saved = delivery.save(image, path)
        loaded = QImage(str(saved))

        assert saved == path
        assert not loaded.isNull()
        assert loaded.size() == image.size()
        if extension == "png":
            assert loaded.pixelColor(5, 5).alpha() == 0
        else:
            assert loaded.pixelColor(5, 5) == QColor("white")

    @pytest.mark.parametrize(
        ("extension", "expected"),
        [
            ("JPEG", ImageFormat.JPG),
            ("tif", ImageFormat.TIFF),
            ("PNG", ImageFormat.PNG),
            ("gif", ImageFormat.GIF),
            ("webp", ImageFormat.WEBP),
        ],
        ids=["jpeg-alias", "tif-alias", "uppercase", "gif", "webp"],
    )
    def test_format(self, extension: str, expected: ImageFormat) -> None:
        """Extensions resolve case-insensitively with JPEG and TIFF aliases."""
        path = Path(f"image.{extension}")

        result = ImageDelivery.format_for(path)

        assert result is expected

    def test_unsupported(self) -> None:
        """Unsupported extensions raise DeliveryError with supported formats."""
        with pytest.raises(DeliveryError, match="Unsupported file type"):
            ImageDelivery.format_for(Path("image.xyz"))

    def test_clipboard_unavailable(
        self,
        monkeypatch: pytest.MonkeyPatch,
        output_settings: OutputSettings,
        sample_image: QImage,
    ) -> None:
        """An unavailable operating-system clipboard raises DeliveryError."""
        monkeypatch.setattr(QGuiApplication, "clipboard", lambda: None)
        delivery = ImageDelivery(output_settings)

        with pytest.raises(DeliveryError, match="clipboard is not available"):
            delivery.copy(sample_image)

    def test_auto_path(self, output_settings: OutputSettings) -> None:
        """Automatic paths use the configured directory and filename pattern."""
        delivery = ImageDelivery(output_settings)

        path = delivery.auto_path(moment=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC))

        assert delivery.settings == output_settings
        assert path == output_settings.directory / "Screenshot 2026-01-02 03-04-05.png"

    def test_copy(
        self, qapp: QApplication, sample_image: QImage, output_settings: OutputSettings
    ) -> None:
        """Copy places the exact pixels on the clipboard."""
        del qapp

        ImageDelivery(output_settings).copy(sample_image)

        assert QGuiApplication.clipboard().image() == sample_image

    def test_pdf(
        self, qapp: QApplication, tmp_path: Path, sample_image: QImage
    ) -> None:
        """Rendering to a PDF printer writes a nonempty PDF without a dialog."""
        assert qapp is not None
        path = tmp_path / "print.pdf"
        printer = QPrinter()
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(str(path))

        ImageDelivery.render_to_printer(sample_image, printer)

        assert path.read_bytes().startswith(b"%PDF-")
        assert path.stat().st_size > 1000

    def test_printer_failure(
        self, qapp: QApplication, tmp_path: Path, sample_image: QImage
    ) -> None:
        """A PDF destination in a missing directory raises DeliveryError."""
        assert qapp is not None
        printer = QPrinter()
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(str(tmp_path / "missing" / "print.pdf"))

        with pytest.raises(DeliveryError, match="Could not start printing"):
            ImageDelivery.render_to_printer(sample_image, printer)

    def test_folder_failure(
        self, tmp_path: Path, sample_image: QImage, output_settings: OutputSettings
    ) -> None:
        """Blocked output folders raise DeliveryError."""
        blocker = tmp_path / "blocker"
        blocker.write_text("blocked", encoding="utf-8")

        with pytest.raises(DeliveryError, match="Could not create folder"):
            ImageDelivery(output_settings).save(sample_image, blocker / "image.png")

    def test_writer_failure(
        self, tmp_path: Path, output_settings: OutputSettings
    ) -> None:
        """A null image writer failure becomes DeliveryError."""
        delivery = ImageDelivery(output_settings)

        with pytest.raises(DeliveryError, match="Could not save"):
            delivery.save(QImage(), tmp_path / "null.png")
