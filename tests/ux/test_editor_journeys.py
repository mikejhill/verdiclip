"""Editor journeys from the UX contract, driven with real input events."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QImage, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QPlainTextEdit, QSpinBox
from pytestqt.qtbot import QtBot
from tests.editor.test_close_prompt import REAL_ASK, answer_when_open
from tests.ux.conftest import NO_MODS, Pixels, User

from verdiclip.editor.close_prompt import CloseChoice, ClosePrompt
from verdiclip.settings import OutputSettings

pytestmark = pytest.mark.ux

CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier


class TestQuickPath:
    """The shortest path from capture to clipboard."""

    def test_draw_arrow_then_enter_copies_and_closes(
        self, user: User, clipboard: Callable[[], QImage], qtbot: QtBot
    ) -> None:
        """A, drag, Esc, Esc, Enter → clipboard holds the arrow and the editor closes.

        UX-OUT-07, UX-TL-04.
        """
        user.snap("opened")
        user.key(Qt.Key.Key_A)
        user.drag((100, 250), (250, 120))
        user.snap("arrow drawn")
        user.key(Qt.Key.Key_Escape)
        user.key(Qt.Key.Key_Escape)

        user.key(Qt.Key.Key_Return)

        copied = clipboard()
        assert copied.size() == QImage(400, 300, QImage.Format.Format_RGB32).size()
        assert Pixels.is_reddish(Pixels.at(copied, 242, 127))
        qtbot.waitUntil(lambda: not user.window.isVisible())

    def test_ctrl_c_without_selection_copies_image(
        self, user: User, clipboard: Callable[[], QImage]
    ) -> None:
        """UX-OUT-01: Ctrl+C with nothing selected copies the whole image."""
        user.key(Qt.Key.Key_R)
        user.drag((10, 10), (100, 100))
        user.key(Qt.Key.Key_Escape)  # Skip the label
        user.key(Qt.Key.Key_Escape)  # Deselect

        user.shortcut("Ctrl+C")

        assert Pixels.is_reddish(Pixels.at(clipboard(), 10, 50))
        assert "copied" in user.status_text().lower()


class TestEscape:
    """Escape backs out exactly one level."""

    def test_escape_steps_back_one_level(self, user: User) -> None:
        """Esc closes the label editor, then deselects, then returns to Select.

        UX-SEL-09, UX-G-01, UX-TL-13.
        """
        user.key(Qt.Key.Key_R)
        user.drag((20, 20), (120, 120))
        session = user.window.session
        editors = user.window.canvas.editors
        assert editors.is_open, "drawing a box should open its label editor"

        user.key(Qt.Key.Key_Escape)
        after_first = (editors.is_open, len(session.selection))
        user.key(Qt.Key.Key_Escape)
        after_second = (len(session.selection), user.window.canvas.tool)
        user.key(Qt.Key.Key_Escape)

        assert after_first == (False, 1)
        assert after_second[0] == 0
        assert after_second[1] is not None
        assert after_second[1].label == "Rectangle"
        assert user.window.action("tool_select").isChecked()
        assert Pixels.is_reddish(Pixels.at(user.window.flattened(), 20, 70))

    def test_escape_finishes_text_without_losing_it(self, user: User) -> None:
        """UX-TL-05: Esc while typing commits the text."""
        user.key(Qt.Key.Key_T)
        user.click((60, 150))
        user.type_text("Look")
        user.snap("typing")

        user.key(Qt.Key.Key_Escape)

        notes = [
            a
            for a in user.window.session.document.annotations
            if a.kind.value == "text"
        ]
        assert [n.text for n in notes] == ["Look"]  # ty: ignore[unresolved-attribute]  # filtered to TextNote


class TestUndo:
    """Every change is undoable."""

    def test_draw_move_resize_nudge_delete_all_undo(self, user: User) -> None:
        """Each gesture is one undo step and undo restores the original.

        UX-G-03, UX-SEL-03..06.
        """
        original = user.window.flattened()
        user.key(Qt.Key.Key_R)
        user.drag((50, 100), (150, 180))
        user.key(Qt.Key.Key_Escape)  # Skip the label
        user.key(Qt.Key.Key_V)
        user.drag((100, 100), (130, 120))
        user.drag((180, 200), (220, 240))
        for _ in range(3):
            user.key(Qt.Key.Key_Right)
        user.snap("edited")
        user.key(Qt.Key.Key_Delete)
        history = user.window.session.history

        steps = 0
        while history.can_undo:
            user.shortcut("Ctrl+Z")
            steps += 1

        assert steps == 5  # draw, move, resize, nudge x3 (one step), delete
        assert user.window.flattened() == original
        for _ in range(steps):
            user.shortcut("Ctrl+Y")
        assert user.window.session.document.annotations == ()

    def test_undo_menu_names_the_change(self, user: User) -> None:
        """UX-G-03: the Undo action says what it will undo."""
        user.key(Qt.Key.Key_E)

        user.drag((10, 10), (60, 60))

        assert user.window.action("undo").text().replace("&", "") == "Undo Draw ellipse"


class TestCrop:
    """Crop is non-destructive."""

    def test_crop_then_undo_restores_everything(self, user: User) -> None:
        """Enter applies the crop; undo restores image and annotation positions.

        UX-TL-11.
        """
        user.key(Qt.Key.Key_A)
        user.drag((60, 200), (200, 150))
        before = user.window.flattened()
        user.key(Qt.Key.Key_C)
        user.drag((50, 50), (350, 250))
        user.snap("crop frame")

        user.key(Qt.Key.Key_Return)

        cropped = user.window.flattened()
        assert (cropped.width(), cropped.height()) == (300, 200)
        assert "300 × 200" in user.status_text()  # noqa: RUF001
        assert user.window.action("tool_select").isChecked()
        user.snap("cropped")
        user.shortcut("Ctrl+Z")
        assert user.window.flattened() == before

    def test_escape_cancels_crop_frame(self, user: User) -> None:
        """UX-TL-11: Esc discards the frame and keeps the image."""
        user.key(Qt.Key.Key_C)
        user.drag((50, 50), (150, 150))

        user.key(Qt.Key.Key_Escape)
        user.key(Qt.Key.Key_Return)

        assert user.window.flattened().size().width() == 400

    def test_crop_frame_is_clamped_to_image(self, user: User) -> None:
        """UX-TL-11: dragging past the edge crops at the edge, never enlarges."""
        user.key(Qt.Key.Key_C)
        user.drag((200, 150), (900, 900))

        user.key(Qt.Key.Key_Return)

        cropped = user.window.flattened()
        assert (cropped.width(), cropped.height()) == (200, 150)


class TestObfuscate:
    """Obfuscation always hides what is beneath it."""

    def test_moved_and_cropped_obfuscation_still_pixelates(self, user: User) -> None:
        """After moving and cropping, exported pixels beneath stay block-averaged.

        UX-TL-09.
        """
        user.key(Qt.Key.Key_O)
        user.drag((30, 30), (110, 70))
        user.key(Qt.Key.Key_V)
        user.drag((60, 50), (300, 210))  # Move it over the black square
        user.key(Qt.Key.Key_C)
        user.drag((250, 150), (399, 299))
        user.key(Qt.Key.Key_Return)
        user.snap("obfuscated after move and crop")

        exported = user.window.flattened()

        # The square's black/white edge (image x=300 → export x=50) falls inside one
        # pixelation block, so both sides of it now share one blended gray value.
        left = Pixels.at(exported, 49, 70)
        right = Pixels.at(exported, 50, 70)
        assert left == right, "the original sharp edge is still visible"
        assert 0 < left.lightness() < 255


class TestTextAndCounters:
    """Text and numbered markers."""

    def test_backspace_edits_text_and_empty_text_is_removed(self, user: User) -> None:
        """Backspace deletes characters; clearing the text deletes the note.

        UX-TL-05.
        """
        user.key(Qt.Key.Key_T)
        user.click((60, 150))
        user.type_text("Hix")
        user.key(Qt.Key.Key_Backspace)
        user.key(Qt.Key.Key_Escape)
        notes = user.window.session.document.annotations
        assert notes[0].text == "Hi"  # ty: ignore[unresolved-attribute]  # only a TextNote exists

        user.key(Qt.Key.Key_V)
        user.double_click((70, 160))
        editor = QApplication.focusWidget()
        assert isinstance(editor, QPlainTextEdit)
        editor.selectAll()
        user.key(Qt.Key.Key_Delete)
        user.key(Qt.Key.Key_Escape)

        assert user.window.session.document.annotations == ()

    def test_counters_number_themselves_and_restart_after_text_label(
        self, user: User
    ) -> None:
        """UX-TL-06, UX-TL-07: 1, 2, 3; relabel 3 to "A"; the next counter is 1."""
        user.key(Qt.Key.Key_N)
        for x in (60, 120, 180):
            user.click((x, 150))
        user.key(Qt.Key.Key_Escape)
        user.key(Qt.Key.Key_Escape)

        user.double_click((180, 150))
        user.type_text("A")
        user.key(Qt.Key.Key_Return)
        user.key(Qt.Key.Key_N)
        user.click((240, 150))
        user.snap("counters")

        labels = [a.label for a in user.window.session.document.annotations]  # ty: ignore[unresolved-attribute]  # all counters
        assert labels == ["1", "2", "A", "1"]


class TestSelection:
    """Selecting, moving, resizing, and styling."""

    def test_rubber_band_selects_and_moves_together(self, user: User) -> None:
        """UX-SEL-02, UX-SEL-03: box-select two shapes and drag them as one."""
        user.key(Qt.Key.Key_R)
        user.drag((20, 120), (60, 160))
        user.drag((80, 120), (120, 160))
        user.key(Qt.Key.Key_Escape)  # Skip the label
        user.key(Qt.Key.Key_V)
        user.drag((5, 105), (130, 170))
        assert len(user.window.session.selection) == 2

        user.drag((40, 120), (40, 220))

        exported = user.window.flattened()
        assert Pixels.is_reddish(Pixels.at(exported, 20, 240))
        assert Pixels.is_reddish(Pixels.at(exported, 80, 240))
        assert Pixels.is_white(Pixels.at(exported, 20, 140))

    def test_ctrl_a_then_delete_clears_everything(self, user: User) -> None:
        """UX-SEL-02, UX-SEL-06: Ctrl+A selects all, Delete removes all."""
        user.key(Qt.Key.Key_L)
        user.drag((10, 10), (100, 10))
        user.drag((10, 30), (100, 30))

        user.shortcut("Ctrl+A")
        user.key(Qt.Key.Key_Delete)

        assert user.window.session.document.annotations == ()

    def test_style_change_applies_to_selection_and_becomes_default(
        self, user: User
    ) -> None:
        """Changing width restyles the selection in one undo step and sets the default.

        UX-SEL-07.
        """
        user.key(Qt.Key.Key_R)
        user.drag((50, 100), (150, 180))
        spin = next(
            box
            for box in user.window.findChildren(QSpinBox)
            if box.toolTip() == "Line width in pixels"
        )

        spin.setValue(9)

        rect = user.window.session.document.annotations[0]
        assert rect.style.width == 9
        assert user.window.action("undo").text().replace("&", "") == "Undo Change style"
        user.drag((200, 100), (300, 180))
        assert user.window.session.document.annotations[1].style.width == 9

    def test_shift_constrains_rectangle_to_square(self, user: User) -> None:
        """UX-TL-02: Shift-drag draws a square."""
        user.key(Qt.Key.Key_R)

        user.drag((20, 20), (120, 70), SHIFT)

        bounds = user.window.session.document.annotations[0].bounds
        assert round(bounds.width) == round(bounds.height)


class TestCopyPaste:
    """Element copy and paste."""

    def test_paste_into_another_editor_offsets_copy(
        self, open_editor: Callable[[QImage], User], sample_image: QImage
    ) -> None:
        """Ctrl+C in one editor, Ctrl+V in another → offset copy, selected.

        UX-SEL-08.
        """
        first = open_editor(sample_image)
        first.key(Qt.Key.Key_E)
        first.drag((20, 20), (80, 80))
        first.key(Qt.Key.Key_Escape)  # Skip the label
        first.shortcut("Ctrl+C")
        second = open_editor(sample_image)

        second.shortcut("Ctrl+V")

        pasted = second.window.session.document.annotations
        assert len(pasted) == 1
        assert pasted[0].bounds.x == pytest.approx(
            first.window.session.document.annotations[0].bounds.x + 10
        )
        assert len(second.window.session.selection) == 1


class TestNavigation:
    """Zoom and scroll."""

    def test_ctrl_wheel_keeps_point_under_cursor(
        self, open_editor: Callable[[QImage], User]
    ) -> None:
        """Zooming with Ctrl+wheel keeps the image point under the cursor fixed.

        UX-NAV-01.
        """
        large = QImage(3000, 2000, QImage.Format.Format_RGB32)
        large.fill(Qt.GlobalColor.white)
        user = open_editor(large)
        user.shortcut("Ctrl+0")
        canvas = user.window.canvas
        anchor = user.view_point(1500, 900)
        before = canvas.to_image(QPointF(anchor))

        for _ in range(3):
            event = QWheelEvent(
                QPointF(anchor),
                QPointF(canvas.mapToGlobal(anchor)),
                QPoint(0, 0),
                QPoint(0, 120),
                Qt.MouseButton.NoButton,
                CTRL,
                Qt.ScrollPhase.NoScrollPhase,
                False,  # noqa: FBT003 — Qt's positional 'inverted'
            )
            QApplication.sendEvent(canvas.viewport(), event)

        after = canvas.to_image(QPointF(anchor))
        assert canvas.zoom > 1.9
        assert after.distance_to(before) < 1.5

    def test_zoom_shortcuts_and_status(self, user: User) -> None:
        """Ctrl+= zooms in, Ctrl+0 resets, status shows size and zoom.

        UX-NAV-02, UX-NAV-05.
        """
        user.shortcut("Ctrl+=")
        assert user.window.canvas.zoom > 1.0

        user.shortcut("Ctrl+0")

        assert user.window.canvas.zoom == 1.0
        assert "100%" in user.status_text()
        assert "400 × 300" in user.status_text()  # noqa: RUF001


class TestDiscoverability:
    """Every action is discoverable."""

    @pytest.mark.parametrize(
        ("key", "tool"),
        [
            (Qt.Key.Key_V, "select"),
            (Qt.Key.Key_C, "crop"),
            (Qt.Key.Key_R, "rectangle"),
            (Qt.Key.Key_E, "ellipse"),
            (Qt.Key.Key_L, "line"),
            (Qt.Key.Key_A, "arrow"),
            (Qt.Key.Key_T, "text"),
            (Qt.Key.Key_N, "counter"),
            (Qt.Key.Key_H, "highlight"),
            (Qt.Key.Key_O, "obfuscate"),
            (Qt.Key.Key_F, "freehand"),
        ],
    )
    def test_tool_shortcut_activates_tool(
        self, user: User, key: Qt.Key, tool: str
    ) -> None:
        """Each single-letter shortcut activates its tool and the toolbar shows it.

        UX-TL-01.
        """
        user.key(key)

        assert user.window.action(f"tool_{tool}").isChecked()

    def test_every_action_shows_its_shortcut(self, user: User) -> None:
        """UX-G-02: tooltips name the shortcut for every action that has one."""
        names = [
            "save",
            "save_as",
            "copy_image",
            "print",
            "undo",
            "redo",
            "zoom_fit",
            "tool_arrow",
        ]

        tips = {n: user.window.action(n).toolTip() for n in names}

        assert all("(" in tip and ")" in tip for tip in tips.values()), tips


class TestSaving:
    """Saving and closing."""

    def test_ctrl_s_saves_with_pattern_then_overwrites(
        self, user: User, tmp_path: Path
    ) -> None:
        """UX-OUT-02: first Ctrl+S picks a name; the second overwrites the same file."""
        user.shortcut("Ctrl+S")
        saved = sorted((tmp_path / "out").glob("*.png"))
        assert len(saved) == 1
        assert saved[0].name in user.window.windowTitle()

        user.key(Qt.Key.Key_R)
        user.drag((10, 10), (90, 90))
        user.shortcut("Ctrl+S")

        assert sorted((tmp_path / "out").glob("*.png")) == saved
        assert Pixels.is_reddish(QImage(str(saved[0])).pixelColor(10, 50))

    def test_save_failure_is_reported_and_work_kept(
        self,
        open_editor: Callable[[QImage], User],
        sample_image: QImage,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A failed save shows a message and the editor stays open with its work.

        UX-OUT-04.
        """
        blocker = tmp_path / "out"
        blocker.write_text("not a folder", encoding="utf-8")
        shown: list[str] = []
        monkeypatch.setattr(
            QMessageBox, "warning", lambda *args: shown.append(str(args[2]))
        )
        current = open_editor(sample_image)
        current.key(Qt.Key.Key_R)
        current.drag((10, 10), (90, 90))

        current.shortcut("Ctrl+S")

        assert shown
        assert "folder" in shown[0].lower()
        assert current.window.isVisible()
        assert not current.window.session.history.is_delivered

    def test_close_with_undelivered_changes_asks(
        self, user: User, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """UX-G-06: Cancel keeps the editor open; after copying, closing doesn't ask."""
        asked: list[str] = []

        def cancel(prompt: ClosePrompt, _parent: object) -> CloseChoice:
            asked.append(prompt.message)
            return CloseChoice.CANCEL

        monkeypatch.setattr(ClosePrompt, "ask", cancel)
        user.key(Qt.Key.Key_R)
        user.drag((10, 10), (90, 90))

        user.window.close()
        assert asked
        assert user.window.isVisible()

        user.shortcut("Ctrl+Shift+C")
        user.window.close()
        assert len(asked) == 1
        assert not user.window.isVisible()


class TestPanning:
    """Moving around a large image."""

    @pytest.fixture
    def large_user(self, open_editor: Callable[[QImage], User]) -> User:
        """An editor at 100% on an image larger than the window."""
        large = QImage(3000, 2000, QImage.Format.Format_RGB32)
        large.fill(Qt.GlobalColor.white)
        user = open_editor(large)
        user.shortcut("Ctrl+0")
        return user

    def test_space_drag_pans(self, large_user: User) -> None:
        """UX-NAV-04: hold Space and drag to pan; nothing is drawn."""
        canvas = large_user.window.canvas
        viewport = canvas.viewport()
        before = canvas.horizontalScrollBar().value()
        start = QPoint(400, 300)
        QTest.keyPress(canvas, Qt.Key.Key_Space)

        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, NO_MODS, start)
        QTest.mouseMove(viewport, start - QPoint(100, 0))
        QTest.mouseRelease(
            viewport, Qt.MouseButton.LeftButton, NO_MODS, start - QPoint(100, 0)
        )
        QTest.keyRelease(canvas, Qt.Key.Key_Space)

        assert canvas.horizontalScrollBar().value() == before + 100
        assert large_user.window.session.document.annotations == ()

    def test_middle_drag_pans(self, large_user: User) -> None:
        """UX-NAV-04: middle-button drag pans."""
        canvas = large_user.window.canvas
        viewport = canvas.viewport()
        before = canvas.verticalScrollBar().value()
        start = large_user.view_point(1500, 1000)

        QTest.mousePress(
            viewport, Qt.MouseButton.MiddleButton, Qt.KeyboardModifier.NoModifier, start
        )
        QTest.mouseMove(viewport, start - QPoint(0, 80))
        QTest.mouseRelease(
            viewport,
            Qt.MouseButton.MiddleButton,
            Qt.KeyboardModifier.NoModifier,
            start - QPoint(0, 80),
        )

        assert canvas.verticalScrollBar().value() == before + 80

    def test_shift_wheel_scrolls_horizontally(self, large_user: User) -> None:
        """UX-NAV-03: Shift+wheel scrolls sideways."""
        canvas = large_user.window.canvas
        before = canvas.horizontalScrollBar().value()
        pos = QPointF(large_user.view_point(1500, 1000))
        event = QWheelEvent(
            pos,
            pos,
            QPoint(0, 0),
            QPoint(0, -120),
            Qt.MouseButton.NoButton,
            SHIFT,
            Qt.ScrollPhase.NoScrollPhase,
            False,  # noqa: FBT003 — Qt's positional 'inverted'
        )

        QApplication.sendEvent(canvas.viewport(), event)

        assert canvas.horizontalScrollBar().value() == before + 120

    def test_status_shows_pointer_position(self, large_user: User) -> None:
        """UX-NAV-05: the status bar shows the pointer position in image pixels."""
        viewport = large_user.window.canvas.viewport()
        spot = QPoint(300, 200)
        expected = large_user.window.canvas.to_image(QPointF(spot))

        QTest.mouseMove(viewport, spot)

        assert f"{int(expected.x)}, {int(expected.y)}" in large_user.status_text()


