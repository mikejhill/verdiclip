"""Pay the first-use costs of the capture overlay and editor before a capture.

The first overlay and editor a process builds load styles, fonts, glyphs, and
icons and create native windows; later ones reuse those caches. Building and
painting a throwaway of each while the tray sits idle moves that delay off the
first capture after a restart. Nothing here is shown on screen or registered
with the real workspace.
"""

from __future__ import annotations

import logging
import time
from typing import Final

from PySide6.QtGui import QColor, QGuiApplication, QImage
from PySide6.QtWidgets import QWidget

from verdiclip.capture.grabber import FrozenScreen
from verdiclip.capture.models import ScreenGeometry
from verdiclip.capture.overlay import SelectionOverlay
from verdiclip.document.annotations import RectangleShape, TextNote
from verdiclip.document.document import Document
from verdiclip.editor.contracts import EditorOptions
from verdiclip.editor.factory import create_editor_window
from verdiclip.editor.session import EditorSession
from verdiclip.geometry import Rect
from verdiclip.output.delivery import ImageDelivery
from verdiclip.settings import EditorSettings, OutputSettings

logger = logging.getLogger(__name__)

# Start after the tray is up and startup registrations are done
WARM_UP_DELAY_MS: Final = 1500
_SAMPLE_SIZE: Final = 64


def warm_up(editor: EditorSettings, output: OutputSettings) -> None:
    """Build, paint, and destroy a throwaway overlay and editor.

    Never raises: a failed warm-up only means the first capture is slower.
    """
    started = time.perf_counter()
    try:
        _warm_overlay()
        _warm_editor(editor, output)
    except Exception:
        logger.exception("Warm-up failed; the first capture may be slower")
        return
    logger.info("Warmed up capture and editor in %.0f ms", _elapsed_ms(started))


def _warm_overlay() -> None:
    screen = QGuiApplication.primaryScreen()
    area = Rect(0, 0, _SAMPLE_SIZE, _SAMPLE_SIZE)
    geometry = ScreenGeometry("warm-up", area, area, 1.0)
    overlay = SelectionOverlay(
        geometry,
        screen,
        FrozenScreen(_sample_image(), area),
        (),
        show_magnifier=True,
    )
    _paint_and_discard(overlay)


def _warm_editor(editor: EditorSettings, output: OutputSettings) -> None:
    document = Document(_sample_image())
    session = EditorSession(document, editor, delivered=True)
    # Lay out text and a shape so their fonts and paths are cached too
    session.document.insert(
        [
            (0, RectangleShape(rect=Rect(8, 8, 40, 24))),
            (1, TextNote(rect=Rect(8, 36, 48, 20), text="Aa", auto_fit=False)),
        ]
    )
    host = create_editor_window(
        session, ImageDelivery(output), EditorOptions(confirm_close=False)
    )
    try:
        host.winId()  # Create the native window without showing it
        host.grab()
    finally:
        owner = host.workspace
        host.close()
        owner.close_all()
        owner.parking.deleteLater()
        host.deleteLater()


def _paint_and_discard(widget: QWidget) -> None:
    try:
        widget.winId()
        widget.grab()
    finally:
        widget.deleteLater()


def _sample_image() -> QImage:
    image = QImage(_SAMPLE_SIZE, _SAMPLE_SIZE, QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    return image


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000
