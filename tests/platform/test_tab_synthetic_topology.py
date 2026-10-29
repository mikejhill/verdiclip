"""Synthetic Windows coordinate policy; these are not physical DPI qualification."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from typing import override

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication
from tests.platform.test_tab_drag import Api, Receiver

from verdiclip.platform.tab_drag import NativeTabDrag, Win32CaptureApi


@dataclass
class Screen:
    """Expose Qt's unscaled Windows origin and scaled-size screen geometry."""

    area: QRect
    ratio: float

    def geometry(self) -> QRect:
        """Return synthetic Qt logical geometry, including negative origins."""
        return self.area

    def devicePixelRatio(self) -> float:  # noqa: N802 - QScreen test boundary
        """Return this screen's scale, not the source window's scale."""
        return self.ratio


@pytest.mark.parametrize("ratio", [1.0, 1.25, 1.5, 1.75, 2.0, 2.5])
@pytest.mark.parametrize("origin", [(-2560, 0), (0, -1440), (1920, 120)])
def test_coordinate_mapping_uses_destination_screen_origin_and_scale(
    monkeypatch: pytest.MonkeyPatch, ratio: float, origin: tuple[int, int]
) -> None:
    """Physical coordinates map within that screen's own Windows coordinate island."""
    x, y = origin
    screen = Screen(QRect(x, y, 800, 600), ratio)
    monkeypatch.setattr(QGuiApplication, "screens", lambda: [screen])
    assert NativeTabDrag.logical_position(x, y) == QPoint(x, y)
    assert NativeTabDrag.logical_position(
        x + round(400 * ratio), y + round(200 * ratio)
    ) == QPoint(x + 400, y + 200)
    # Half-open far edges and desktop gaps have no guessed destination scale.
    far = QPoint(x + round(800 * ratio), y + round(600 * ratio))
    assert NativeTabDrag.logical_position(far.x(), far.y()) == far


def test_mixed_dpi_screen_removal_and_reorder_do_not_cache_old_scale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each event uses the current topology; a removed screen becomes a gap."""
    left = Screen(QRect(-2560, 0, 1280, 720), 2.0)
    primary = Screen(QRect(0, 0, 1280, 720), 1.5)
    right = Screen(QRect(1920, 0, 1600, 900), 1.0)
    screens = [left, primary, right]
    monkeypatch.setattr(QGuiApplication, "screens", lambda: screens)
    points = [
        (-1280, 360, QPoint(-1920, 180)),
        (960, 540, QPoint(640, 360)),
        (2400, 450, QPoint(2400, 450)),
    ]
    for x, y, expected in points:
        assert NativeTabDrag.logical_position(x, y) == expected
    screens.reverse()
    for x, y, expected in points:
        assert NativeTabDrag.logical_position(x, y) == expected
    screens.remove(left)
    assert NativeTabDrag.logical_position(-1280, 360) == QPoint(-1280, 360)
    screens.clear()
    assert NativeTabDrag.logical_position(960, 540) == QPoint(960, 540)


@pytest.mark.parametrize("point", [(-32768, -32768), (-2, 32767), (32767, -1), (0, 0)])
def test_native_event_coordinates_decode_signed_words_without_live_cursor(
    monkeypatch: pytest.MonkeyPatch, point: tuple[int, int]
) -> None:
    """Negative client coordinates survive LPARAM packing before DPI mapping."""

    class Function:
        def __init__(self) -> None:
            self.argtypes: object = None
            self.restype: object = None

        def __call__(self, *_arguments: object) -> int:
            return 1

    class Dll:
        def __init__(self) -> None:
            self.SetCapture = Function()
            self.GetCapture = Function()
            self.ReleaseCapture = Function()
            self.ClientToScreen = Function()

    monkeypatch.setattr(
        ctypes, "WinDLL", lambda *_args, **_kwargs: Dll(), raising=False
    )
    monkeypatch.setattr(QGuiApplication, "screens", list)
    x, y = point
    parameter = (x & 0xFFFF) | ((y & 0xFFFF) << 16)
    assert Win32CaptureApi().position(42, parameter) == QPoint(x, y)


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    st.lists(
        st.tuples(
            st.booleans(),
            st.sampled_from(
                [0x0200, 0x0202, 0x0215, 0x001F, 0x0204, 0x0312, 0x0100, 0x9999]
            ),
        ),
        min_size=1,
        max_size=30,
    )
)
def test_generated_native_sequences_have_one_terminal_receipt(
    qapp: QApplication, events: list[tuple[bool, int]]
) -> None:
    """Foreign, late and duplicate events never create two outcomes for one lease."""
    del qapp
    api, receiver = Api(), Receiver()
    driver = NativeTabDrag(receiver, api)
    assert driver.begin(42)
    try:
        for owned, message in events:
            driver.dispatch(42 if owned else 99, message, 0x1B, QPoint(-100, 200))
        driver.cancel()
        terminal = [event for event in receiver.events if event != "move"]
        assert len(terminal) == 1
        assert api.releases == [42]
        assert api.owner == 0
        assert not driver.active
        driver.dispatch(42, 0x0202, position=QPoint(200, 100))
        assert [event for event in receiver.events if event != "move"] == terminal
    finally:
        driver.cancel()


@pytest.mark.parametrize("raises", [False, True])
def test_release_callback_reentrancy_and_failure_cannot_leave_input_owned(
    qapp: QApplication,
    *,
    raises: bool,
) -> None:
    """The lease is invalid before synchronous capture-loss or receiver callbacks."""
    del qapp

    class ReentrantApi(Api):
        @override
        def release(self, handle: int) -> None:
            super().release(handle)
            assert not driver.active
            driver.dispatch(handle, 0x0215)

    class ReentrantReceiver(Receiver):
        @override
        def released(self, position: QPoint) -> None:
            assert api.owner == 0
            assert not driver.active
            driver.cancel()
            super().released(position)
            if raises:
                raise RuntimeError("Injected receiver failure after release")

    api, receiver = ReentrantApi(), ReentrantReceiver()
    driver = NativeTabDrag(receiver, api)
    assert driver.begin(42)
    if raises:
        with pytest.raises(RuntimeError, match="receiver failure"):
            driver.dispatch(42, 0x0202, position=QPoint(-400, 200))
    else:
        driver.dispatch(42, 0x0202, position=QPoint(-400, 200))
    assert api.releases == [42]
    assert api.owner == 0
    assert receiver.events == ["release"]
    driver.cancel()
    assert receiver.events == ["release"]
