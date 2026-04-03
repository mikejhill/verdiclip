"""Integration test: multi-tool annotation workflow."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QGraphicsView

from tests.conftest import simulate_draw
from verdiclip.editor.canvas import EditorCanvas
from verdiclip.editor.tools.arrow import ArrowTool
from verdiclip.editor.tools.highlight import HighlightTool
from verdiclip.editor.tools.rectangle import RectangleTool

if TYPE_CHECKING:
    from pathlib import Path


class TestMultiToolAnnotation:
    """Draw multiple annotation types, export, and verify the result."""

    def test_draw_rect_arrow_text_export(self, qapp, tmp_path: Path) -> None:
        canvas = EditorCanvas()
        pixmap = QPixmap(400, 300)
        pixmap.fill(Qt.GlobalColor.white)
        canvas.set_image(pixmap)
        scene = canvas.scene

        # Draw a rectangle
        rect_tool = RectangleTool(stroke_color=QColor("#FF0000"))
        simulate_draw(rect_tool, scene, QGraphicsView(scene), QPointF(10, 10), QPointF(100, 80))

        # Draw an arrow
        arrow_tool = ArrowTool(stroke_color=QColor("#0000FF"))
        simulate_draw(arrow_tool, scene, QGraphicsView(scene), QPointF(120, 50), QPointF(200, 50))

        # Draw a highlight
        hl_tool = HighlightTool()
        simulate_draw(hl_tool, scene, QGraphicsView(scene), QPointF(50, 100), QPointF(150, 130))

        # We should have at least 3 annotation items (excluding background/boundary)
        annotations = [i for i in scene.items() if 0 <= i.zValue() < 9000]
        assert len(annotations) >= 3, f"Expected at least 3 annotations, got {len(annotations)}"

        # Export should produce a valid, non-null pixmap
        flattened = canvas.get_flattened_pixmap()
        assert not flattened.isNull()
        assert flattened.width() == 400
        assert flattened.height() == 300

        # Save to file and verify
        out = tmp_path / "multi_tool.png"
        assert flattened.save(str(out), "PNG")
        assert out.exists()
        assert out.stat().st_size > 0


class TestUndoRedoWorkflow:
    """Draw, undo, redo, then export — verifying state at each step."""

    def test_draw_undo_redo_export(self, qapp) -> None:
        canvas = EditorCanvas()
        pixmap = QPixmap(200, 200)
        pixmap.fill(Qt.GlobalColor.white)
        canvas.set_image(pixmap)
        scene = canvas.scene
        view = QGraphicsView(scene)

        # Draw a rectangle
        tool = RectangleTool()
        simulate_draw(tool, scene, view, QPointF(10, 10), QPointF(90, 90))

        def _annotations():
            return [i for i in scene.items() if 0 <= i.zValue() < 9000]

        assert len(_annotations()) == 1, "Should have 1 annotation after draw"

        # Undo should remove it
        if hasattr(canvas, "_history") and canvas._history:
            canvas._history.undo()
            assert len(_annotations()) == 0, "Should have 0 annotations after undo"

            # Redo should restore it
            canvas._history.redo()
            assert len(_annotations()) == 1, "Should have 1 annotation after redo"

        # Export should still work
        flattened = canvas.get_flattened_pixmap()
        assert not flattened.isNull()


class TestCaptureAnnotateExport:
    """Full workflow: capture → annotate → export to multiple formats."""

    def test_capture_annotate_export_png_jpg(self, qapp, tmp_path: Path) -> None:
        from verdiclip.capture.screen import ScreenCapture

        pixmap = ScreenCapture.capture_primary_monitor()
        assert not pixmap.isNull(), "Capture should produce a non-null pixmap"

        canvas = EditorCanvas()
        canvas.set_image(pixmap)
        scene = canvas.scene
        view = QGraphicsView(scene)

        # Add an annotation
        tool = RectangleTool(stroke_color=QColor("#00FF00"))
        simulate_draw(tool, scene, view, QPointF(20, 20), QPointF(120, 80))

        flattened = canvas.get_flattened_pixmap()
        assert not flattened.isNull()

        # Export as PNG
        png_path = tmp_path / "out.png"
        assert flattened.save(str(png_path), "PNG")
        assert png_path.stat().st_size > 0

        # Export as JPEG
        jpg_path = tmp_path / "out.jpg"
        assert flattened.save(str(jpg_path), "JPEG")
        assert jpg_path.stat().st_size > 0
