"""Image journeys: rotate, flip, and resize from the keyboard, menus, and toolbar."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QDialogButtonBox,
    QMenu,
    QSpinBox,
    QToolBar,
    QToolButton,
)
from pytestqt.qtbot import QtBot
from tests.ux.conftest import Pixels, User

from verdiclip.document.annotations import RectangleShape
from verdiclip.editor.resize_dialog import ResizeDialog
from verdiclip.geometry import Rect

pytestmark = pytest.mark.ux

BLACK = QColor("black")
TIMES = "×"  # noqa: RUF001 — the sign used in size readouts


def size_of(image: QImage) -> tuple[int, int]:
    """Return (width, height)."""
    return image.width(), image.height()


def with_resize_dialog(action: Callable[[ResizeDialog], None]) -> None:
    """Run ``action`` on the Resize dialog as soon as it opens."""

    def handle() -> None:
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, ResizeDialog), "the Resize dialog should be open"
        action(dialog)

    QTimer.singleShot(0, handle)


def press_ok(dialog: ResizeDialog) -> None:
    """Click the dialog's OK button."""
    box = dialog.findChild(QDialogButtonBox)
    assert box is not None
    ok = box.button(QDialogButtonBox.StandardButton.Ok)
    QTest.mouseClick(ok, Qt.MouseButton.LeftButton)


