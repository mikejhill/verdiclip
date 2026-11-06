"""Disposable native fixtures/owner; invoked only by the dedicated hosted test."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import override

from PySide6.QtCore import QObject
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from tests.capture.cursor_admission import GuardedPlatform
from tests.capture.cursor_admission_policy import Admission
from tests.capture.cursor_admission_surface import NativeState, OwnedSurface
from tests.capture.cursor_state_peer import Wire, send
from tests.capture.cursor_state_policy import CursorStatePolicy
from tests.capture.cursor_state_surface import CursorStateSurface
from tests.capture.hosted_fixture_input import require_hosted

from verdiclip.capture.input_broker import NativeInputBroker
from verdiclip.capture.input_worker import CaptureInputWorker
from verdiclip.geometry import Point, Rect


def wait_for(predicate: Callable[[], bool], seconds: float) -> None:
    """Wait only for a protocol/lifecycle event, never eventual cursor recovery."""
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() >= deadline:
            raise TimeoutError("Owned process phase deadline")
        QTest.qWait(5)


class Commands:
    """Private sequential command decoder for the exact owned process."""

    def __init__(self, nonce: str) -> None:
        self.nonce, self.last = nonce, 0
        self.wire = Wire(sys.stdin, stop_on_close=True)

    def get(self, *, timeout: float = 1) -> dict[str, object]:
        """Validate nonce and strictly increasing sequence before dispatch."""
        command = self.wire.receive(timeout=timeout)
        sequence = command.get("sequence")
        if (
            command.get("nonce") != self.nonce
            or type(sequence) is not int
            or sequence <= self.last
        ):
            raise ValueError("Owned command correlation failed")
        self.last = sequence
        return command

    def expect(self, op: str) -> None:
        """Require the next command to match this lifecycle boundary."""
        if self.get().get("op") != op:
            raise ValueError("Unexpected owned phase command")

    def report(self, phase: str, **fields: object) -> None:
        """Send transient identities only through the private parent pipe."""
        send(sys.stdout, {"nonce": self.nonce, "phase": phase, **fields})


class ClientWindow(OwnedSurface):
    """Independent client with its own standard class cursor and default handler."""

    @override
    def _message(self, hwnd: int, message: int, wp: int, lp: int) -> int:
        return int(self.api.DefWindowProcW(hwnd, message, wp, lp))


def client(mode: str, commands: Commands) -> None:
    """Own an ordinary class-cursor fixture and sample its UI-thread policy."""
    state = NativeState()
    surface = ClientWindow(state, Admission(state.read()), lambda: True)
    api = state.api
    api.LoadCursorW.argtypes = [w.HINSTANCE, ctypes.c_void_p]
    api.LoadCursorW.restype = w.HANDLE
    api.GetCursor.restype = w.HANDLE
    api.GetClassLongPtrW.argtypes = [w.HWND, ctypes.c_int]
    api.GetClassLongPtrW.restype = ctypes.c_size_t
    api.SetForegroundWindow.argtypes = [w.HWND]
    api.SetForegroundWindow.restype = w.BOOL
    cursor_id = {"arrow": 32512, "cross": 32515}[mode]
    surface.window_class.cursor = api.LoadCursorW(None, cursor_id)
    if not surface.window_class.cursor:
        raise RuntimeError("Owned client class cursor unavailable")
    try:
        surface.show()
        # Owned fixture setup only, before the preservation baseline.
        if not api.SetForegroundWindow(surface.handle):
            raise RuntimeError("Owned client foreground setup failed")
        QTest.qWait(100)
        commands.report("ready", pid=os.getpid(), hwnd=surface.handle)
        for _ in range(32):
            command = commands.get(timeout=20)
            if command.get("op") == "close":
                return
            if command.get("op") != "snapshot":
                raise ValueError("Unsupported client operation")
            # All thread-local state is sampled on this window's own UI thread.
            snap = state.read()
            commands.report(
                "snapshot",
                sequence=command["sequence"],
                thread_cursor=int(api.GetCursor() or 0),
                class_cursor=int(api.GetClassLongPtrW(surface.handle, -12)),
                foreground=snap.foreground,
                gui=snap.gui,
                cursor=snap.cursor,
                pid=os.getpid(),
                hwnd=surface.handle,
            )
        raise RuntimeError("Owned client request budget exceeded")
    finally:
        if not surface.close():
            raise RuntimeError("Owned client cleanup failed")


@dataclass
class OwnedContext:
    """References owned by exactly one scoped experiment."""

    state: NativeState
    policy: CursorStatePolicy
    surface: CursorStateSurface
    safe: Callable[[], bool]
    deadline: float


def owned_interval(commands: Commands, context: OwnedContext) -> bool:
    """Observe the owned phase and report the first post-destroy state once."""
    state, policy, surface, safe = (
        context.state,
        context.policy,
        context.surface,
        context.safe,
    )
    wait_for(
        lambda: bool(policy.writes) or not policy.preserved(),
        max(0, context.deadline - time.monotonic()),
    )
    if not policy.writes or policy.failed:
        raise RuntimeError("Natural owned NULL admission unavailable")
    commands.report("owned", pid=os.getpid(), hwnd=surface.handle, writes=policy.writes)
    commands.expect("observe")
    while time.monotonic() < context.deadline:
        if not policy.observe(state.read(), safe=safe()):
            raise RuntimeError("Owned phase preservation failed")
        QTest.qWait(10)
    commands.report("observed", preserved=policy.preserved())
    commands.expect("destroy")
    destroyed = surface.close()
    if not destroyed:
        raise RuntimeError("Owned surface destruction unconfirmed")
    restored = policy.retire(state.read())
    commands.report("destroyed", first_restored=restored, writes=policy.writes)
    commands.expect("drain")
    return destroyed


def owner(mode: str, commands: Commands) -> None:
    """Execute one bounded scoped-write case without restoring any cursor."""
    state = NativeState()
    baseline = state.read()
    policy = CursorStatePolicy(baseline)
    if baseline.gui[3] or baseline.gui[4] or baseline.gui[0] & 0x14:
        commands.report("unsupported", reason="foreign-capture-or-menu", writes=0)
        return
    if baseline.cursor[0] != 1 or not baseline.cursor[1]:
        commands.report("unsupported", reason="baseline-cursor-unavailable", writes=0)
        return
    handles = {baseline.foreground, *baseline.gui[1:], state.hit(baseline)} - {0}
    recipients = {handle: state.process(handle) for handle in handles}
    if not baseline.foreground or not all(recipients.values()):
        raise RuntimeError("Foreign recipient identity unavailable")
    parent = QObject()
    platform: list[GuardedPlatform] = []

    def platform_factory() -> GuardedPlatform:
        result = GuardedPlatform(state, policy, recipients)
        platform.append(result)
        return result

    broker = NativeInputBroker(
        parent,
        factory=lambda point, bounds: CaptureInputWorker(
            point,
            bounds,
            platform_factory=platform_factory,
        ),
    )
    closed: list[bool] = []
    broker.closed.connect(lambda cleaned, _message: closed.append(cleaned))
    broker.input_event.connect(lambda _event: policy.fail())

    def safe() -> bool:
        return (
            broker.active
            and len(platform) == 1
            and platform[0].recipient_safe(baseline.foreground)
        )

    surface = CursorStateSurface(state, policy, safe)
    x, y = baseline.cursor[2:]
    destroyed = False
    try:
        broker.start(Point(x, y), Rect(x - 16, y - 16, 32, 32))
        wait_for(lambda: broker.active or bool(closed), 2.5)
        if not safe():
            raise RuntimeError("Owned broker admission failed")
        deadline = time.monotonic() + (0.05 if mode == "cancel" else 1)
        surface.show()
        destroyed = owned_interval(
            commands, OwnedContext(state, policy, surface, safe, deadline)
        )
    finally:
        try:
            if not destroyed and not surface.close():
                raise RuntimeError("Owned surface cleanup unconfirmed")
        finally:
            broker.release()
            wait_for(lambda: bool(closed), 1.5)
    finish_owner(commands, state, policy, closed)


def finish_owner(
    commands: Commands,
    state: NativeState,
    policy: CursorStatePolicy,
    closed: list[bool],
) -> None:
    """Observe fixed post-drain samples and retire the private input reader."""
    drained = policy.observe(state.read(), safe=closed == [True])
    commands.report("drained", preserved=drained, cleanup=closed == [True])
    commands.expect("finish")
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        policy.observe(state.read(), safe=closed == [True])
        QTest.qWait(10)
    commands.report("done", preserved=policy.preserved(), writes=policy.writes)
    commands.expect("close")


def main() -> None:
    """Require explicit disposable-hosted admission before any native API."""
    require_hosted(os.environ)
    if sys.platform != "win32" or os.environ.get("CURSOR_STATE_DISPOSABLE") != "1":
        raise RuntimeError("Disposable Windows experiment only")
    role, mode, nonce = sys.argv[1:]
    if len(nonce) != 32 or any(char not in "0123456789abcdef" for char in nonce):
        raise ValueError("Private nonce required")
    app = QApplication([])
    commands = Commands(nonce)
    if role not in {"client", "owner"} or mode not in {
        "arrow",
        "cross",
        "normal",
        "cancel",
        "crash",
    }:
        raise ValueError("Unknown experiment role")
    try:
        if role == "client":
            client(mode, commands)
        else:
            owner(mode, commands)
    except Exception:  # noqa: BLE001 - fixed receipt only; no native identities in errors
        commands.report("failed", reason="owned-process-phase-failure")
        raise SystemExit(1) from None
    finally:
        app.quit()


if __name__ == "__main__":
    main()
