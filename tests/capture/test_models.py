"""Test screen coordinate conversion across scales and desktop origins."""

from __future__ import annotations

import pytest
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from verdiclip.capture.models import ScreenGeometry, ScreenLayout
from verdiclip.geometry import Point, Rect


class TestScreenGeometry:
    """Physical and logical screen mappings."""

    @pytest.mark.parametrize(
        "scale", [1.0, 1.5, 2.0], ids=["100-percent", "150-percent", "200-percent"]
    )
    @pytest.mark.parametrize(
        "origin",
        [Point(0, 0), Point(-1200, -600), Point(100, -300)],
        ids=["primary", "negative", "mixed"],
    )
    def test_round_trip(self, scale: float, origin: Point) -> None:
        """Mapping preserves widget-local coordinates at every supported DPI."""
        screen = ScreenGeometry(
            "test",
            Rect(origin.x, origin.y, 800, 600),
            Rect(origin.x, origin.y, 800 * scale, 600 * scale),
            scale,
        )
        local = Point(123, 234)

        physical = screen.to_physical(local)

        assert physical == origin + local.scaled(scale)
        assert screen.to_local(physical) == local
        assert screen.local_rect(
            Rect(physical.x, physical.y, 20 * scale, 30 * scale)
        ) == Rect(123, 234, 20, 30)

    def test_from_screen(self, qapp: QApplication) -> None:
        """Qt screen geometry maps its physical origin to local zero."""
        assert qapp is not None
        screen = QGuiApplication.primaryScreen()
        assert screen is not None

        geometry = ScreenGeometry.from_screen(screen)

        assert geometry.to_local(geometry.physical.top_left) == Point(0, 0)
        assert geometry.name == screen.name()
        assert geometry.physical.width == round(
            screen.geometry().width() * screen.devicePixelRatio()
        )


class TestScreenLayout:
    """Current attached screen descriptions."""

    def test_current(self, qapp: QApplication) -> None:
        """Current layout describes each screen reported by Qt."""
        assert qapp is not None

        layout = ScreenLayout.current()

        assert layout == [
            ScreenGeometry.from_screen(screen) for screen in QGuiApplication.screens()
        ]
