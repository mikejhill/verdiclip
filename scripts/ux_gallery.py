"""Render key screens in light and dark themes for visual design review.

Uses the real Windows platform style (not the headless test platform) with
windows kept off-screen, so themes render exactly as users see them.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QImage,
    QMouseEvent,
    QPainter,
    QPalette,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTabWidget, QWidget

from verdiclip.capture.grabber import FrozenScreen
from verdiclip.capture.models import ScreenGeometry
from verdiclip.capture.overlay import SelectionOverlay
from verdiclip.document.annotations import (
    ArrowShape,
    CounterMarker,
    HighlightShape,
    RectangleShape,
)
from verdiclip.document.commands import AddAnnotations
from verdiclip.document.document import Document
from verdiclip.document.style import HIGHLIGHT_YELLOW, RED, WHITE, Color, Style
from verdiclip.document.transform import Rotate
from verdiclip.editor.close_prompt import CloseChoice, ClosePrompt
from verdiclip.editor.resize_dialog import ResizeDialog
from verdiclip.editor.session import EditorSession, ToolId
from verdiclip.editor.window import EditorWindow
from verdiclip.geometry import Point, Rect
from verdiclip.output.delivery import ImageDelivery
from verdiclip.settings import Settings, Theme
from verdiclip.shell.settings_dialog import SettingsDialog
from verdiclip.shell.theme import ThemeManager

type Scene = Callable[[], QWidget]

STEP: Final = Style(stroke=WHITE, fill=RED, font_size=14)
README_IMAGES: Final = {
    "dark-editor-rectangle-selected": "editor.png",
    "light-editor-select": "editor-light.png",
    "dark-capture-overlay": "capture.png",
    "dark-editor-rotated": "editor-rotated.png",
    "dark-settings-capture": "settings-capture.png",
    "dark-settings-general": "settings-general.png",
}

# Windows 11's default accent, as each mode shows it
DEFAULT_ACCENT: Final = {
    Theme.LIGHT: QColor(0, 95, 184),
    Theme.DARK: QColor(96, 205, 255),
}


@dataclass(frozen=True, slots=True)
class _Window:
    """A window shown on the gallery's fake desktop."""

    title: str
    bounds: Rect