class TestDropToOpen:
    """Opening images by dropping files."""

    def test_dropping_an_image_requests_an_editor(
        self, user: User, tmp_path: Path, qtbot: QtBot
    ) -> None:
        """UX-OUT-06: dropping image files on the editor asks to open each one."""
        picture = tmp_path / "dropped.png"
        QImage(10, 10, QImage.Format.Format_RGB32).save(str(picture))
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(picture))])
        point = QPointF(100, 100)
        enter = QDragEnterEvent(
            point.toPoint(),
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        drop = QDropEvent(
            point,
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )

        QApplication.sendEvent(user.window, enter)
        with qtbot.waitSignal(user.window.open_image_requested) as signal:
            QApplication.sendEvent(user.window, drop)

        assert enter.isAccepted()
        assert signal.args[0] == picture


class TestStableChrome:
    """The window layout does not jump around."""

    def test_switching_tools_and_selecting_never_moves_canvas(self, user: User) -> None:
        """UX-G-07: the canvas stays put across every tool and selection change."""
        canvas = user.window.canvas
        positions = set()
        user.key(Qt.Key.Key_R)
        user.drag((20, 20), (120, 120))
        for key in (
            Qt.Key.Key_V,
            Qt.Key.Key_C,
            Qt.Key.Key_R,
            Qt.Key.Key_E,
            Qt.Key.Key_L,
            Qt.Key.Key_A,
            Qt.Key.Key_T,
            Qt.Key.Key_N,
            Qt.Key.Key_H,
            Qt.Key.Key_O,
            Qt.Key.Key_F,
            Qt.Key.Key_V,
        ):
            user.key(key)
            QApplication.processEvents()
            positions.add(canvas.mapTo(user.window, QPoint(0, 0)).y())
        user.click((70, 20))
        QApplication.processEvents()
        positions.add(canvas.mapTo(user.window, QPoint(0, 0)).y())

        assert len(positions) == 1, positions


class TestBoxLabels:
    """Typing text into rectangles and ellipses."""

    def test_double_click_inside_hollow_rectangle_types_a_label(
        self, user: User
    ) -> None:
        """UX-TL-13: double-click inside a hollow rectangle, type a centered label."""
        user.key(Qt.Key.Key_R)
        user.drag((100, 100), (300, 200))
        before = user.window.flattened()

        user.double_click((200, 150))
        user.type_text("Login")
        user.snap("typing a label")
        user.key(Qt.Key.Key_Escape)
        user.snap("label committed")

        box = user.window.session.document.annotations[0]
        assert getattr(box, "text", None) == "Login"
        after = user.window.flattened()
        center_changed = any(
            after.pixelColor(x, y) != before.pixelColor(x, y)
            for x in range(170, 231)
            for y in range(140, 161)
        )
        assert center_changed, "label text should be drawn near the box center"
        assert user.window.action("undo").text().replace("&", "") == "Undo Edit label"

    def test_enter_on_selected_box_edits_and_clearing_removes_label(
        self, user: User
    ) -> None:
        """UX-TL-13, UX-OUT-07: Enter edits the selected box label; empty clears it."""
        user.key(Qt.Key.Key_E)
        user.drag((100, 100), (300, 220))
        user.key(Qt.Key.Key_Return)
        user.type_text("Two words")
        user.key(Qt.Key.Key_Escape)
        assert user.window.isVisible(), "Enter must edit, not copy-and-close"

        user.key(Qt.Key.Key_F2)
        editor = QApplication.focusWidget()
        assert editor is not None
        user.shortcut("Ctrl+A")
        user.key(Qt.Key.Key_Delete)
        user.key(Qt.Key.Key_Escape)

        assert getattr(user.window.session.document.annotations[0], "text", None) == ""

    def test_label_wraps_inside_the_box(self, user: User) -> None:
        """UX-TL-13: long labels wrap to the box width instead of overflowing it."""
        user.key(Qt.Key.Key_R)
        user.drag((150, 100), (250, 220))
        user.double_click((200, 160))
        user.type_text("a long label that must wrap")
        user.key(Qt.Key.Key_Escape)

        exported = user.window.flattened()

        outside = [exported.pixelColor(x, 160) for x in (120, 140, 260, 280)]
        assert all(Pixels.is_white(c) for c in outside)


class TestLabelFirstBoxes:
    """Drawing a box goes straight to typing its label."""

    def test_draw_then_type_labels_the_new_box(self, user: User) -> None:
        """UX-TL-13: after drawing a rectangle, typing goes straight into it."""
        user.key(Qt.Key.Key_R)
        user.drag((100, 100), (300, 200))

        user.type_text("Step 1")
        user.key(Qt.Key.Key_Escape)

        assert getattr(user.window.session.document.annotations[0], "text", None) == (
            "Step 1"
        )

    def test_undo_right_after_drawing_removes_the_box(self, user: User) -> None:
        """UX-G-03: Ctrl+Z with the empty label editor open undoes the box itself."""
        user.key(Qt.Key.Key_R)
        user.drag((100, 100), (300, 200))

        user.shortcut("Ctrl+Z")

        assert user.window.session.document.annotations == ()
        assert not user.window.canvas.editors.is_open

    def test_undo_while_typing_edits_text_first(self, user: User) -> None:
        """Ctrl+Z while typing a label undoes typing, not the box."""
        user.key(Qt.Key.Key_R)
        user.drag((100, 100), (300, 200))
        user.type_text("abc")

        user.shortcut("Ctrl+Z")

        assert len(user.window.session.document.annotations) == 1
        assert user.window.canvas.editors.is_open


@pytest.mark.usefixtures("real_close_prompt")
class TestClosePromptKeyboard:
    """UX-G-06: the real prompt answered from the keyboard, end to end."""

    @pytest.fixture
    def real_close_prompt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Let the real dialog run instead of the suite-wide automatic answer."""
        monkeypatch.setattr(ClosePrompt, "ask", REAL_ASK)

    def _edit_then_close(self, user: User, key: Qt.Key) -> None:
        """Draw a box, press Ctrl+W, and answer the prompt with ``key``."""
        user.key(Qt.Key.Key_R)
        user.drag((10, 10), (90, 90))
        user.key(Qt.Key.Key_Escape)  # Skip the label
        answer_when_open(lambda box: QTest.keyClick(box, key))
        user.shortcut("Ctrl+W")

    def test_n_closes_without_saving(
        self, user: User, output_settings: OutputSettings, qtbot: QtBot
    ) -> None:
        """Ctrl+W then N: the editor closes and nothing is written."""
        self._edit_then_close(user, Qt.Key.Key_N)

        qtbot.waitUntil(lambda: not user.window.isVisible())
        assert not any(output_settings.directory.glob("*"))

    def test_s_saves_then_closes(
        self, user: User, output_settings: OutputSettings, qtbot: QtBot
    ) -> None:
        """Ctrl+W then S: the image is saved to the output folder and closes."""
        self._edit_then_close(user, Qt.Key.Key_S)

        qtbot.waitUntil(lambda: not user.window.isVisible())
        saved = list(output_settings.directory.glob("*.png"))
        assert len(saved) == 1
        assert Pixels.is_reddish(QImage(str(saved[0])).pixelColor(10, 50))

    def test_esc_keeps_working(self, user: User) -> None:
        """Ctrl+W then Esc: the editor and its edits stay."""
        self._edit_then_close(user, Qt.Key.Key_Escape)

        assert user.window.isVisible()
        assert len(user.window.session.document.annotations) == 1
        # Teardown closes the editor; don't let the real prompt block it
        user.window.session.history.mark_delivered()
