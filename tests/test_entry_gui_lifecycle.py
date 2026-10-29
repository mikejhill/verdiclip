"""Hosted-only actual entry methods with inert lifecycle ports."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from verdiclip import __main__ as entry
from verdiclip.__main__ import Application


class PortSignal:
    """Only explicitly connected callbacks can run in this inert signal."""

    def __init__(self) -> None:
        self.callbacks: list[Any] = []

    def connect(self, callback: Callable[..., object]) -> None:
        """Retain the exact callback."""
        self.callbacks.append(callback)

    def disconnect(self, callback: Callable[..., object]) -> None:
        """Retire one owned connection."""
        self.callbacks.remove(callback)

    def emit(self) -> None:
        """Model quit notification without an application event loop."""
        for callback in tuple(self.callbacks):
            callback()


class StartupError(Exception):
    """A stand-in for the readiness AppError."""


class Harness:
    """Only Python objects stand in for Qt, signals, timers and OS services."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: C901 - explicit inert lifecycle ports
        self.events: list[str] = []
        self.start_error: BaseException | None = StartupError("fonts")
        self.construct_error: BaseException | None = None
        self.shutdown_error: BaseException | None = None
        self.timer_error: BaseException | None = None
        self.forward = False
        self.listening = False
        self.filter_installed = False
        self.registered = False
        self.tray_visible = False
        self.timer_active = False
        self.exec_calls = 0
        self.timer: Any = None
        self.app: Any = None
        self.instance: Any = None
        self.previous = object()
        self.current_handler: object = self.previous
        harness = self

        class App:
            aboutToQuit = PortSignal()  # noqa: N815 - exact Qt signal spelling

            @staticmethod
            def instance() -> object:
                return harness.app

            def setApplicationName(self, _value: str) -> None:  # noqa: N802 - exact Qt port
                pass

            def setApplicationVersion(self, _value: str) -> None:  # noqa: N802 - exact Qt port
                pass

            def setQuitOnLastWindowClosed(self, _value: object) -> None:  # noqa: N802 - exact Qt port
                pass

            def exec(self) -> int:
                harness.exec_calls += 1
                self.aboutToQuit.emit()
                return 7

            def quit(self) -> None:
                raise AssertionError("Borrowed application must not be quit")

        class Instance:
            def __init__(self) -> None:
                self.message_received = PortSignal()
                harness.instance = self

            def forward(self, _files: list[str]) -> bool:
                return harness.forward

            def listen(self) -> bool:
                harness.listening = True
                return True

            def close(self) -> None:
                harness.events.append("instance_close")
                harness.listening = False

        class Hotkeys:
            def __init__(self) -> None:
                harness.filter_installed = True

            def close(self) -> None:
                if harness.filter_installed:
                    harness.events.append("filter_close")
                harness.filter_installed = False

        class Controller:
            def __init__(self, *_args: object) -> None:
                if harness.construct_error is not None:
                    raise harness.construct_error
                self.hotkeys: Any = _args[3]

            def handle_forwarded(self, _files: list[str]) -> None:
                raise AssertionError("Retired callback must not run")

            def start(self) -> None:
                if harness.start_error is not None:
                    raise harness.start_error
                harness.registered = True
                harness.tray_visible = True

            def shutdown(self) -> None:
                harness.events.append("shutdown")
                if harness.shutdown_error is not None:
                    raise harness.shutdown_error
                self.hotkeys.close()
                harness.registered = False
                harness.tray_visible = False

        class Timer:
            def __init__(self) -> None:
                self.timeout = PortSignal()
                harness.timer = self

            def start(self, _interval: int) -> None:
                harness.timer_active = True
                if harness.timer_error is not None:
                    raise harness.timer_error

            def stop(self) -> None:
                harness.events.append("timer_stop")
                harness.timer_active = False

        def set_handler(_signum: int, handler: object) -> None:
            self.current_handler = handler

        namespace: dict[str, Any] = {
            "QApplication": App,
            "QTimer": Timer,
            "SingleInstance": Instance,
            "HotkeyService": Hotkeys,
            "AppController": Controller,
            "AppError": StartupError,
            "APP_NAME": "Fixture",
            "VERSION": "0",
            "SIGNAL_POLL_MS": 200,
            "logger": logging.getLogger(__name__),
            "sys": SimpleNamespace(argv=["fixture"]),
            "signal": SimpleNamespace(
                SIGINT=2,
                getsignal=lambda _signum: self.current_handler,
                signal=set_handler,
            ),
            "ShellLinkBackend": lambda: SimpleNamespace(known_folder=lambda _f: "f"),
            "KnownFolder": SimpleNamespace(STARTUP=1, PROGRAMS=2),
            "Installation": SimpleNamespace(current=object),
        }
        for name in (
            "MssScreenSource",
            "WindowLocator",
            "ShellIntegration",
            "StartupRegistration",
            "ShortcutRegistration",
            "WinRegRunKey",
            "FileAssociations",
            "LowLevelKeyboardHook",
            "Updater",
            "ReleaseFeed",
            "UpdateLauncher",
        ):
            namespace[name] = lambda *_args: object()
        for name, port in namespace.items():
            monkeypatch.setattr(entry, name, port)
        self.app = App()
        self.entry = Application()
        self.request = entry.CliRequest(entry.Command.TRAY)
        self.store = entry.SettingsStore(tmp_path / "settings.json")

    def run(self) -> int:
        """Call the actual GUI lifecycle using only these inert ports."""
        result = self.entry._run_gui(self.request, self.store)
        assert isinstance(result, int)
        return result

    def assert_retired(self) -> None:
        """No callback or acquired service may survive return/failure."""
        assert not self.listening
        assert not self.filter_installed
        assert not self.timer_active
        assert not self.app.aboutToQuit.callbacks
        assert not self.instance.message_received.callbacks
        assert self.current_handler is self.previous


