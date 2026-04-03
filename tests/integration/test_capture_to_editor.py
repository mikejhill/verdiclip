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
        assert not pixmap.isNull(), "Screen capture returned a null pixmap"

        canvas = EditorCanvas()
        canvas.set_image(pixmap)

        assert canvas.pixmap_item is not None, "pixmap_item should be set after set_image"
        assert isinstance(canvas.pixmap_item, QGraphicsPixmapItem), (
            f"Expected QGraphicsPixmapItem, got {type(canvas.pixmap_item).__name__}"
        )
        assert canvas.pixmap_item.pixmap().width() == pixmap.width(), (
            f"Canvas pixmap width {canvas.pixmap_item.pixmap().width()} "
            f"should match capture width {pixmap.width()}"
        )
        assert canvas.pixmap_item.pixmap().height() == pixmap.height(), (
            f"Canvas pixmap height {canvas.pixmap_item.pixmap().height()} "
            f"should match capture height {pixmap.height()}"
        )
