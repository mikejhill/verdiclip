"""Pure placement policy against synthetic available display/work-area geometry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QGuiApplication

from verdiclip.editor.transfer import TabTransfer
from verdiclip.editor.window import EditorWindow


@dataclass
class Display:
    """A logical work area, independent of the host's current monitors."""

    area: QRect

    def availableGeometry(self) -> QRect:  # noqa: N802 - QScreen test boundary
        """Account for taskbars/docks instead of using full screen bounds."""
        return self.area


@dataclass
class Host:
    """Observe desired resize/move without constructing or moving a native window."""

    w: int
    h: int
    x: int = 0
    y: int = 0

    def width(self) -> int:
        """Return requested logical width."""
        return self.w

    def height(self) -> int:
        """Return requested logical height."""
        return self.h

    def resize(self, width: int, height: int) -> None:
        """Record policy request only; Qt minimum-size negotiation is separate."""
        self.w, self.h = width, height

    def move(self, x: int, y: int) -> None:
        """Record logical placement without touching the desktop."""
        self.x, self.y = x, y


@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    x=st.integers(-6000, 6000),
    y=st.integers(-4000, 4000),
    width=st.integers(800, 3000),
    height=st.integers(600, 1800),
    source_w=st.integers(720, 5000),
    source_h=st.integers(480, 4000),
    pointer_x=st.integers(-10000, 10000),
    pointer_y=st.integers(-10000, 10000),
)
def test_generated_detach_work_area_policy_keeps_requested_window_reachable(  # noqa: PLR0913, PLR0917 - generated coordinate inputs
    monkeypatch: pytest.MonkeyPatch,
    x: int,
    y: int,
    width: int,
    height: int,
    source_w: int,
    source_h: int,
    pointer_x: int,
    pointer_y: int,
) -> None:
    """Every work area retains the entire requested window, including edge pixels."""
    display = Display(QRect(x, y, width, height))
    monkeypatch.setattr(QGuiApplication, "screenAt", lambda _point: display)
    transfer = object.__new__(TabTransfer)
    transfer.source = cast(EditorWindow, Host(source_w, source_h))
    target = Host(1, 1)
    transfer._position(cast(EditorWindow, target), QPoint(pointer_x, pointer_y))
    placed = QRect(target.x, target.y, target.w, target.h)
    assert display.area.contains(placed)
    assert target.w == min(source_w, width)
    assert target.h == min(source_h, height)
    assert target.x == max(x, min(pointer_x, x + width - target.w))
    assert target.y == max(y, min(pointer_y, y + height - target.h))


def test_removed_destination_uses_current_primary_work_area(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A detached drop into a removed-display gap cannot reuse a stale screen."""
    primary = Display(QRect(-1920, 40, 1920, 1040))
    monkeypatch.setattr(QGuiApplication, "screenAt", lambda _point: None)
    monkeypatch.setattr(QGuiApplication, "primaryScreen", lambda: primary)
    transfer = object.__new__(TabTransfer)
    transfer.source = cast(EditorWindow, Host(1200, 800))
    target = Host(1, 1)
    transfer._position(cast(EditorWindow, target), QPoint(9000, -9000))
    assert QRect(target.x, target.y, target.w, target.h) == QRect(-1200, 40, 1200, 800)
