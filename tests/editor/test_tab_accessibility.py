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

from verdiclip.document.annotations import CounterMarker, RectangleShape, TextNote
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


@pytest.fixture
def host(owner: Workspace, sample_image: QImage, tmp_path: Path) -> EditorWindow:
    """An active two-document host, disposed by the owning workspace fixture."""
    return grouped(owner, sample_image, tmp_path)


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
        "Next tab": ["Ctrl+Tab", "Ctrl+PgDown"],
        "Previous tab": ["Ctrl+Shift+Tab", "Ctrl+PgUp"],
        "Move tab to new window": ["Ctrl+Shift+N"],
        "Move tab left": ["Ctrl+Shift+PgUp"],
        "Move tab right": ["Ctrl+Shift+PgDown"],
        "Activate tab 1": ["Ctrl+1"],
    }
    actions = {action.text(): action for action in host.findChildren(QAction)}
    for label, shortcuts in expected.items():
        action = actions[label]
        assert action.shortcuts() == [QKeySequence(key) for key in shortcuts]
        for key in action.shortcuts():
            assert (
                key.toString(QKeySequence.SequenceFormat.NativeText) in action.toolTip()
            )
        assert action.shortcutContext() is Qt.ShortcutContext.WindowShortcut
        assert label in action.toolTip()
        assert not action.toolTip().endswith("()")


@pytest.mark.parametrize(
    "key", [Qt.Key.Key_Tab, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown]
)
def test_inline_editor_undo_stays_local_and_tab_switch_commits_to_origin(
    owner: Workspace,
    qtbot: QtBot,
    sample_image: QImage,
    tmp_path: Path,
    key: Qt.Key,
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
    QTest.keyClick(widget, key, Qt.KeyboardModifier.ControlModifier)
    assert host.session is second.record.session
    assert len(first.record.session.document.annotations) == 1
    assert not second.record.session.document.annotations
    assert first.record.session.history.can_undo
    assert not second.record.session.history.can_undo
    assert second.canvas.hasFocus()


@pytest.mark.parametrize("focus", ["canvas", "tab_bar"])
@pytest.mark.parametrize(
    ("key", "step"), [(Qt.Key.Key_PageUp, -1), (Qt.Key.Key_PageDown, 1)]
)
def test_page_shortcuts_switch_and_wrap_without_reordering(
    host: EditorWindow,
    qtbot: QtBot,
    focus: str,
    key: Qt.Key,
    step: int,
) -> None:
    """Page shortcuts visit each tab once, wrap and leave tab order untouched."""
    workspace = host.workspace
    assert isinstance(workspace, Workspace)
    workspace.open_document(
        EditorSession(Document(host.session.document.image), EditorSettings()),
        host.pages[0].record.delivery,
        EditorOptions(title="Third capture"),
    )
    pages = host.pages
    host.activate_page(pages[0])
    host.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is host)
    for count in range(1, 7):
        target = host.canvas if focus == "canvas" else host.tab_bar
        target.setFocus()
        QTest.keyClick(target, key, Qt.KeyboardModifier.ControlModifier)
        assert host.session is pages[(count * step) % 3].record.session
        assert host.pages == pages


@pytest.mark.parametrize(
    ("key", "step"), [(Qt.Key.Key_PageUp, -1), (Qt.Key.Key_PageDown, 1)]
)
def test_shift_page_shortcuts_move_active_tab_and_stop_at_edge(
    host: EditorWindow,
    qtbot: QtBot,
    key: Qt.Key,
    step: int,
) -> None:
    """Moving retains the active document and cannot wrap past either edge."""
    pages = host.pages
    active = pages[1 if step < 0 else 0]
    host.activate_page(active)
    host.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is host)
    for _ in range(3):
        host.canvas.setFocus()
        QTest.keyClick(
            host.canvas,
            key,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
        )
        assert host.pages == tuple(reversed(pages))
        assert host.session is active.record.session
        assert host.tab_bar.currentIndex() == (0 if step < 0 else 1)


