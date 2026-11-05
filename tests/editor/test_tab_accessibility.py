"""Offscreen keyboard and accessibility contracts, not Narrator certification."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QImage, QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPlainTextEdit, QWidget
from pytestqt.qtbot import QtBot

from verdiclip.document.annotations import RectangleShape
from verdiclip.document.commands import AddAnnotations
from verdiclip.document.document import Document
from verdiclip.editor.session import EditorSession
from verdiclip.editor.window import EditorOptions, EditorWindow
from verdiclip.editor.workspace import Workspace
from verdiclip.geometry import Point, Rect
from verdiclip.output.delivery import ImageDelivery
from verdiclip.settings import EditorSettings, OutputSettings


@pytest.fixture
def owner(qapp: QApplication) -> Iterator[Workspace]:
    """Retain and explicitly close only this test's synthetic document windows."""
    workspace = Workspace()
    yield workspace
    assert workspace.close_all()
    workspace.parking.deleteLater()
    qapp.processEvents()


def grouped(owner: Workspace, image: QImage, path: Path) -> EditorWindow:
    """Create two documents with distinct spoken names and independent sessions."""
    for title in ("First capture", "Second capture"):
        owner.open_document(
            EditorSession(Document(image), EditorSettings()),
            ImageDelivery(OutputSettings(directory=path)),
            EditorOptions(title=title),
        )
    return owner.windows[0]


def test_spoken_tab_identity_dirty_state_and_close_names_follow_moves(
    owner: Workspace,
    sample_image: QImage,
    tmp_path: Path,
) -> None:
    """Accessible names describe identity/state/order after reorder and detach."""
    host = grouped(owner, sample_image, tmp_path)
    first, second = host.pages
    first.record.session.history.execute(
        AddAnnotations([RectangleShape(rect=Rect(2, 2, 5, 5))], "Draw")
    )
    assert host.tab_bar.accessibleName() == "Capture tabs"
    assert "First capture" in host.tab_bar.accessibleTabName(0)
    assert "1 of 2" in host.tab_bar.accessibleTabName(0)
    assert "not saved or copied" in host.tab_bar.accessibleTabName(0)
    first.record.session.history.mark_delivered()
    assert host.tab_bar.accessibleTabName(0).endswith(", delivered")
    assert owner.move(host, first, host, 1)
    assert "First capture" in host.tab_bar.accessibleTabName(1)
    assert "2 of 2" in host.tab_bar.accessibleTabName(1)
    for index, page in enumerate(host.pages):
        buttons = [
            cast(QWidget | None, host.tab_bar.tabButton(index, side))
            for side in (
                host.tab_bar.ButtonPosition.LeftSide,
                host.tab_bar.ButtonPosition.RightSide,
            )
        ]
        close = next(button for button in buttons if button is not None)
        assert close.accessibleName() == f"Close {page.record.label}"
        assert host.tab_bar.tabToolTip(index) == page.record.label
    assert owner.move(host, first)
    target = owner.host_of(first.record.identifier)
    assert target is not None
    assert "First capture" in target.tab_bar.accessibleTabName(0)
    assert "1 of 1" in target.tab_bar.accessibleTabName(0)
    assert "1 of 1" in host.tab_bar.accessibleTabName(0)
    assert host.pages == (second,)


def test_tab_shortcuts_are_named_window_scoped_and_reachable_without_pointer(
    owner: Workspace,
    sample_image: QImage,
    tmp_path: Path,
) -> None:
    """Structure commands have discoverable keyboard parity per editor host."""
    host = grouped(owner, sample_image, tmp_path)
    expected = {
        "Next tab": "Ctrl+Tab",
        "Previous tab": "Ctrl+Shift+Tab",
        "Move tab to new window": "Ctrl+Shift+N",
        "Move tab left": "Ctrl+Shift+PgUp",
        "Move tab right": "Ctrl+Shift+PgDown",
        "Activate tab 1": "Ctrl+1",
    }
    actions = {action.text(): action for action in host.findChildren(QAction)}
    for label, shortcut in expected.items():
        action = actions[label]
        assert action.shortcut() == QKeySequence(shortcut)
        assert action.shortcutContext() is Qt.ShortcutContext.WindowShortcut
        assert label in action.toolTip()
        assert not action.toolTip().endswith("()")


def test_inline_editor_undo_stays_local_and_tab_switch_commits_to_origin(
    owner: Workspace,
    qtbot: QtBot,
    sample_image: QImage,
    tmp_path: Path,
) -> None:
    """Text undo is local; switching focuses the new tab and commits old text once."""
    host = grouped(owner, sample_image, tmp_path)
    first, second = host.pages
    host.activate_page(first)
    host.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is host)
    first.edit_text(None, Point(5, 5))
    widget = first.canvas.editors.widget
    assert isinstance(widget, QPlainTextEdit)
    widget.setFocus()
    QTest.keyClicks(widget, "temporary")
    QTest.keyClick(widget, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert widget.toPlainText() == ""
    assert not first.record.session.history.can_undo
    QTest.keyClicks(widget, "kept")
    QTest.keyClick(widget, Qt.Key.Key_Tab, Qt.KeyboardModifier.ControlModifier)
    assert host.session is second.record.session
    assert len(first.record.session.document.annotations) == 1
    assert not second.record.session.document.annotations
    assert first.record.session.history.can_undo
    assert not second.record.session.history.can_undo
    assert second.canvas.hasFocus()
