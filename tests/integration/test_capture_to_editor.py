"""Integration test: capture → editor workflow."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QGraphicsPixmapItem

from verdiclip.capture.screen import ScreenCapture
from verdiclip.editor.canvas import EditorCanvas

if TYPE_CHECKING:
    from verdiclip.config import Config


class TestCaptureOpensEditor:
    def test_capture_opens_editor(self, qapp, tmp_config: Config) -> None:
        pixmap = ScreenCapture.capture_primary_monitor()
        assert not pixmap.isNull()

        canvas = EditorCanvas()
        canvas.set_image(pixmap)

        assert canvas.pixmap_item is not None
        assert isinstance(canvas.pixmap_item, QGraphicsPixmapItem)
        assert canvas.pixmap_item.pixmap().width() == pixmap.width()
        assert canvas.pixmap_item.pixmap().height() == pixmap.height()