class Gallery:
    """Render each scene in each theme and save PNGs."""

    def __init__(self, out_dir: Path) -> None:
        self._out = out_dir
        self._settings = Settings()
        self._after_show: Callable[[], None] | None = None

    def run(self) -> list[Path]:
        """Render every scene in light and dark; return the files written."""
        self._out.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for theme in (Theme.LIGHT, Theme.DARK):
            ThemeManager.apply(theme)
            self._use_default_accent(theme)
            QApplication.processEvents()
            for name, scene in self._scenes().items():
                written.append(self._render(f"{theme.value}-{name}", scene))
        return written

    @staticmethod
    def _use_default_accent(theme: Theme) -> None:
        """Use Windows' default accent so docs don't show this PC's personal color."""
        palette = QApplication.palette()
        palette.setColor(QPalette.ColorRole.Accent, DEFAULT_ACCENT[theme])
        QApplication.setPalette(palette)

    # Scenes

    def _scenes(self) -> dict[str, Scene]:
        """Return the scenes to render, by name."""
        return {
            "editor-select": lambda: self._editor(ToolId.SELECT, select=False),
            "editor-rectangle-selected": lambda: self._editor(
                ToolId.RECTANGLE, select=True
            ),
            "editor-text-tool": lambda: self._editor(ToolId.TEXT, select=False),
            "editor-rotated": self._rotated_editor,
            "resize-dialog": lambda: ResizeDialog(720, 420),
            "close-prompt": lambda: ClosePrompt.describe(
                file_name=None, copied=False
            ).build(None),
            "settings-capture": lambda: self._settings_tab(0),
            "settings-hotkeys": lambda: self._settings_tab(1),
            "settings-general": lambda: self._settings_tab(4),
            "capture-overlay": self._overlay,
        }

    def _editor(self, tool: ToolId, *, select: bool) -> EditorWindow:
        """Return an editor over a sample screenshot with a few annotations."""
        session = EditorSession(Document(self._sample()), self._settings.editor)
        callout = Style(fill=Color(255, 255, 255), width=3, font_size=15)
        box = RectangleShape(
            rect=Rect(454, 84, 234, 64), text="Leave room to explore", style=callout
        )
        arrow = ArrowShape(start=Point(567, 157), end=Point(572, 230))
        steps = [
            CounterMarker(center=Point(437, 237), radius=13, label="1", style=STEP),
            CounterMarker(center=Point(437, 303), radius=13, label="2", style=STEP),
        ]
        mark = HighlightShape(
            rect=Rect(484, 292, 183, 22), style=Style(fill=HIGHLIGHT_YELLOW)
        )
        session.history.execute(AddAnnotations([mark, box, arrow, *steps], "Sample"))
        window = EditorWindow(session, ImageDelivery(self._settings.output))
        window.resize(1000, 640)
        window.activate_tool(tool)
        if select:
            session.selection.set([box.id])
        return window

    def _rotated_editor(self) -> QWidget:
        """Return the sample editor after Rotate left and Fit, with the box selected."""
        window = self._editor(ToolId.SELECT, select=True)

        def rotate() -> None:
            window.transform_image(Rotate(clockwise=False))
            window.canvas.zoom_fit()

        # Rotate once shown, as a user would: a status message posted before
        # show leaves the tool hint drawn underneath it
        self._after_show = rotate
        return window

    def _overlay(self) -> QWidget:
        """Return the capture overlay hovering a window, with the magnifier."""
        desktop = QImage(900, 560, QImage.Format.Format_RGB32)
        desktop.fill(QColor(40, 70, 110))
        painter = QPainter(desktop)
        painter.drawImage(90, 70, self._sample())
        painter.end()
        bounds = Rect(0, 0, 900, 560)
        geometry = ScreenGeometry("gallery", bounds, bounds, 1.0)
        overlay = SelectionOverlay(
            geometry,
            QGuiApplication.primaryScreen(),
            FrozenScreen(desktop, bounds),
            [_Window("Fieldnotes - Weekend collection", Rect(90, 70, 720, 420))],
            show_magnifier=True,
        )
        overlay.setFixedSize(900, 560)
        return overlay

    def _settings_tab(self, index: int) -> QWidget:
        """Return the settings dialog on tab ``index``."""
        dialog = SettingsDialog(self._settings)
        tabs = dialog.findChild(QTabWidget)
        if tabs is not None:
            tabs.setCurrentIndex(index)
        return dialog

    # Rendering

    def _render(self, name: str, scene: Scene) -> Path:
        """Show ``scene`` off-screen, let it settle, and save a PNG."""
        widget = scene()
        widget.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        widget.show()
        QTest.qWait(50)
        if self._after_show is not None:
            self._after_show()
            self._after_show = None
            QTest.qWait(50)
        if isinstance(widget, EditorWindow):
            QTest.mouseMove(widget.canvas.viewport(), QPoint(500, 300))
        if isinstance(widget, SelectionOverlay):
            # Off-screen widgets don't get synthetic cursor moves; send one directly.
            # Rest on the itinerary so the magnifier shows real pixel detail.
            pos = QPointF(625, 315)
            move = QMouseEvent(
                QEvent.Type.MouseMove,
                pos,
                pos,
                Qt.MouseButton.NoButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            )
            QApplication.sendEvent(widget, move)
        QApplication.processEvents()
        path = self._out / f"{name}.png"
        widget.grab().save(str(path))
        if isinstance(widget, EditorWindow):
            # Sample edits are throwaway; never prompt about unsaved changes
            widget.session.history.mark_delivered()
        widget.close()
        widget.deleteLater()
        return path

    @staticmethod
    def _sample() -> QImage:
        """Render original fictional content; screenshots still use real app widgets."""
        image = QImage(720, 420, QImage.Format.Format_RGB32)
        image.fill(QColor("white"))
        painter = QPainter(image)
        renderer = QSvgRenderer(
            str(Path(__file__).parent.parent / "docs/images/source/fieldnotes.svg")
        )
        renderer.render(painter)
        painter.end()
        return image


class GalleryCommand:
    """Command-line entry for the gallery."""

    @classmethod
    def main(cls) -> None:
        """Parse arguments, render, and list the files written."""
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--out", default="docs/ux/gallery", help="Output folder.")
        parser.add_argument(
            "--readme-dir",
            default="",
            help="Also copy the README screenshots into this folder.",
        )
        args = parser.parse_args()
        app = QApplication(sys.argv[:1])
        del app
        # Never show a modal prompt on the reviewer's real desktop
        ClosePrompt.ask = lambda *_args: CloseChoice.DISCARD  # ty: ignore[invalid-assignment]  # script-only stub
        written = Gallery(Path(args.out)).run()
        QGuiApplication.processEvents()
        if args.readme_dir:
            readme = Path(args.readme_dir)
            readme.mkdir(parents=True, exist_ok=True)
            for source, target in README_IMAGES.items():
                shutil.copyfile(Path(args.out) / f"{source}.png", readme / target)
                written.append(readme / target)
        sys.stdout.write("\n".join(str(p) for p in written) + "\n")


if __name__ == "__main__":
    GalleryCommand.main()
