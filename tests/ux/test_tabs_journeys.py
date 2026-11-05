"""Observable editor-tab workflows, keyboard parity and activation budgets."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from verdiclip.document.document import Document
from verdiclip.editor.session import EditorSession
from verdiclip.editor.window import EditorOptions
from verdiclip.editor.workspace import Workspace
from verdiclip.output.delivery import ImageDelivery
from verdiclip.settings import EditorSettings, OutputSettings, Theme
from verdiclip.shell.theme import ThemeManager


@pytest.mark.ux
@pytest.mark.parametrize("theme", [Theme.LIGHT, Theme.DARK])
def test_keyboard_switch_reorder_detach_and_close(
    qtbot: QtBot,
    sample_image: QImage,
    tmp_path: Path,
    snapshot_dir: Path | None,
    theme: Theme,
) -> None:
    """UX-TAB-01..04: tabs group work and keep every window workflow reachable."""
    palette = QApplication.palette()
    ThemeManager.apply(theme)
    owner = Workspace()
    delivery = ImageDelivery(OutputSettings(directory=tmp_path))
    window = owner.open_document(
        EditorSession(Document(sample_image), EditorSettings()),
        delivery,
        EditorOptions(title="Capture one"),
    )
    owner.open_document(
        EditorSession(Document(sample_image), EditorSettings()),
        delivery,
        EditorOptions(title="Capture two"),
    )
    qtbot.waitExposed(window)
    window.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is window)
    window.canvas.setFocus()
    try:
        assert window.tab_bar.count() == 2
        assert window.tab_bar.tabText(1) == "Capture two"
        QTest.keyClick(window.canvas, Qt.Key.Key_1, Qt.KeyboardModifier.ControlModifier)
        assert window.tab_bar.currentIndex() == 0
        QTest.keyClick(
            window.canvas, Qt.Key.Key_Tab, Qt.KeyboardModifier.ControlModifier
        )
        assert window.tab_bar.currentIndex() == 1
        QTest.keyClick(
            window.canvas,
            Qt.Key.Key_PageUp,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
        )
        assert window.tab_bar.tabText(0) == "Capture two"
        if snapshot_dir is not None:
            assert window.grab().save(str(snapshot_dir / f"tabs-{theme.value}.png"))
        QTest.keyClick(
            window.canvas,
            Qt.Key.Key_N,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
        )
        qtbot.wait(0)
        assert len(owner.windows) == 2
        assert all(host.tab_bar.count() == 1 for host in owner.windows)
        detached = next(host for host in owner.windows if host is not window)
        qtbot.waitExposed(detached)
        detached.activateWindow()
        qtbot.waitUntil(lambda: QApplication.activeWindow() is detached)
        detached.canvas.setFocus()
        QTest.keyClick(
            detached.canvas, Qt.Key.Key_W, Qt.KeyboardModifier.ControlModifier
        )
        qtbot.wait(0)
        assert owner.windows == (window,)
        assert window.isVisible()
        assert window.tab_bar.tabText(0) == "Capture one"
    finally:
        owner.close_all()
        owner.parking.deleteLater()
        QApplication.setPalette(palette)


@pytest.mark.ux
@pytest.mark.ux_budget
def test_warm_tab_activation_budget(
    qtbot: QtBot, tmp_path: Path, record_property: Callable[[str, object], None]
) -> None:
    """UX-TAB-05: switching tabs does not encode or copy the document image."""
    owner = Workspace()
    image = QImage(1920, 1080, QImage.Format.Format_ARGB32)
    image.fill(0xFFEEEEEE)
    delivery = ImageDelivery(OutputSettings(directory=tmp_path))
    for number in range(10):
        owner.open_document(
            EditorSession(Document(image), EditorSettings()),
            delivery,
            EditorOptions(title=f"Capture {number}"),
        )
    window = owner.windows[0]
    durations: list[float] = []
    try:
        for index in range(100):
            start = time.perf_counter()
            window.activate_page(window.pages[index % 10])
            qtbot.wait(0)
            durations.append((time.perf_counter() - start) * 1000)
        percentile = sorted(durations)[94]
        record_property("warm_tab_activation_p95_ms", percentile)
        assert percentile < 50, f"Warm tab activation p95={percentile:.2f} ms"
        assert not list(tmp_path.glob("*.png"))
    finally:
        owner.close_all()
        owner.parking.deleteLater()
