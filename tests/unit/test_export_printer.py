"""Tests for verdiclip.export.printer module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPageLayout, QPixmap
from PySide6.QtPrintSupport import QPrintDialog, QPrinter

from verdiclip.export.printer import PrinterExporter, _best_orientation, _render_to_printer

# ---------------------------------------------------------------------------
# _best_orientation
# ---------------------------------------------------------------------------


class TestBestOrientation:
    def test_returns_landscape_for_wide_image(self) -> None:
        assert _best_orientation(1920, 1080) == QPageLayout.Orientation.Landscape, (
            f"Expected _best_orientation() to be Landscape, got {_best_orientation(1920, 1080)}"
        )

    def test_returns_portrait_for_tall_image(self) -> None:
        assert _best_orientation(1080, 1920) == QPageLayout.Orientation.Portrait, (
            f"Expected _best_orientation() to be Portrait, got {_best_orientation(1080, 1920)}"
        )

    def test_returns_portrait_for_square_image(self) -> None:
        assert _best_orientation(500, 500) == QPageLayout.Orientation.Portrait, (
            f"Expected _best_orientation() to be Portrait, got {_best_orientation(500, 500)}"
        )


# ---------------------------------------------------------------------------
# _render_to_printer
# ---------------------------------------------------------------------------


class TestRenderToPrinter:
    def test_renders_pixmap_to_printer(self, qapp) -> None:
        pixmap = QPixmap(200, 100)
        pixmap.fill(Qt.GlobalColor.red)

        mock_printer = MagicMock(spec=QPrinter)
        mock_printer.pageRect.return_value = QRectF(0, 0, 600, 800)

        with patch("verdiclip.export.printer.QPainter") as mock_painter_cls:
            painter_instance = mock_painter_cls.return_value
            painter_instance.begin.return_value = True

            _render_to_printer(pixmap, mock_printer)

            painter_instance.begin.assert_called_once_with(mock_printer)
            painter_instance.drawPixmap.assert_called_once()
            painter_instance.end.assert_called_once()

    def test_aborts_when_begin_fails(self, qapp) -> None:
        pixmap = QPixmap(200, 100)
        pixmap.fill(Qt.GlobalColor.red)

        mock_printer = MagicMock(spec=QPrinter)

        with patch("verdiclip.export.printer.QPainter") as mock_painter_cls:
            painter_instance = mock_painter_cls.return_value
            painter_instance.begin.return_value = False

            _render_to_printer(pixmap, mock_printer)

            painter_instance.drawPixmap.assert_not_called()
            painter_instance.end.assert_not_called()


# ---------------------------------------------------------------------------
# PrinterExporter.print_pixmap
# ---------------------------------------------------------------------------


class TestPrintPixmap:
    def test_returns_true_when_dialog_accepted(self, qapp) -> None:
        pixmap = QPixmap(100, 100)
        pixmap.fill(Qt.GlobalColor.blue)

        with (
            patch.object(
                QPrintDialog, "exec", return_value=QPrintDialog.DialogCode.Accepted
            ),
            patch(
                "verdiclip.export.printer._render_to_printer"
            ) as mock_render,
        ):
            result = PrinterExporter.print_pixmap(pixmap)

        assert result is True, f"Expected result to be True, got {result}"
        mock_render.assert_called_once()

    def test_returns_false_when_dialog_rejected(self, qapp) -> None:
        pixmap = QPixmap(100, 100)
        pixmap.fill(Qt.GlobalColor.blue)

        with patch.object(
            QPrintDialog, "exec", return_value=QPrintDialog.DialogCode.Rejected
        ):
            result = PrinterExporter.print_pixmap(pixmap)

        assert result is False, f"Expected result to be False, got {result}"
