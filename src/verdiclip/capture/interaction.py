"""Route capture commands to owned controls without activating a Qt window."""

from __future__ import annotations

from typing import override

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import (
    QAccessible,
    QAccessibleEvent,
    QCloseEvent,
    QColor,
    QPainter,
    QPaintEvent,
    QPen,
    QShowEvent,
)
from PySide6.QtWidgets import QComboBox, QPushButton, QSpinBox, QWidget

from verdiclip.capture.input_policy import InputKind, SelectionInput
from verdiclip.capture.models import ScreenGeometry
from verdiclip.capture.presentation import PASSIVE_WINDOW_FLAGS
from verdiclip.geometry import Point


class VirtualPointer(QWidget):
    """A child-only marker; it owns no foreground, grab or native input."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.point = QPoint(-20, -20)

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.setPen(QPen(QColor("#e94f64"), 2))
        painter.drawLine(self.point + QPoint(-7, 0), self.point + QPoint(7, 0))
        painter.drawLine(self.point + QPoint(0, -7), self.point + QPoint(0, 7))
        painter.end()


class CapturePanel(QWidget):
    """Only explicitly declared, capture-owned controls may receive commands."""

    capture_closed = Signal()

    def __init__(
        self, parent: QWidget | None = None, *, external_input: bool = False
    ) -> None:
        """Keep legacy standalone use; preserved capture opts into output-only UI."""
        flags = Qt.WindowType.Window
        if external_input:
            flags = PASSIVE_WINDOW_FLAGS
        super().__init__(parent, flags)
        if external_input:
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._external_input = external_input
        self._retired = self._dismissing = False
        self._controls: tuple[QWidget, ...] = ()
        self._styles: dict[QWidget, str] = {}
        self._selected = 0
        self._pressed: QWidget | None = None
        self._pointer = VirtualPointer(self) if external_input else None

    @property
    def external_input(self) -> bool:
        """Report whether this owned panel is output-only to Qt/Windows input."""
        return self._external_input

    def use_capture_input(self) -> None:
        """Opt in before presentation without activating or focusing any window."""
        if self.isVisible():
            raise ValueError("Capture input must be configured before presentation")
        self._external_input = True
        self.setWindowFlags(PASSIVE_WINDOW_FLAGS)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        if self._pointer is None:
            self._pointer = VirtualPointer(self)
        self._mark_selected()

    def dismiss(self) -> None:
        """Retire only this newly created panel without emitting user cancellation."""
        self._dismissing = True
        self.close()

    def complete_capture_input(self) -> None:
        """Leave passive delivery status mouse-operable without taking foreground.

        This is not a transfer to a keyboard-focused editor. The capture owner
        retains the review; keyboard review/retry uses a fresh capture lease.
        """
        visible = self.isVisible()
        self._external_input = False
        self._pressed = None
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, on=False)
        self._mark_selected()
        if self._pointer is not None:
            self._pointer.hide()
        if visible:
            self.show()

    @override
    def showEvent(self, event: QShowEvent) -> None:
        """Announce real controls omitted by Qt's tooltip presentation policy."""
        super().showEvent(event)
        if self.windowType() == Qt.WindowType.ToolTip:
            # QWidget skips ObjectShow for ToolTip (QToolTip announces itself).
            # This is a plain owned panel, so it must announce its own controls.
            QAccessible.updateAccessibility(
                QAccessibleEvent(self, QAccessible.Event.ObjectShow)
            )

    def set_capture_controls(self, controls: tuple[QWidget, ...]) -> None:
        """Declare a bounded keyboard order instead of changing native focus."""
        if len(set(controls)) != len(controls) or any(
            not self.isAncestorOf(control) for control in controls
        ):
            raise ValueError(
                "Capture commands require unique, owned descendant controls"
            )
        original = self._styles
        for control in self._controls:
            control.setStyleSheet(original.get(control, ""))
        self._controls = controls
        self._styles = {
            control: original.get(control, control.styleSheet()) for control in controls
        }
        self._selected = 0
        self._pressed = None
        self._mark_selected()

    def _mark_selected(self) -> None:
        for index, control in enumerate(self._controls):
            control.setStyleSheet(
                self._styles.get(control, "")
                + (
                    "border: 2px solid #438de0;"
                    if self._external_input and index == self._selected
                    else ""
                )
            )

    def _local_point(self, point: Point) -> QPoint:
        geometry = ScreenGeometry.from_screen(self.screen())
        logical = geometry.logical.top_left + (
            point - geometry.physical.top_left
        ).scaled(1 / geometry.scale)
        return self.mapFromGlobal(QPoint(round(logical.x), round(logical.y)))

    def _control_at(self, point: QPoint) -> QWidget | None:
        return next(
            (
                control
                for control in self._controls
                if control.isEnabled()
                and control.isVisible()
                and control.rect().contains(control.mapFrom(self, point))
            ),
            None,
        )

    def receive_input(self, event: SelectionInput) -> None:
        """Consume only semantic capture input; never synthesize an external click."""
        if not self._external_input or self._retired or not self.isVisible():
            return
        if event.kind is InputKind.CANCEL:
            self.close()
            return
        if event.kind is InputKind.NEXT:
            self._next_control(-1 if event.reason == "previous" else 1)
            return
        if event.kind is InputKind.ENTER:
            self._activate_selected()
            return
        if event.reason.startswith("arrow:"):
            self._adjust(
                event.amount
                if event.reason in {"arrow:up", "arrow:right"}
                else -event.amount
            )
            return
        self._pointer_input(event)

    def _pointer_input(self, event: SelectionInput) -> None:
        point = self._local_point(event.point)
        if self._pointer is not None:
            self._pointer.setGeometry(self.rect())
            self._pointer.point = point
            self._pointer.raise_()
            self._pointer.update()
        target = self._control_at(point)
        if event.kind is InputKind.PRESS:
            self._pressed = target
            if target is not None:
                self._selected = self._controls.index(target)
                self._mark_selected()
        elif event.kind is InputKind.RELEASE:
            pressed, self._pressed = self._pressed, None
            if pressed is not None and target is pressed:
                self._activate_control(pressed, keyboard=False)

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        """Report closure of this newly created capture panel, never other UI."""
        self._retired = True
        if not self._dismissing:
            self.capture_closed.emit()
        super().closeEvent(event)

    def _next_control(self, step: int) -> None:
        if not self._controls:
            return
        for _ in self._controls:
            self._selected = (self._selected + step) % len(self._controls)
            if self._controls[self._selected].isEnabled():
                break
        self._mark_selected()

    def _activate_selected(self, *, keyboard: bool = True) -> None:
        if not self._controls:
            return
        self._activate_control(self._controls[self._selected], keyboard=keyboard)

    def _activate_control(self, control: QWidget, *, keyboard: bool) -> None:
        if (
            not control.isEnabled()
            or not control.isVisible()
            or control not in self._controls
        ):
            return
        if isinstance(control, QPushButton):
            control.click()
        elif not keyboard and isinstance(control, QComboBox) and control.count():
            control.setCurrentIndex((control.currentIndex() + 1) % control.count())
        elif keyboard:
            self._next_control(1)

    def _adjust(self, step: int) -> None:
        if not self._controls:
            return
        control = self._controls[self._selected]
        if not control.isEnabled():
            return
        if isinstance(control, QSpinBox):
            control.stepBy(step)
        elif isinstance(control, QComboBox) and control.count():
            control.setCurrentIndex((control.currentIndex() + step) % control.count())
        else:
            self._next_control(step)