def test_readiness_failure_retires_owners_without_entering_exec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The original exception survives normal independent cleanup."""
    harness = Harness(tmp_path, monkeypatch)
    with pytest.raises(StartupError) as caught:
        harness.run()
    assert caught.value is harness.start_error
    harness.assert_retired()
    assert harness.exec_calls == 0
    assert not harness.registered
    assert not harness.tray_visible


def test_controller_construction_failure_still_closes_listener_and_filter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ownership starts before constructing the controller."""
    harness = Harness(tmp_path, monkeypatch)
    harness.construct_error = RuntimeError("constructor")
    with pytest.raises(RuntimeError) as caught:
        harness.run()
    assert caught.value is harness.construct_error
    harness.assert_retired()


def test_partial_signal_setup_stops_timer_and_restores_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A timer that starts and then raises is still an acquired owner."""
    harness = Harness(tmp_path, monkeypatch)
    harness.timer_error = RuntimeError("partial timer")
    with pytest.raises(RuntimeError) as caught:
        harness.run()
    assert caught.value is harness.timer_error
    harness.assert_retired()


def test_shutdown_failure_does_not_skip_filter_or_instance_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ExitStack retains the primary failure in the cleanup exception chain."""
    harness = Harness(tmp_path, monkeypatch)
    harness.shutdown_error = RuntimeError("shutdown")
    with pytest.raises(StartupError) as caught:
        harness.run()
    assert caught.value is harness.start_error
    assert caught.value.__cause__ is harness.shutdown_error
    harness.assert_retired()


def test_repeated_runs_on_borrowed_application_leave_no_connections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Quit notification and finally share one idempotent ExitStack owner."""
    harness = Harness(tmp_path, monkeypatch)
    harness.start_error = None
    assert harness.run() == 7
    harness.assert_retired()
    assert harness.run() == 7
    harness.assert_retired()
    assert harness.events.count("shutdown") == 2


def test_forwarded_launch_does_not_acquire_controller_or_signal_timer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A secondary instance returns without changing process signal state."""
    harness = Harness(tmp_path, monkeypatch)
    harness.forward = True
    assert harness.run() == 0
    harness.assert_retired()
    assert harness.timer is None
    assert harness.events == ["instance_close"]


def test_restore_does_not_overwrite_an_intervening_signal_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cleanup may restore only the handler this run actually installed."""
    harness = Harness(tmp_path, monkeypatch)
    harness.entry._install_sigint(harness.app)
    replacement = object()
    harness.current_handler = replacement
    harness.entry._stop_signal_timer()
    harness.entry._restore_sigint()
    assert harness.current_handler is replacement


def test_cleanup_control_propagates_after_other_owners_retire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cleanup interrupt cannot become a logged-and-swallowed AppError."""
    harness = Harness(tmp_path, monkeypatch)
    harness.shutdown_error = KeyboardInterrupt("cleanup")
    with pytest.raises(KeyboardInterrupt) as caught:
        harness.run()
    assert caught.value is harness.shutdown_error
    assert caught.value.__context__ is harness.start_error
    harness.assert_retired()


def test_cleanup_failure_after_success_is_not_silenced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful body cannot conceal failed owned shutdown."""
    harness = Harness(tmp_path, monkeypatch)
    harness.start_error = None
    harness.shutdown_error = RuntimeError("cleanup")
    with pytest.raises(RuntimeError) as caught:
        harness.run()
    assert caught.value is harness.shutdown_error
    harness.assert_retired()


@pytest.mark.parametrize(
    "primary", [KeyboardInterrupt("body"), SystemExit(130), GeneratorExit("body")]
)
def test_primary_control_keeps_identity_when_shutdown_fails(
    tmp_path: Path, primary: BaseException, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ordinary cleanup errors cannot replace the operation's genuine control."""
    harness = Harness(tmp_path, monkeypatch)
    harness.start_error = primary
    harness.shutdown_error = RuntimeError("cleanup")
    with pytest.raises(type(primary)) as caught:
        harness.run()
    assert caught.value is primary
    assert primary.__cause__ is harness.shutdown_error
    harness.assert_retired()


def test_unknown_signal_handler_is_not_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unrestorable native handler blocks admission without any signal write."""
    harness = Harness(tmp_path, monkeypatch)
    harness.previous = None
    harness.current_handler = None
    with pytest.raises(StartupError, match="unknown SIGINT handler"):
        harness.run()
    harness.assert_retired()
    assert harness.timer is None
    assert harness.exec_calls == 0
    assert not harness.registered
    assert not harness.tray_visible
