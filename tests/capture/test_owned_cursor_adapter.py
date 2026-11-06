"""Actual adapter methods with injected objects, never real Windows APIs."""

import threading
from asyncio import CancelledError
from types import SimpleNamespace
from typing import cast

import pytest
from tests.capture import owned_cursor_native as native
from tests.capture.owned_cursor_native import OriginalRecipientGuard
from tests.capture.owned_cursor_policy import OwnedCursorScope
from tests.capture.owned_cursor_surface import NativeState, OwnedSurface, PreservedState

from verdiclip.capture.input_integrity import GuiThreadInfo


@pytest.mark.parametrize("hit", [10, 20, 30, 0])
def test_original_recipient_substitution_requires_exact_hit(hit: int) -> None:
    """An unrelated window cannot be hidden behind the saved process identity."""
    guard = object.__new__(OriginalRecipientGuard)
    guard.state = cast(
        NativeState,
        SimpleNamespace(process=lambda _: 99, read=lambda: None, hit=lambda _: hit),
    )
    guard.original, guard.pid = 10, 99
    guard.owned = lambda: 20
    assert guard._pointer_recipient(GuiThreadInfo()) == (10 if hit in (10, 20) else 0)


@pytest.mark.parametrize(
    "error", [RuntimeError, KeyboardInterrupt, SystemExit, CancelledError]
)
def test_callback_fault_is_latched_without_escaping_ctypes(
    error: type[BaseException],
) -> None:
    """Control exceptions are reraised only by the ordinary owner pump frame."""
    surface = object.__new__(OwnedSurface)
    surface.state = cast(
        NativeState, SimpleNamespace(read=lambda: None, hit=lambda _: 10)
    )
    surface.handle, surface.failed, surface.control = 10, False, None
    surface.safe = lambda: True

    def fail() -> None:
        raise error()

    surface.on_cursor = fail
    handled = surface._message(10, 0x20, 10, 1)
    assert handled == 1
    assert surface.failed
    if error is RuntimeError:
        assert surface.control is None
    else:
        assert isinstance(surface.control, error)
        with pytest.raises(error):
            surface.pump()


@pytest.mark.parametrize("failure", ["start", "join", "control", "timeout"])
def test_wrapper_incomplete_thread_exit_cancels_and_retains(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Actual wrapper never abandons a possibly started thread on failure."""
    calls: list[str] = []
    cancel = threading.Event()

    class Thread:
        ident: int | None = None

        def __init__(self, **_: object) -> None:
            pass

        def start(self) -> None:
            calls.append("start")
            if failure == "start":
                raise RuntimeError("start-failed")
            self.ident = 1

        def join(self, _: float) -> None:
            calls.append("join")
            if len(calls) == 2 and failure in {"join", "control"}:
                raise (
                    KeyboardInterrupt()
                    if failure == "control"
                    else RuntimeError("join-failed")
                )

        def is_alive(self) -> bool:
            return True

    monkeypatch.setattr(native.sys, "platform", "win32")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    monkeypatch.setenv("VERDICLIP_OWNED_CURSOR", "1")
    monkeypatch.setattr(native.threading, "Thread", Thread)
    if failure == "timeout":
        result = native.run_owned_surface(cancel, lambda _: None)
        assert result["cleanup_confirmed"] is False
        assert result["reason"] == "cleanup-unconfirmed"
        assert "worker_cleaned" not in result
    else:
        with pytest.raises(KeyboardInterrupt if failure == "control" else RuntimeError):
            native.run_owned_surface(cancel, lambda _: None)
    assert cancel.is_set()
    assert any(isinstance(owner, Thread) for owner in native._RETAINED)


def test_worker_release_failure_still_checks_retirement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing release call cannot skip independent stopped/cleaned evidence."""
    calls: list[str] = []
    baseline = PreservedState(1, (0, 1, 2, 0, 0, 0, 0), (1, 7, 20, 20))
    state = SimpleNamespace(
        read=lambda: baseline, hit=lambda _: 10, process=lambda _: 99
    )

    def release() -> None:
        calls.append("release")
        raise RuntimeError("release-failed")

    def stopped(_: float) -> bool:
        calls.append("stopped")
        return True

    worker = SimpleNamespace(
        start=lambda: None,
        ready=SimpleNamespace(wait=lambda _: False),
        active=False,
        release=release,
        stopped=SimpleNamespace(wait=stopped),
        cleaned=True,
        options=SimpleNamespace(drain_seconds=0.1),
    )
    monkeypatch.setattr(native, "NativeState", lambda: state)
    monkeypatch.setattr(native, "CaptureInputWorker", lambda *_: worker)
    monkeypatch.setattr(native, "OriginalRecipientGuard", lambda *_: None)
    result: dict[str, bool | str] = {}
    with pytest.raises(RuntimeError, match="release-failed"):
        native._owner(threading.Event(), lambda _: None, result)
    assert calls == ["release", "stopped"]
    assert result["worker_cleaned"] is True


def test_revocation_preserves_balance_evidence_in_public_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Policy failure remains visible without falsely denying a proven balance."""

    def owner(
        _: threading.Event,
        emit: object,
        result: dict[str, bool | str],
        *,
        crash_requested: threading.Event | None = None,
    ) -> None:
        del emit, crash_requested
        scope = OwnedCursorScope(lambda: 1, lambda visible: 0 if visible else -1)
        accepted = scope.admit(lambda: True, natural=True)
        assert accepted
        accepted = scope.observe(lambda: False)
        assert not accepted
        assert scope.failed
        native._accounting(scope, result)
        result.update(
            admitted=True,
            hwnd_destroyed=True,
            worker_cleaned=True,
            foreign_preserved=False,
            reason="foreign-state-changed",
        )

    monkeypatch.setattr(native.sys, "platform", "win32")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    monkeypatch.setenv("VERDICLIP_OWNED_CURSOR", "1")
    monkeypatch.setattr(native, "_owner", owner)
    result = native.run_owned_surface(threading.Event(), lambda _: None)
    assert result["balance_confirmed"] is True
    assert result["cleanup_confirmed"] is True
    assert result["foreign_preserved"] is False
    assert result["reason"] == "foreign-state-changed"


@pytest.mark.parametrize(
    "state", ["no-command", "unadmitted", "retired", "revoked", "eligible"]
)
def test_forced_exit_requires_live_unbalanced_scope(state: str) -> None:
    """A returning termination call cannot qualify as a forced process death."""
    calls: list[str] = []
    scope = OwnedCursorScope(lambda: 1, lambda visible: 0 if visible else -1)
    requested = threading.Event()
    if state != "no-command":
        requested.set()
    if state != "unadmitted":
        admitted = scope.admit(lambda: True, natural=True)
        assert admitted
    if state == "retired":
        scope.retire()
    if state == "revoked":
        scope.observe(lambda: False)

    def terminate() -> None:
        calls.append("terminate")

    try:
        if state == "eligible":
            with pytest.raises(RuntimeError, match="self-termination-returned"):
                native._crash_if_requested(scope, requested, calls.append, terminate)
        else:
            native._crash_if_requested(scope, requested, calls.append, terminate)
    finally:
        scope.retire()
    assert calls == (["crash-entry", "terminate"] if state == "eligible" else [])
