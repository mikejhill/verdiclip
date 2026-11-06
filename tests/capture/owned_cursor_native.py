"""Disposable ordinary-window component; never imported by product capture."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import os
import sys
import threading
import time
from asyncio import CancelledError
from collections.abc import Callable
from typing import override

from tests.capture.owned_cursor_policy import OwnedCursorScope
from tests.capture.owned_cursor_surface import NativeState, OwnedSurface, PreservedState
from tests.capture.owned_cursor_thread import Controls, Outcome, run_owner

from verdiclip.capture.input_integrity import GuiThreadInfo, InputIntegrityGuard
from verdiclip.capture.input_worker import CaptureInputWorker
from verdiclip.geometry import Point, Rect

_RETAINED: list[object] = []


class OriginalRecipientGuard(InputIntegrityGuard):
    """Revalidate original foreign process even when owned surface wins hit testing."""

    def __init__(self, state: NativeState, original: int, pid: int) -> None:
        super().__init__()
        self.state, self.original, self.pid = state, original, pid
        self.owned: Callable[[], int] = lambda: 0

    @override
    def _pointer_recipient(self, info: GuiThreadInfo) -> int:
        if info.capture or self.state.process(self.original) != self.pid:
            return 0
        hit = self.state.hit(self.state.read())
        if hit != self.original and (not self.owned() or hit != self.owned()):
            return 0
        return self.original


def run_owned_surface(
    cancel: threading.Event,
    emit: Callable[[str], None],
    *,
    crash_requested: threading.Event | None = None,
) -> dict[str, bool | str]:
    """Own one finite GUI thread; no caller can force foreign cursor restoration."""
    if not (
        sys.platform == "win32"
        and os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
        and os.environ.get("VERDICLIP_OWNED_CURSOR") == "1"
    ):
        raise RuntimeError("disposable-host-required")
    result: dict[str, bool | str] = {
        "admitted": False,
        "balance_attempted": False,
        "balance_confirmed": False,
        "hwnd_destroyed": True,
        "worker_cleaned": True,
        "accounting_confirmed": True,
        "thread_retired": False,
        "foreign_preserved": False,
        "cleanup_confirmed": False,
        "reason": "native-failure",
    }
    errors: list[BaseException] = []

    def target() -> None:
        try:
            _owner(cancel, emit, result, crash_requested=crash_requested)
        except (KeyboardInterrupt, SystemExit, CancelledError) as error:
            errors.append(error)
        except Exception:  # noqa: BLE001 - only fixed result crosses process boundary
            result["reason"] = "native-failure"

    thread = threading.Thread(target=target, daemon=True)
    _RETAINED.append(thread)
    completed = [False]
    try:
        thread.start()
        thread.join(6.0)
        completed[0] = not thread.is_alive()
    finally:
        if not all(completed):
            original_error = sys.exception()
            cleanup_error = _cancel_thread(thread, cancel)
            if cleanup_error is not None and original_error is None:
                raise cleanup_error
    if thread.is_alive():
        result["reason"] = "cleanup-unconfirmed"
        return _public_result(result)
    _RETAINED.remove(thread)
    if errors:
        raise errors[0]
    result["thread_retired"] = True
    result["cleanup_confirmed"] = bool(
        result["hwnd_destroyed"]
        and result["worker_cleaned"]
        and result["accounting_confirmed"]
    )
    if result["cleanup_confirmed"]:
        emit("retired")
    return _public_result(result)


def _public_result(result: dict[str, bool | str]) -> dict[str, bool | str]:
    return {
        key: value
        for key, value in result.items()
        if key not in {"worker_cleaned", "accounting_confirmed"}
    }


def _cancel_thread(
    thread: threading.Thread, cancel: threading.Event
) -> BaseException | None:
    cancel.set()
    try:
        if thread.ident is not None:
            thread.join(1.1)
    except (Exception, KeyboardInterrupt, SystemExit, CancelledError) as error:  # noqa: BLE001 - preserve cleanup error for caller, never suppress it
        return error
    return None


def _owner(
    cancel: threading.Event,
    emit: Callable[[str], None],
    result: dict[str, bool | str],
    *,
    crash_requested: threading.Event | None = None,
) -> None:
    state = NativeState()
    baseline = state.read()
    original = state.hit(baseline)
    pid = state.process(original)
    if (
        cancel.is_set()
        or not baseline.cursor[0] & 1
        or not original
        or not pid
        or baseline.gui[0] & 0x1E
        or any(baseline.gui[i] for i in (3, 4, 5))
    ):
        result["reason"] = "foreign-state-refused"
        emit("refused")
        return
    # Existing worker performs its complete desktop/recipient/integrity admission.
    worker = CaptureInputWorker(
        Point(*baseline.cursor[2:]),
        Rect(baseline.cursor[2] - 16, baseline.cursor[3] - 16, 32, 32),
    )
    _RETAINED.append(worker)
    guard = OriginalRecipientGuard(state, original, pid)
    surface: OwnedSurface | None = None
    scope: OwnedCursorScope | None = None
    try:
        result["worker_cleaned"] = False
        worker.start()
        if not worker.ready.wait(2.0) or not worker.active:
            return
        emit("baseline-admitted")

        def safe() -> bool:
            worker.heartbeat()
            worker.drain()
            current = state.read()
            return (
                not cancel.is_set()
                and worker.active
                and (surface is None or not surface.failed)
                and current.foreground == baseline.foreground
                and current.gui == baseline.gui
                and current.cursor[2:] == baseline.cursor[2:]
                and state.process(original) == pid
                and guard.permits(baseline.foreground)
            )

        surface = OwnedSurface(state, baseline, safe)
        guard.owned = lambda: surface.handle
        state.api.ShowCursor.argtypes = [w.BOOL]
        state.api.ShowCursor.restype = ctypes.c_int
        scope = OwnedCursorScope(threading.get_ident, state.api.ShowCursor)

        def observed(outcome: Outcome) -> None:
            result["admitted"] = outcome.admitted
            emit("admitted")

        result["hwnd_destroyed"] = False
        terminate = _self_terminator()

        def active() -> None:
            _crash_if_requested(scope, crash_requested, emit, terminate)

        outcome = run_owner(
            scope,
            surface,
            Controls(safe, cancel.is_set, time.monotonic, observed, active),
        )
        result.update(
            admitted=outcome.admitted,
            balance_attempted=scope.balance_attempted,
            balance_confirmed=outcome.balanced,
            hwnd_destroyed=outcome.destroyed,
            foreign_preserved=not outcome.failed
            and not surface.failed
            and _foreign_equal(state, baseline),
            reason=(
                "foreign-state-changed"
                if outcome.failed or surface.failed
                else "cancelled"
                if cancel.is_set()
                else "completed"
                if outcome.admitted
                else "counter-refused"
                if scope.counter_refused
                else "no-natural-ownership"
            ),
        )
    finally:
        _accounting(scope, result)
        # run_owner already balances and destroys before worker draining.
        try:
            worker.release()
        finally:
            if (
                worker.stopped.wait(worker.options.drain_seconds + 0.1)
                and worker.cleaned
            ):
                _RETAINED.remove(worker)
                result["worker_cleaned"] = True


def _accounting(scope: OwnedCursorScope | None, result: dict[str, bool | str]) -> None:
    if scope is not None:
        result["balance_attempted"] = scope.balance_attempted
        result["accounting_confirmed"] = scope.accounting_confirmed
        result["balance_confirmed"] = scope.balance_confirmed


def _foreign_equal(state: NativeState, baseline: PreservedState) -> bool:
    current = state.read()
    return current.foreground == baseline.foreground and current.gui == baseline.gui


def _self_terminator() -> Callable[[], None]:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
    kernel.TerminateProcess.restype = w.BOOL
    process = kernel.GetCurrentProcess()

    def terminate() -> None:
        kernel.TerminateProcess(process, 97)
        raise RuntimeError("self-termination-returned")

    return terminate


def _crash_if_requested(
    scope: OwnedCursorScope,
    requested: threading.Event | None,
    emit: Callable[[str], None],
    terminate: Callable[[], None],
) -> None:
    if requested is None or not requested.is_set() or not scope.crash_eligible:
        return
    emit("crash-entry")
    terminate()
    raise RuntimeError("self-termination-returned")
