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
from tests.capture.cursor_admission_policy import Admission, PreservedState
from tests.capture.cursor_admission_surface import NativeState, OwnedSurface
from tests.capture.cursor_state_evidence import Evidence
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
        self.evidence = Evidence()
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


def refuse(reason: str) -> None:
    """Raise a fixed internal reason; public evidence uses only location/category."""
    raise RuntimeError(reason)


def client(mode: str, commands: Commands) -> None:
    """Own an ordinary class-cursor fixture and sample its UI-thread policy."""
    commands.evidence.mark("client-native-state")
    state = NativeState()
    commands.evidence.mark("client-window-construction")
    surface = ClientWindow(state, Admission(state.read()), lambda: True)
    commands.evidence.mark("client-api-bind")
    api = state.api
    api.LoadCursorW.argtypes = [w.HINSTANCE, ctypes.c_void_p]
    api.LoadCursorW.restype = w.HANDLE
    api.GetCursor.restype = w.HANDLE
    api.GetClassLongPtrW.argtypes = [w.HWND, ctypes.c_int]
    api.GetClassLongPtrW.restype = ctypes.c_size_t
    api.SetForegroundWindow.argtypes = [w.HWND]
    api.SetForegroundWindow.restype = w.BOOL
    cursor_id = {"arrow": 32512, "cross": 32515}[mode]
    commands.evidence.mark("client-load-cursor")
    surface.window_class.cursor = api.LoadCursorW(None, cursor_id)
    if not surface.window_class.cursor:
        refuse("Owned client class cursor unavailable")
    try:
        commands.evidence.mark("client-show")
        surface.show()
        # Owned fixture setup only, before the preservation baseline.
        commands.evidence.mark("client-set-foreground")
        if not api.SetForegroundWindow(surface.handle):
            refuse("Owned client foreground setup failed")
        commands.evidence.mark("client-settle")
        QTest.qWait(100)
        commands.evidence.mark("client-ready-report")
        commands.report("ready", pid=os.getpid(), hwnd=surface.handle)
        for _ in range(32):
            commands.evidence.mark("client-command")
            command = commands.get(timeout=20)
            if command.get("op") == "close":
                return
            require_snapshot(command)
            report_client_snapshot(state, surface, commands, command["sequence"])
        commands.evidence.mark("client-request-budget")
        refuse("Owned client request budget exceeded")
    except Exception as error:
        commands.evidence.fail(error)
        raise
    finally:
        commands.evidence.mark("client-cleanup")
        if not surface.close():
            refuse("Owned client cleanup failed")


def require_snapshot(command: dict[str, object]) -> None:
    """Reject any other client operation without changing its error class."""
    if command.get("op") != "snapshot":
        raise ValueError("Unsupported client operation")


def report_client_snapshot(
    state: NativeState, surface: ClientWindow, commands: Commands, sequence: object
) -> None:
    """Sample one correlated client policy on that client window's UI thread."""
    # All thread-local state is sampled on this window's own UI thread.
    api = state.api
    commands.evidence.mark("client-global-snapshot")
    snap = state.read()
    commands.evidence.mark("client-thread-snapshot")
    thread_cursor = int(api.GetCursor() or 0)
    commands.evidence.mark("client-class-snapshot")
    class_cursor = int(api.GetClassLongPtrW(surface.handle, -12))
    commands.evidence.mark("client-snapshot-report")
    commands.report(
        "snapshot",
        sequence=sequence,
        thread_cursor=thread_cursor,
        class_cursor=class_cursor,
        foreground=snap.foreground,
        gui=snap.gui,
        cursor=snap.cursor,
        pid=os.getpid(),
        hwnd=surface.handle,
    )


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
    commands.evidence.mark("owner-natural-admission")
    wait_for(
        lambda: bool(policy.writes) or not policy.preserved(),
        max(0, context.deadline - time.monotonic()),
    )
    if not policy.writes or policy.failed:
        refuse("Natural owned NULL admission unavailable")
    commands.evidence.mark("owner-owned-report")
    commands.report("owned", pid=os.getpid(), hwnd=surface.handle, writes=policy.writes)
    commands.evidence.mark("owner-observe-command")
    commands.expect("observe")
    while time.monotonic() < context.deadline:
        commands.evidence.mark("owner-observation")
        if not policy.observe(state.read(), safe=safe()):
            refuse("Owned phase preservation failed")
        QTest.qWait(10)
    commands.evidence.mark("owner-observed-report")
    commands.report("observed", preserved=policy.preserved())
    commands.evidence.mark("owner-destroy-command")
    commands.expect("destroy")
    commands.evidence.mark("owner-destroy")
    destroyed = surface.close()
    if not destroyed:
        refuse("Owned surface destruction unconfirmed")
    commands.evidence.mark("owner-first-restoration")
    restored = policy.retire(state.read())
    commands.evidence.mark("owner-destroyed-report")
    commands.report("destroyed", first_restored=restored, writes=policy.writes)
    commands.evidence.mark("owner-drain-command")
    commands.expect("drain")
    return destroyed


@dataclass
class BrokerScope:
    """Strongly owned objects for one test process's input broker."""

    parent: QObject
    broker: NativeInputBroker
    platform: list[GuardedPlatform]
    closed: list[bool]


def make_broker(
    state: NativeState, policy: CursorStatePolicy, recipients: dict[int, int]
) -> BrokerScope:
    """Construct the unchanged guarded broker and retain every lifetime owner."""
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

    return BrokerScope(parent, broker, platform, closed)


