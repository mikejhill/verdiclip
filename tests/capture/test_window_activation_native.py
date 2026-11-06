"""Hosted-only foreground acceptance, without pointer/key injection or focus hacks."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import os
import sys
import time
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QWidget
from pytestqt.qtbot import QtBot
from tests.capture.desktop import DesktopSession
from tests.capture.hosted_fixture_input import require_hosted
from tests.capture.menu_peer import MenuPeer
from tests.capture.scroll_peer import ScrollPeer
from tests.ux.test_app_journeys import AppFactory, make_app

from verdiclip.capture.models import Capture, CaptureMode
from verdiclip.geometry import Rect

__all__ = ["make_app"]
pytestmark = pytest.mark.native_desktop


@pytest.fixture(autouse=True)
def hosted_only() -> None:
    """Reject before any window/child creation outside the dedicated hosted job."""
    if os.environ.get("VERDICLIP_HOSTED_ACTIVATION") != "1":
        pytest.skip("Requires the dedicated hosted Windows activation job")
    require_hosted(os.environ)
    assert sys.platform == "win32"
    session = DesktopSession.current()
    assert session is not None
    assert session.state == 0
    assert session.flags == 1


def foreground() -> int:
    """Read the actual OS foreground HWND, never request an override."""
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetForegroundWindow.argtypes = []
    user.GetForegroundWindow.restype = w.HWND
    return int(user.GetForegroundWindow() or 0)


def capture(image: QImage) -> Capture:
    """Exercise the ordinary adapter, including the broker's preservation flag."""
    return Capture(
        image,
        CaptureMode.REGION,
        Rect(0, 0, image.width(), image.height()),
        "Owned fixture capture",
        datetime.now(UTC),
        preserve_foreground=True,
    )


@pytest.mark.parametrize("minimized", [False, True])
def test_existing_editor_becomes_os_foreground_repeatedly(
    make_app: AppFactory,
    sample_image: QImage,
    qtbot: QtBot,
    *,
    minimized: bool,
) -> None:
    app = make_app()
    app.controller._on_captured(capture(sample_image))
    host = app.controller.editors[0]
    qtbot.waitExposed(host)
    hwnd = int(host.winId())
    first = host.pages[0]
    other = QWidget()
    qtbot.addWidget(other)
    other.setWindowTitle("Owned foreground acceptance fixture")
    other.resize(240, 180)
    other.show()
    qtbot.waitExposed(other)
    for expected_pages in (2, 3):
        if minimized:
            host.showMinimized()
            qtbot.waitUntil(host.isMinimized, timeout=2000)
        # Ordinary Qt activation of our own second window establishes the control.
        # No AttachThreadInput, SendInput, topmost toggles or foreground-lock changes.
        other.raise_()
        other.activateWindow()
        qtbot.waitUntil(
            lambda: (
                foreground() == int(other.winId())
                and QApplication.activeWindow() is other
            ),
            timeout=3000,
        )
        app.controller._on_captured(capture(sample_image))
        qtbot.waitUntil(
            lambda: foreground() == hwnd and QApplication.activeWindow() is host,
            timeout=3000,
        )
        assert int(host.winId()) == hwnd
        assert app.controller.editors == (host,)
        assert not host.isMinimized()
        assert len(host.pages) == expected_pages
        assert host.pages[0] is first
        assert host.session is host.pages[-1].record.session


def test_ordinary_capture_preserves_external_native_menu(
    make_app: AppFactory,
    sample_image: QImage,
    qtbot: QtBot,
) -> None:
    app = make_app()
    app.controller._on_captured(capture(sample_image))
    host = app.controller.editors[0]
    qtbot.waitExposed(host)
    hwnd = int(host.winId())
    peer = MenuPeer()
    try:
        peer.start(handoff=True)
        deadline = time.monotonic() + 5
        while True:
            sequence = peer.poll()
            assert sequence is not None
            remaining = int((deadline - time.monotonic()) * 1000)
            assert remaining > 0, "Owned native menu did not open within setup budget"
            qtbot.waitUntil(partial(peer.acknowledged, sequence), timeout=remaining)
            if not peer.preservation_errors():
                break
            assert not peer.error
            qtbot.wait(10)  # Setup only; never retry capture or its final verdict.
        assert foreground() == peer.owner != hwnd
        app.controller._on_captured(capture(sample_image))
        assert len(host.pages) == 2
        assert app.controller.editors == (host,)
        assert int(host.winId()) == hwnd
        # A correlated post-adapter reply, not cached pre-capture state, is required.
        sequence = peer.poll()
        assert sequence is not None
        qtbot.waitUntil(lambda: peer.acknowledged(sequence), timeout=2000)
        assert peer.preservation_errors() == []
        assert foreground() == peer.owner != hwnd
    finally:
        peer.close()


def test_existing_editor_activates_from_owned_external_window(
    make_app: AppFactory,
    sample_image: QImage,
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    """Cross-process acceptance reuses the existing no-menu WinForms fixture."""
    app = make_app()
    app.controller._on_captured(capture(sample_image))
    host = app.controller.editors[0]
    qtbot.waitExposed(host)
    hwnd = int(host.winId())
    peer = ScrollPeer(tmp_path / "owned-foreground", provider="winforms")
    try:
        peer.start()
        qtbot.waitUntil(peer.poll, timeout=20000)
        assert peer.identity is not None
        assert foreground() == peer.identity[2] != hwnd
        app.controller._on_captured(capture(sample_image))
        qtbot.waitUntil(
            lambda: foreground() == hwnd and QApplication.activeWindow() is host,
            timeout=3000,
        )
        assert app.controller.editors == (host,)
        assert int(host.winId()) == hwnd
        assert len(host.pages) == 2
        assert host.session is host.pages[-1].record.session
    finally:
        peer.close()
