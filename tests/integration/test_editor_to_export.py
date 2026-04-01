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
        assert not flattened.isNull()
        assert flattened.width() == 200
        assert flattened.height() == 200

        output_path = tmp_path / "export_test.png"
        success = flattened.save(str(output_path), "PNG")
        assert success is True
        assert output_path.exists()
        assert output_path.stat().st_size > 0