def unsupported_baseline(baseline: PreservedState, commands: Commands) -> bool:
    """Keep preexisting menu/capture/cursor refusals exact."""
    if baseline.gui[3] or baseline.gui[4] or baseline.gui[0] & 0x14:
        commands.report(
            "unsupported",
            reason="foreign-capture-or-menu",
            writes=0,
            failure_stage="owner-capture-menu-refusal",
            error_kind="check",
        )
        return True
    if baseline.cursor[0] != 1 or not baseline.cursor[1]:
        commands.report(
            "unsupported",
            reason="baseline-cursor-unavailable",
            writes=0,
            failure_stage="owner-cursor-refusal",
            error_kind="check",
        )
        return True
    return False


def owner(mode: str, commands: Commands) -> None:
    """Execute one bounded scoped-write case without restoring any cursor."""
    commands.evidence.mark("owner-native-state")
    state = NativeState()
    commands.evidence.mark("owner-baseline")
    baseline = state.read()
    policy = CursorStatePolicy(baseline)
    if unsupported_baseline(baseline, commands):
        return
    commands.evidence.mark("owner-recipients")
    handles = {baseline.foreground, *baseline.gui[1:], state.hit(baseline)} - {0}
    recipients = {handle: state.process(handle) for handle in handles}
    if not baseline.foreground or not all(recipients.values()):
        refuse("Foreign recipient identity unavailable")
    commands.evidence.mark("owner-broker-construction")
    scope = make_broker(state, policy, recipients)
    broker, platform, closed = scope.broker, scope.platform, scope.closed

    def safe() -> bool:
        return (
            broker.active
            and len(platform) == 1
            and platform[0].recipient_safe(baseline.foreground)
        )

    commands.evidence.mark("owner-surface-construction")
    surface = CursorStateSurface(state, policy, safe)
    x, y = baseline.cursor[2:]
    destroyed = False

    try:
        commands.evidence.mark("owner-broker-start")
        broker.start(Point(x, y), Rect(x - 16, y - 16, 32, 32))
        commands.evidence.mark("owner-broker-ready")
        wait_for(lambda: broker.active or bool(closed), 2.5)
        commands.evidence.mark("owner-guard")
        if not safe():
            refuse("Owned broker admission failed")
        deadline = time.monotonic() + (0.05 if mode == "cancel" else 1)
        commands.evidence.mark("owner-show")
        surface.show()
        destroyed = owned_interval(
            commands, OwnedContext(state, policy, surface, safe, deadline)
        )
    except Exception as error:
        commands.evidence.fail(error)
        raise
    finally:
        retire_owner(surface, broker, closed, commands, destroyed=destroyed)
    finish_owner(commands, state, policy, closed)


def retire_owner(
    surface: CursorStateSurface,
    broker: NativeInputBroker,
    closed: list[bool],
    commands: Commands,
    *,
    destroyed: bool,
) -> None:
    """Freeze surface failure before independently attempting broker cleanup."""

    def cleanup_reported() -> bool:
        return bool(closed)

    try:
        commands.evidence.mark("owner-cleanup")
        if not destroyed and not surface.close():
            refuse("Owned surface cleanup unconfirmed")
    except Exception as error:
        commands.evidence.fail(error)
        raise
    finally:
        commands.evidence.mark("owner-release")
        broker.release()
        commands.evidence.mark("owner-retirement-wait")
        wait_for(cleanup_reported, 1.5)


def finish_owner(
    commands: Commands,
    state: NativeState,
    policy: CursorStatePolicy,
    closed: list[bool],
) -> None:
    """Observe fixed post-drain samples and retire the private input reader."""
    commands.evidence.mark("owner-late-observation")
    drained = policy.observe(state.read(), safe=closed == [True])
    commands.evidence.mark("owner-drained-report")
    commands.report("drained", preserved=drained, cleanup=closed == [True])
    commands.evidence.mark("owner-finish-command")
    commands.expect("finish")
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        commands.evidence.mark("owner-late-observation")
        policy.observe(state.read(), safe=closed == [True])
        QTest.qWait(10)
    commands.evidence.mark("owner-done-report")
    commands.report("done", preserved=policy.preserved(), writes=policy.writes)
    commands.evidence.mark("owner-close-command")
    commands.expect("close")


def main() -> None:
    """Require explicit disposable-hosted admission before any native API."""
    require_hosted(os.environ)
    if sys.platform != "win32" or os.environ.get("CURSOR_STATE_DISPOSABLE") != "1":
        raise RuntimeError("Disposable Windows experiment only")
    role, mode, nonce = sys.argv[1:]
    if len(nonce) != 32 or any(char not in "0123456789abcdef" for char in nonce):
        raise ValueError("Private nonce required")
    commands = Commands(nonce)
    app: QApplication | None = None
    if role not in {"client", "owner"} or mode not in {
        "arrow",
        "cross",
        "normal",
        "cancel",
        "crash",
    }:
        raise ValueError("Unknown experiment role")
    try:
        commands.evidence.mark("child-bootstrap")
        app = QApplication([])
        if role == "client":
            client(mode, commands)
        else:
            owner(mode, commands)
    except Exception as error:  # noqa: BLE001 - only fixed locations/categories cross
        commands.report("failed", **commands.evidence.fail(error))
        raise SystemExit(1) from None
    finally:
        if app is not None:
            app.quit()


if __name__ == "__main__":
    main()