def type_into(box: QSpinBox, text: str) -> None:
    """Replace a spin box's text the way a user would."""
    box.setFocus()
    QTest.keyClick(box, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClicks(box, text)


class TestRotate:
    """Quarter turns from the keyboard."""

    def test_ctrl_r_rotates_right_and_ctrl_z_restores(self, user: User) -> None:
        """UX-IMG-01: Ctrl+R turns the image clockwise; Ctrl+Z undoes it exactly."""
        before = user.window.flattened()

        user.shortcut("Ctrl+R")
        user.snap("rotated right")
        rotated = user.window.flattened()

        assert size_of(rotated) == (300, 400)
        assert rotated.pixelColor(75, 325) == BLACK
        on_screen = user.view_point(75, 325)
        assert user.screen_pixels().pixelColor(on_screen) == BLACK
        assert f"Rotate right: 300 {TIMES} 400 px" in user.status_text()
        assert user.window.action("undo").text().replace("&", "") == (
            "Undo Rotate right"
        )
        user.shortcut("Ctrl+Z")
        assert user.window.flattened() == before
        on_screen = user.view_point(325, 225)
        assert user.screen_pixels().pixelColor(on_screen) == BLACK

    def test_ctrl_shift_r_rotates_left(self, user: User) -> None:
        """UX-IMG-01: Ctrl+Shift+R turns the image counter-clockwise."""
        user.shortcut("Ctrl+Shift+R")

        rotated = user.window.flattened()
        assert size_of(rotated) == (300, 400)
        assert rotated.pixelColor(225, 400 - 325) == BLACK

    def test_annotations_turn_with_the_image_and_stay_selected(
        self, user: User
    ) -> None:
        """UX-IMG-01: a drawn box rotates with the pixels and keeps its selection."""
        user.key(Qt.Key.Key_R)
        user.drag((20, 20), (120, 70))
        user.key(Qt.Key.Key_Escape)  # Skip the label; the box stays selected
        selected = user.window.session.selection.ids

        user.shortcut("Ctrl+R")
        user.snap("box rotated")

        box = user.window.session.document.annotations[0]
        assert isinstance(box, RectangleShape)
        assert box.rect == Rect(230, 20, 50, 100)
        assert user.window.session.selection.ids == selected
        assert Pixels.is_reddish(user.window.flattened().pixelColor(230, 70))

    def test_open_label_is_committed_before_rotating(self, user: User) -> None:
        """UX-IMG-01: typing a label then rotating keeps the label."""
        user.key(Qt.Key.Key_R)
        user.drag((100, 100), (300, 200))
        user.type_text("Keep me")

        user.shortcut("Ctrl+R")

        box = user.window.session.document.annotations[0]
        assert getattr(box, "text", None) == "Keep me"
        assert size_of(user.window.flattened()) == (300, 400)


class TestFlipFromTheToolbar:
    """The toolbar's Image button holds every image command."""

    def image_button(self, user: User) -> QToolButton:
        """Return the toolbar button for the Image menu."""
        for bar in user.window.findChildren(QToolBar):
            widget = bar.widgetForAction(user.window.action("image"))
            if isinstance(widget, QToolButton):
                return widget
        msg = "no Image toolbar button"
        raise AssertionError(msg)

    def test_image_button_lists_rotate_flip_and_resize(self, user: User) -> None:
        """UX-IMG-02: one click opens a menu naming each command and its keys."""
        button = self.image_button(user)
        menu = user.window.action("image").menu()

        assert button.popupMode() is QToolButton.ToolButtonPopupMode.InstantPopup
        assert button.isVisible()
        assert isinstance(menu, QMenu)
        labels = [a.text().replace("&", "") for a in menu.actions()]
        assert labels == [
            "Rotate left",
            "Rotate right",
            "Flip horizontally",
            "Flip vertically",
            "Resize…",
        ]
        assert "Ctrl+R" in user.window.action("rotate_right").toolTip()

    @pytest.mark.parametrize(
        ("name", "expected"),
        [("flip_horizontal", (400 - 325, 225)), ("flip_vertical", (325, 300 - 225))],
    )
    def test_flip_from_menu_mirrors_the_image(
        self, user: User, name: str, expected: tuple[int, int]
    ) -> None:
        """UX-IMG-02: choosing a flip mirrors the image in place."""
        menu = user.window.action("image").menu()
        assert isinstance(menu, QMenu)
        action = next(a for a in menu.actions() if a is user.window.action(name))

        action.trigger()
        user.snap(name)

        assert user.window.flattened().pixelColor(*expected) == BLACK

    def test_flip_shortcuts(self, user: User) -> None:
        """UX-IMG-02: Ctrl+Shift+H and Ctrl+Shift+V flip; doing both is a 180° turn."""
        user.shortcut("Ctrl+Shift+H")
        user.shortcut("Ctrl+Shift+V")

        assert user.window.flattened().pixelColor(400 - 325, 300 - 225) == BLACK
        assert user.window.action("undo").text().replace("&", "") == (
            "Undo Flip vertically"
        )


class TestResize:
    """Resizing through the dialog."""

    def test_width_keeps_aspect_and_resizes(
        self, user: User, clipboard: Callable[[], QImage]
    ) -> None:
        """UX-IMG-03: typing a width fills in height and percent; OK resizes."""
        seen: dict[str, int] = {}

        def fill(dialog: ResizeDialog) -> None:
            type_into(dialog.width_box, "200")
            seen["height"] = dialog.height_box.value()
            seen["percent"] = dialog.percent_box.value()
            press_ok(dialog)

        with_resize_dialog(fill)
        user.shortcut("Ctrl+Alt+I")
        user.snap("resized to half")

        assert seen == {"height": 150, "percent": 50}
        assert f"Resize: 200 {TIMES} 150 px" in user.status_text()
        user.window.copy_image()
        copied = clipboard()
        assert size_of(copied) == (200, 150)
        assert copied.pixelColor(162, 112) == BLACK

    def test_resize_uses_the_cropped_area(self, user: User) -> None:
        """UX-IMG-03: the dialog starts at the cropped size and keeps only that."""
        user.window.session.document.set_crop(Rect(300, 200, 50, 50))
        seen: list[tuple[int, int]] = []

        def double(dialog: ResizeDialog) -> None:
            seen.append(dialog.result_size())
            type_into(dialog.percent_box, "200")
            press_ok(dialog)

        with_resize_dialog(double)
        user.window.action("resize").trigger()

        result = user.window.flattened()
        assert seen == [(50, 50)]
        assert size_of(result) == (100, 100)
        assert result.pixelColor(50, 50) == BLACK

    def test_unlinked_sides_change_independently(self, user: User) -> None:
        """UX-IMG-03: unticking "Keep aspect ratio" lets width and height differ."""

        def stretch(dialog: ResizeDialog) -> None:
            dialog.keep_aspect_box.setChecked(False)
            type_into(dialog.width_box, "800")
            press_ok(dialog)

        with_resize_dialog(stretch)
        user.shortcut("Ctrl+Alt+I")

        assert size_of(user.window.flattened()) == (800, 300)

    def test_relinking_restores_the_aspect(self, user: User) -> None:
        """UX-IMG-03: ticking the box again recomputes the height from the width."""
        seen: list[int] = []

        def relink(dialog: ResizeDialog) -> None:
            dialog.keep_aspect_box.setChecked(False)
            type_into(dialog.height_box, "10")
            dialog.keep_aspect_box.setChecked(True)
            seen.append(dialog.height_box.value())
            dialog.reject()

        with_resize_dialog(relink)
        user.shortcut("Ctrl+Alt+I")

        assert seen == [300]

    @pytest.mark.parametrize("finish", [ResizeDialog.reject, press_ok])
    def test_cancel_or_same_size_changes_nothing(
        self, user: User, finish: Callable[[ResizeDialog], None]
    ) -> None:
        """UX-IMG-03: Cancel, or OK without changes, adds no undo step."""
        with_resize_dialog(finish)
        user.shortcut("Ctrl+Alt+I")

        assert not user.window.session.history.can_undo
        assert size_of(user.window.flattened()) == (400, 300)

    def test_height_drives_width(self, qtbot: QtBot) -> None:
        """UX-IMG-03: typing a height fills in the width when linked."""
        dialog = ResizeDialog(400, 300)
        qtbot.addWidget(dialog)

        dialog.height_box.setValue(600)

        assert dialog.result_size() == (800, 600)
        assert dialog.percent_box.value() == 200
