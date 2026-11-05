"""Test frozen desktop cropping and a real Windows capture smoke test."""

from __future__ import annotations

import sys

import mss
import pytest
from mss.screenshot import ScreenShot
from PySide6.QtGui import QImage
from tests.capture.desktop import DesktopSession

from verdiclip.capture.grabber import FrozenScreen, MssScreenSource
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Rect


class FakeMss:
    """Deterministic replacement for the operating-system screen boundary."""

    def __init__(self, *, fail: bool = False) -> None:
        self.monitors: list[dict[str, int]] = [
            {"left": -2, "top": -3, "width": 2, "height": 1}
        ]
        self.raw = bytearray([10, 20, 30, 255] * 2)
        self.fail = fail
        self.grabs = 0
        self.closes = 0

    def grab(self, monitor: dict[str, int]) -> ScreenShot:
        """Return fixed BGRA pixels or a native capture error."""
        self.grabs += 1
        if self.fail:
            raise mss.ScreenShotError("test screen unavailable")
        return ScreenShot(self.raw, monitor)

    def close(self) -> None:
        """Record native handle release."""
        self.closes += 1


class TestFrozenScreen:
    """Frozen physical desktop pixel selection."""

    @pytest.mark.parametrize(
        ("area", "expected"),
        [
            (Rect(-60, -10, 160, 20), Rect(40, 40, 160, 20)),
            (Rect(-120, -70, 40, 40), Rect(0, 0, 20, 20)),
            (Rect(290, 240, 30, 30), Rect(390, 290, 10, 10)),
            (Rect(-59.5, -9.5, 1, 1), Rect(40, 40, 2, 2)),
        ],
        ids=[
            "negative-origin",
            "clip-top-left",
            "clip-bottom-right",
            "outward-rounding",
        ],
    )
    def test_crop(self, sample_image: QImage, area: Rect, expected: Rect) -> None:
        """Desktop coordinates offset and clip to the exact frozen image pixels."""
        frozen = FrozenScreen(sample_image, Rect(-100, -50, 400, 300))

        cropped = frozen.crop(area)

        assert cropped == sample_image.copy(
            int(expected.x), int(expected.y), int(expected.width), int(expected.height)
        )

    @pytest.mark.parametrize(
        "area",
        [Rect(500, 500, 10, 10), Rect(-200, -200, 10, 10), Rect(300, 0, 10, 10)],
        ids=["after", "before", "touch-edge"],
    )
    def test_outside(self, sample_image: QImage, area: Rect) -> None:
        """Non-overlapping crop areas raise CaptureError."""
        frozen = FrozenScreen(sample_image, Rect(-100, -50, 400, 300))

        with pytest.raises(CaptureError, match="outside the desktop"):
            frozen.crop(area)


class TestMssScreenSource:
    """Physical screen source lifecycle."""

    @pytest.mark.native_desktop
    @pytest.mark.skipif(
        sys.platform != "win32", reason="Real screen capture requires Windows"
    )
    def test_freeze(self, request: pytest.FixtureRequest) -> None:
        """Real desktop captures produce detached images matching physical bounds."""
        session = DesktopSession.current()
        reason = session.unavailable_reason() if session is not None else None
        if reason is not None:
            if request.config.getoption("--require-interactive-capture"):
                pytest.fail(reason)
            pytest.skip(reason)
        source = MssScreenSource()

        try:
            frozen = source.freeze()
            second = source.freeze()
        finally:
            source.close()
            source.close()

        assert not frozen.image.isNull()
        assert frozen.image.width() == frozen.bounds.width
        assert frozen.image.height() == frozen.bounds.height
        assert second.bounds == frozen.bounds

    def test_close_unused(self) -> None:
        """Closing an unused source is harmless."""
        source = MssScreenSource()

        source.close()

        assert source is not None

    def test_native_boundary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Native BGRA pixels become detached images and the handle is reused."""
        native = FakeMss()
        monkeypatch.setattr(mss, "MSS", lambda: native)
        source = MssScreenSource()

        first = source.freeze()
        native.raw[0] = 99
        second = source.freeze()
        source.close()
        source.close()

        assert first.bounds == Rect(-2, -3, 2, 1)
        assert first.image.pixelColor(0, 0).blue() == 10
        assert second.image.pixelColor(0, 0).blue() == 99
        assert native.grabs == 2
        assert native.closes == 1

    def test_native_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Native capture failures release the handle and become CaptureError."""
        native = FakeMss(fail=True)
        monkeypatch.setattr(mss, "MSS", lambda: native)
        source = MssScreenSource()

        with pytest.raises(CaptureError, match=r"Could not capture.*unavailable"):
            source.freeze()

        assert native.closes == 1


class TestDesktopGuard:
    """A known prerequisite can skip; capture errors and strict checks cannot."""

    @pytest.mark.parametrize("strict", [False, True])
    def test_locked_desktop_is_explicit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        request: pytest.FixtureRequest,
        *,
        strict: bool,
    ) -> None:
        """Normal runs name the prerequisite; strict runs fail instead of skipping."""
        monkeypatch.setattr(
            DesktopSession, "current", classmethod(lambda _cls: DesktopSession(0, 0))
        )
        monkeypatch.setattr(
            request.config.option, "require_interactive_capture", strict
        )
        expected = pytest.fail.Exception if strict else pytest.skip.Exception

        with pytest.raises(expected, match="Windows session is locked"):
            TestMssScreenSource().test_freeze(request)

    @pytest.mark.parametrize("session", [None, DesktopSession(0, 1)])
    def test_unknown_or_available_desktop_keeps_capture_errors(
        self,
        monkeypatch: pytest.MonkeyPatch,
        request: pytest.FixtureRequest,
        session: DesktopSession | None,
    ) -> None:
        """Neither unknown nor unlocked state turns a native capture error into skip."""
        monkeypatch.setattr(
            DesktopSession, "current", classmethod(lambda _cls: session)
        )
        monkeypatch.setattr(mss, "MSS", lambda: FakeMss(fail=True))

        with pytest.raises(CaptureError, match=r"Could not capture.*unavailable"):
            TestMssScreenSource().test_freeze(request)
