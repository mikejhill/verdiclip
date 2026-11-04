"""Actual GUI entry sequencing with fake integration owners and no event loop."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication

from verdiclip import __main__ as entry
from verdiclip.scrolling import ScrollingStorageError
from verdiclip.settings import SettingsStore


@pytest.mark.parametrize("mode", ["normal", "forwarded", "storage", "partial", "start"])
def test_real_entry_composes_once_before_start_with_retirement_registered(  # noqa: C901 - five explicit entry/cleanup outcomes
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    mode: str,
) -> None:
    """Known storage refusal is graceful; partial attachment/start errors propagate."""
    del qapp
    calls: list[str] = []
    error = RuntimeError("synthetic operation failure")

    class Controller:
        """No widgets, hooks, settings writes or native integrations."""

        def start(self) -> None:
            calls.append("start")
            if mode == "start":
                raise error

        def shutdown(self) -> None:
            calls.append("shutdown")

        def handle_forwarded(self, _arguments: list[str]) -> None:
            pytest.fail("No forwarded message is emitted in this fixture")

    controller = Controller()
    instance = SimpleNamespace(
        close=lambda: calls.append("instance-close"),
        forward=lambda _files: mode == "forwarded",
        listen=lambda: calls.append("listen"),
        message_received=SimpleNamespace(connect=Mock(), disconnect=Mock()),
    )
    monkeypatch.setattr(entry, "SingleInstance", lambda: instance)
    monkeypatch.setattr(entry, "AppController", lambda *_args: controller)
    monkeypatch.setattr(
        entry,
        "HotkeyService",
        lambda: SimpleNamespace(close=lambda: calls.append("hotkeys-close")),
    )
    monkeypatch.setattr(
        entry,
        "ShellLinkBackend",
        lambda: SimpleNamespace(known_folder=lambda _kind: tmp_path),
    )
    for name in (
        "MssScreenSource",
        "WindowLocator",
        "ShortcutRegistration",
        "StartupRegistration",
        "WinRegRunKey",
        "FileAssociations",
        "LowLevelKeyboardHook",
        "Updater",
        "ReleaseFeed",
        "UpdateLauncher",
    ):
        monkeypatch.setattr(entry, name, lambda *_args: object())
    monkeypatch.setattr(entry.Installation, "current", staticmethod(object))
    monkeypatch.setattr(entry.Application, "_install_sigint", lambda *_args: None)
    monkeypatch.setattr(QApplication, "exec", lambda _self: calls.append("exec") or 0)

    def compose(owner: object, *, data_root: Path) -> None:
        assert owner is controller
        assert data_root == tmp_path
        calls.append("compose")
        if mode == "storage":
            raise ScrollingStorageError("synthetic private storage refusal")
        if mode == "partial":
            raise error

    monkeypatch.setattr(entry, "compose_scrolling", compose)
    app = entry.Application()
    request = entry.CommandLine().parse([])
    store = SettingsStore(tmp_path / "settings.json")
    if mode in {"partial", "start"}:
        with pytest.raises(RuntimeError) as caught:
            app._run_gui(request, store)
        assert caught.value is error
        assert "exec" not in calls
    else:
        assert app._run_gui(request, store) == 0
    if mode == "forwarded":
        assert calls == ["instance-close"]
        return
    assert calls.count("compose") == 1
    assert calls.count("shutdown") == 1
    assert calls[-3:] == ["shutdown", "hotkeys-close", "instance-close"]
    if mode != "partial":
        assert calls.index("compose") < calls.index("start")
    if mode == "storage":
        assert "Scrolling capture unavailable" in caplog.text
        assert "exec" in calls