def test_page_shortcuts_leave_single_tab_in_place(
    owner: Workspace,
    qtbot: QtBot,
    sample_image: QImage,
    tmp_path: Path,
) -> None:
    """All four page shortcuts are harmless with only one document open."""
    host = owner.open_document(
        EditorSession(Document(sample_image), EditorSettings()),
        ImageDelivery(OutputSettings(directory=tmp_path)),
        EditorOptions(),
    )
    page = host.pages[0]
    host.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is host)
    host.canvas.setFocus()
    for key in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
        for mods in (
            Qt.KeyboardModifier.ControlModifier,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
        ):
            QTest.keyClick(host.canvas, key, mods)
            assert host.pages == (page,)
            assert host.session is page.record.session
            assert host.canvas.hasFocus()


@pytest.mark.parametrize("kind", ["text", "box", "counter"])
@pytest.mark.parametrize("key", [Qt.Key.Key_PageUp, Qt.Key.Key_PageDown])
@pytest.mark.parametrize("reorder", [False, True])
def test_page_shortcuts_preserve_focused_inline_edits(
    host: EditorWindow,
    qtbot: QtBot,
    kind: str,
    key: Qt.Key,
    *,
    reorder: bool,
) -> None:
    """All inline editor kinds yield tab commands without losing typed content."""
    pages = host.pages
    original = pages[1 if key == Qt.Key.Key_PageUp else 0]
    other = next(page for page in pages if page is not original)
    host.activate_page(original)
    host.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is host)
    if kind == "text":
        original.edit_text(None, Point(5, 5))
    elif kind == "box":
        box = RectangleShape(rect=Rect(5, 5, 100, 80))
        original.record.session.history.execute(AddAnnotations([box], "Draw box"))
        original.edit_box_text(box)
    else:
        marker = CounterMarker(center=Point(40, 40), radius=20, label="1")
        original.record.session.history.execute(AddAnnotations([marker], "Add counter"))
        original.edit_counter(marker)
    widget = original.canvas.editors.widget
    assert widget is not None
    widget.setFocus()
    QTest.keyClicks(widget, "kept")
    modifiers = Qt.KeyboardModifier.ControlModifier
    if reorder:
        modifiers |= Qt.KeyboardModifier.ShiftModifier
    QTest.keyClick(widget, key, modifiers)
    assert host.pages == (tuple(reversed(pages)) if reorder else pages)
    assert host.session is (original if reorder else other).record.session
    assert not original.canvas.editors.is_open
    annotation = original.record.session.document.annotations[0]
    assert isinstance(annotation, (TextNote, RectangleShape, CounterMarker))
    assert (
        annotation.label if isinstance(annotation, CounterMarker) else annotation.text
    ) == "kept"
    assert not other.record.session.document.annotations
    original.record.session.history.undo()
    if kind == "text":
        assert not original.record.session.document.annotations
    else:
        restored = original.record.session.document.annotations[0]
        assert isinstance(restored, (RectangleShape, CounterMarker))
        assert (
            restored.label if isinstance(restored, CounterMarker) else restored.text
        ) == ("1" if kind == "counter" else "")


@pytest.mark.parametrize("key", [Qt.Key.Key_PageUp, Qt.Key.Key_PageDown])
def test_reorder_at_edge_leaves_pending_text_open(
    host: EditorWindow, qtbot: QtBot, key: Qt.Key
) -> None:
    """A clamped move is a no-op, including the current inline typing session."""
    pages = host.pages
    page = pages[0 if key == Qt.Key.Key_PageUp else -1]
    host.activate_page(page)
    host.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is host)
    page.edit_text(None, Point(5, 5))
    widget = page.canvas.editors.widget
    assert isinstance(widget, QPlainTextEdit)
    widget.setFocus()
    QTest.keyClicks(widget, "still typing")
    QTest.keyClick(
        widget,
        key,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
    )
    assert host.pages == pages
    assert host.session is page.record.session
    assert page.canvas.editors.widget is widget
    assert widget.hasFocus()
    assert widget.toPlainText() == "still typing"
    assert not page.record.session.history.can_undo
