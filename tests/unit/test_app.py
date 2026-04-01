"""Tests for verdiclip.app.VerdiClipApp."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QSharedMemory
from PySide6.QtWidgets import QApplication

from verdiclip import __app_name__, __version__
from verdiclip.app import VerdiClipApp

if TYPE_CHECKING:
    from verdiclip.config import Config


class TestVerdiClipAppInit:
    """VerdiClipApp.__init__ sets expected attributes."""

    def test_sets_argv(self, qapp: QApplication) -> None:
        app = VerdiClipApp(["--test"])
        assert app._argv == ["--test"], f"Expected argv ['--test'], got {app._argv}"

    def test_shared_memory_is_initialized(self, qapp: QApplication) -> None:
        app = VerdiClipApp([])
        assert isinstance(app._shared_memory, QSharedMemory), (
            f"Expected QSharedMemory instance, got {type(app._shared_memory).__name__}"
        )

    def test_qt_app_is_none(self, qapp: QApplication) -> None:
        app = VerdiClipApp([])
        assert app._qt_app is None, f"Expected _qt_app to be None, got {app._qt_app}"

    def test_config_is_none(self, qapp: QApplication) -> None:
        app = VerdiClipApp([])
        assert app._config is None, f"Expected _config to be None, got {app._config}"


class TestIsAlreadyRunning:
    """_is_already_running returns False when no shared memory is attached."""

    def test_returns_false_when_no_shared_memory_attached(self, qapp: QApplication) -> None:
        app = VerdiClipApp([])
        assert app._is_already_running() is False, (
            f"Expected _is_already_running() to be False, got {app._is_already_running()}"
        )


class TestInitQt:
    """_init_qt configures and returns a QApplication."""

    def test_returns_qapplication(self, qapp: QApplication) -> None:
        app = VerdiClipApp([])
        with patch("verdiclip.app.QApplication", return_value=qapp):
            result = app._init_qt()
        assert isinstance(result, QApplication), (
            f"Expected QApplication instance, got {type(result).__name__}"
        )

    def test_sets_application_name(self, qapp: QApplication) -> None:
        app = VerdiClipApp([])
        with patch("verdiclip.app.QApplication", return_value=qapp):
            result = app._init_qt()
        assert result.applicationName() == __app_name__, (
            f"Expected applicationName '{__app_name__}', got '{result.applicationName()}'"
        )

    def test_sets_application_version(self, qapp: QApplication) -> None:
        app = VerdiClipApp([])
        with patch("verdiclip.app.QApplication", return_value=qapp):
            result = app._init_qt()
        assert result.applicationVersion() == __version__, (
            f"Expected applicationVersion '{__version__}', got '{result.applicationVersion()}'"
        )

    def test_quit_on_last_window_closed_is_false(self, qapp: QApplication) -> None:
        app = VerdiClipApp([])
        with patch("verdiclip.app.QApplication", return_value=qapp):
            result = app._init_qt()
        assert result.quitOnLastWindowClosed() is False, (
            f"Expected quitOnLastWindowClosed to be False, got {result.quitOnLastWindowClosed()}"
        )


class TestConfigProperty:
    """Config property access."""

    def test_raises_assertion_error_when_config_is_none(self, qapp: QApplication) -> None:
        app = VerdiClipApp([])
        with pytest.raises(AssertionError):
            _ = app.config

    def test_returns_config_when_set(self, qapp: QApplication, tmp_config: Config) -> None:
        app = VerdiClipApp([])
        app._config = tmp_config
        assert app.config is tmp_config, (
            f"Expected config to be tmp_config, got {app.config}"
        )


class TestIsAlreadyRunningTrue:
    """_is_already_running returns True when shared memory can attach."""

    def test_returns_true_when_attached(self, qapp: QApplication) -> None:
        app = VerdiClipApp([])
        app._shared_memory = MagicMock()
        app._shared_memory.attach.return_value = True
        assert app._is_already_running() is True, (
            f"Expected _is_already_running() to be True, got {app._is_already_running()}"
        )
        app._shared_memory.detach.assert_called_once()


class TestRun:
    """VerdiClipApp.run orchestration."""

    def test_returns_1_when_already_running(
        self, qapp: QApplication,
    ) -> None:
        app = VerdiClipApp([])
        app._shared_memory = MagicMock()
        app._shared_memory.attach.return_value = True
        assert app.run() == 1, f"Expected run() to return 1 when already running, got {app.run()}"

    def test_successful_path(self, qapp: QApplication) -> None:
        app = VerdiClipApp([])
        app._shared_memory = MagicMock()
        app._shared_memory.attach.return_value = False
        app._shared_memory.create.return_value = True

        mock_qt_app = MagicMock()
        mock_qt_app.exec.return_value = 0

        with (
            patch.object(app, "_init_qt", return_value=mock_qt_app),
            patch("verdiclip.app.Config") as mock_config_cls,
            patch.object(app, "_setup_tray"),
            patch.object(app, "_setup_hotkeys"),
        ):
            result = app.run()

        assert result == 0, f"Expected run() to return 0 on success, got {result}"
        mock_qt_app.exec.assert_called_once()
        mock_config_cls.assert_called_once()

    def test_shared_memory_create_failure_continues(
        self, qapp: QApplication,
    ) -> None:
        app = VerdiClipApp([])
        app._shared_memory = MagicMock()
        app._shared_memory.attach.return_value = False
        app._shared_memory.create.return_value = False
        app._shared_memory.errorString.return_value = "err"

        mock_qt_app = MagicMock()
        mock_qt_app.exec.return_value = 0

        with (
            patch.object(app, "_init_qt", return_value=mock_qt_app),
            patch("verdiclip.app.Config"),
            patch.object(app, "_setup_tray"),
            patch.object(app, "_setup_hotkeys"),
        ):
            result = app.run()

        assert result == 0, (
            f"Expected run() to return 0 despite shared memory failure, got {result}"
        )


class TestSetupTray:
    """_setup_tray creates and shows the tray icon."""

    @patch("verdiclip.tray.icon.TrayIcon")
    def test_creates_and_shows_tray(
        self, mock_tray_cls, qapp: QApplication, tmp_config: Config,
    ) -> None:
        app = VerdiClipApp([])
        app._qt_app = qapp
        app._config = tmp_config
        mock_icon = MagicMock()
        mock_tray_cls.return_value = mock_icon

        app._setup_tray()

        mock_tray_cls.assert_called_once_with(qapp, tmp_config)
        mock_icon.show.assert_called_once()
        assert app._tray_icon is mock_icon, (
            f"Expected _tray_icon to be mock_icon, got {app._tray_icon}"
        )


class TestSetupHotkeys:
    """_setup_hotkeys creates and starts the hotkey manager."""

    @patch("verdiclip.hotkeys.manager.HotkeyManager")
    def test_creates_and_starts_hotkeys(
        self, mock_hk_cls, qapp: QApplication, tmp_config: Config,
    ) -> None:
        app = VerdiClipApp([])
        app._config = tmp_config
        mock_manager = MagicMock()
        mock_hk_cls.return_value = mock_manager

        app._setup_hotkeys()

        mock_hk_cls.assert_called_once_with(tmp_config)
        mock_manager.start.assert_called_once()
        assert app._hotkey_manager is mock_manager, (
            f"Expected _hotkey_manager to be mock_manager, got {app._hotkey_manager}"
        )
