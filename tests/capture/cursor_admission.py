"""Hosted-only first-stage ownership probe; no cursor suppression or pixels."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import os
from pathlib import Path
from typing import override

from PySide6.QtCore import QObject, QTimer
from pytestqt.qtbot import QtBot
from tests.capture.cursor_admission_policy import Admission
from tests.capture.cursor_admission_surface import NativeState, OwnedSurface
from tests.capture.desktop import DesktopSession
from tests.capture.hosted_fixture_input import require_hosted
from tests.capture.menu_peer import MenuPeer
from tests.capture.scroll_peer import ScrollPeer

from verdiclip.capture.input_broker import NativeInputBroker
from verdiclip.capture.input_native import Win32InputPlatform
from verdiclip.capture.input_worker import CaptureInputWorker
from verdiclip.geometry import Point, Rect


class GuardedPlatform(Win32InputPlatform):
    """Additional foreign-recipient guard; never substitute our HWND for it."""

    def __init__(
        self, state: NativeState, admission: Admission, recipients: dict[int, int]
    ) -> None:
        super().__init__()
        self.state, self.admission, self.recipients = state, admission, recipients

    @override
    def recipient_safe(self, foreground: int, /) -> bool:
        if not super().recipient_safe(foreground):
            return False
        if not self.admission.observe(self.state.read(), safe=True):
            return False
        own_level = self._integrity._level(self._integrity._kernel.GetCurrentProcess())
        return own_level is not None and all(
            self.state.process(handle) == pid
            and self._integrity._permitted_process(pid, own_level)
            for handle, pid in self.recipients.items()
        )


def observe_once(
    qtbot: QtBot, state: NativeState, *, cancel_early: bool
) -> dict[str, object]:
    """Finite ownership-only session; snapshot comparisons span every boundary."""
    before = state.read()
    if before.gui[3]:
        if state.read() != before:
            raise RuntimeError("Foreign capture changed during refusal")
        return {
            "verdict": "unsupported-foreign-capture",
            "surface_created": False,
            "preservation_unchanged": True,
            "cursor_writes": 0,
        }
    admission = Admission(before)
    handles = {before.foreground, *before.gui[1:], state.hit(before)} - {0}
    recipients = {handle: state.process(handle) for handle in handles}
    if not before.foreground or not recipients or not all(recipients.values()):
        raise RuntimeError("Foreign recipient snapshot unavailable")
    owner = QObject()
    broker = NativeInputBroker(
        owner,
        factory=lambda point, bounds: CaptureInputWorker(
            point,
            bounds,
            platform_factory=lambda: GuardedPlatform(state, admission, recipients),
        ),
    )
    clean: list[bool] = []
    broker.closed.connect(lambda cleaned, _message: clean.append(cleaned))
    x, y = before.cursor[2:]
    surface = OwnedSurface(state, admission, lambda: broker.active)
    timer = QTimer()
    timer.setInterval(10)

    def observe() -> None:
        try:
            admission.observe(state.read(), safe=broker.active)
        except Exception:  # noqa: BLE001 - latch observation failure, still retire
            admission.fail()

    broker.input_event.connect(lambda _event: admission.fail())
    timer.timeout.connect(observe)
    destroyed = active = created = False
    try:
        broker.start(Point(x, y), Rect(x - 16, y - 16, 32, 32))
        qtbot.waitUntil(lambda: broker.active or bool(clean), timeout=2500)
        if broker.active and admission.observe(state.read(), safe=True):
            surface.show()
            created = True
            admission.observe(state.read(), safe=broker.active)
            timer.start()
            qtbot.wait(50 if cancel_early else 1000)
            active = broker.active
    finally:
        timer.stop()
        try:
            destroyed = surface.close()
            if not destroyed:
                raise RuntimeError("Owned surface retirement unconfirmed; end harness")
            admission.observe(state.read(), safe=destroyed)
        finally:
            broker.release()
            qtbot.waitUntil(lambda: bool(clean), timeout=1500)
            admission.observe(state.read(), safe=clean == [True])
    return {
        "verdict": admission.verdict(broker_active=active, destroyed=destroyed),
        "surface_created": created,
        "natural_owned_messages": admission.messages,
        "preservation_unchanged": not admission.failed,
        "surface_destroyed_before_drain": destroyed,
        "broker_cleanup_confirmed": clean == [True],
        "cursor_writes": 0,
        "presentation_verified": False,
        "crash_verified": False,
        "mixed_dpi_verified": False,
    }


def run_admission(
    qtbot: QtBot, directory: Path, fixture: str
) -> list[dict[str, object]]:
    """Owned fixture setup precedes the preserved baseline; never move a pointer."""
    require_hosted(os.environ)
    session = DesktopSession.current()
    if session is None or session.state != 0 or session.flags != 1:
        raise RuntimeError("Known unlocked hosted desktop required")
    if fixture not in {"window", "menu"}:
        raise ValueError("Unknown fixture")
    state = NativeState()
    peer = (
        ScrollPeer(directory / "fixture", provider="winforms")
        if fixture == "window"
        else MenuPeer()
    )
    try:
        prepare_peer(qtbot, state, peer)
        results = []
        for cancel in (False, True, False):
            if isinstance(peer, MenuPeer) and peer.preservation_errors():
                raise RuntimeError("Foreign popup changed")
            result = observe_once(qtbot, state, cancel_early=cancel)
            results.append(result)
            if result["verdict"] == "unsupported-preservation-or-cleanup":
                break
            if isinstance(peer, MenuPeer) and peer.preservation_errors():
                raise RuntimeError("Foreign popup changed")
        return results
    finally:
        peer.close()


def prepare_peer(qtbot: QtBot, state: NativeState, peer: ScrollPeer | MenuPeer) -> None:
    """Position only the owned fixture before establishing the preservation baseline."""
    if isinstance(peer, ScrollPeer):
        peer.start()
        qtbot.waitUntil(peer.poll, timeout=20000)
        # Move only the fixture window during setup, never the stationary cursor.
        x, y = state.read().cursor[2:]
        api = state.api
        api.SetWindowPos.argtypes = [
            w.HWND,
            w.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            w.UINT,
        ]
        api.SetWindowPos.restype = w.BOOL
        if not api.SetWindowPos(peer.handle, None, x - 100, y - 100, 0, 0, 0x15):
            raise OSError("Owned fixture positioning failed")
        qtbot.wait(100)
        if not peer.poll() or state.read().foreground != peer.handle:
            raise RuntimeError("Owned fixture foreground unavailable")
        if state.process(state.hit(state.read())) != peer.integer("pid"):
            raise RuntimeError("Stationary anchor does not hit the owned fixture")
    else:
        peer.start(handoff=True)
        qtbot.waitUntil(lambda: peer.state is not None, timeout=10000)
        if peer.preservation_errors():
            raise RuntimeError("Owned foreign popup unavailable")
