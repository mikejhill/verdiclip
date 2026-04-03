"""Integration test: editor → file export workflow."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap

from verdiclip.editor.canvas import EditorCanvas

if TYPE_CHECKING:
    from pathlib import Path


class TestEditorExportToFile:
    def test_editor_export_to_file(self, qapp, tmp_path: Path) -> None:
        pixmap = QPixmap(200, 200)
        pixmap.fill(Qt.GlobalColor.blue)

        canvas = EditorCanvas()
        canvas.set_image(pixmap)

        flattened = canvas.get_flattened_pixmap()
        assert not flattened.isNull(), "Flattened pixmap should not be null"
        assert flattened.width() == 200, f"Expected flattened width 200, got {flattened.width()}"
        assert flattened.height() == 200, f"Expected flattened height 200, got {flattened.height()}"

        output_path = tmp_path / "export_test.png"
        success = flattened.save(str(output_path), "PNG")
        assert success is True, "Saving flattened pixmap to PNG should succeed"
        assert output_path.exists(), f"Exported file should exist at {output_path}"
        assert output_path.stat().st_size > 0, (
            f"Exported file should not be empty, got {output_path.stat().st_size} bytes"
        )
