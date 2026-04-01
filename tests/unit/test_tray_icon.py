"""Tests for the system tray icon and context menu."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from verdiclip.tray.icon import TrayIcon, _create_default_icon


class TestCreateDefaultIcon:
    def test_returns_qicon(self, qapp) -> None:
        icon = _create_default_icon()
        assert isinstance(icon, QIcon), f"Expected icon to be instance of QIcon, got {type(icon)}"

    def test_icon_is_not_null(self, qapp) -> None:
        icon = _create_default_icon()
        assert not icon.isNull(), f"Expected icon.isNull() to be falsy, got {icon.isNull()}"


class TestTrayIconCreation:
    def test_creates_with_app_and_config(self, qapp, tmp_config) -> None:
        app = QApplication.instance()
        icon = TrayIcon(app, tmp_config)
        assert icon is not None, f"Expected icon to not be None, got {icon}"

    def test_tooltip_contains_verdiclip(self, qapp, tmp_config) -> None:
        app = QApplication.instance()
        icon = TrayIcon(app, tmp_config)
        assert "VerdiClip" in icon.toolTip(), (
            f"Expected 'VerdiClip' to be in icon.toolTip(), got {icon.toolTip()}"
        )


class TestTrayIconMenu:
    def test_context_menu_has_expected_actions(self, qapp, tmp_config) -> None:
        app = QApplication.instance()
        icon = TrayIcon(app, tmp_config)
        menu = icon.contextMenu()
        action_texts = [
            a.text().split("\t")[0] for a in menu.actions() if not a.isSeparator()
        ]
        expected = [
            "Capture Region",
            "Capture Window",
            "Capture Full Screen",
            "Open Image...",
            "Settings...",
            "About",
            "Exit",
        ]
        assert action_texts == expected, (
            f"Expected action base labels to equal expected, got {action_texts}"
        )


class TestTrayIconBehavior:
    def test_on_activated_trigger_calls_capture_region(self, qapp, tmp_config) -> None:
        app = QApplication.instance()
        icon = TrayIcon(app, tmp_config)
        with patch.object(icon, "capture_region") as mock_capture:
            icon._on_activated(QSystemTrayIcon.ActivationReason.Trigger)
            mock_capture.assert_called_once()

    def test_exit_app_hides_icon_and_quits(self, qapp, tmp_config) -> None:
        app = QApplication.instance()
        icon = TrayIcon(app, tmp_config)
        with patch.object(icon, "hide") as mock_hide, \
             patch.object(icon._app, "quit") as mock_quit:
            icon._exit_app()
            mock_hide.assert_called_once()
            mock_quit.assert_called_once()


class TestCaptureRegion:
    def test_creates_region_capture_and_starts_selection(self, qapp, tmp_config) -> None:
        app = QApplication.instance()
        icon = TrayIcon(app, tmp_config)
        mock_capture = MagicMock()
        with patch("verdiclip.capture.region.RegionCapture", return_value=mock_capture):
            icon.capture_region()
            mock_capture.start_selection.assert_called_once()
            kwargs = mock_capture.start_selection.call_args
            assert "on_captured" in kwargs.kwargs or len(kwargs.args) >= 1, (
                "Expected on_captured callback to be passed to start_selection"
            )

    def test_stores_active_capture_reference(self, qapp, tmp_config) -> None:
        app = QApplication.instance()
        icon = TrayIcon(app, tmp_config)
        mock_capture = MagicMock()
        with patch("verdiclip.capture.region.RegionCapture", return_value=mock_capture):
            icon.capture_region()
            assert icon._active_capture is mock_capture, (
                f"Expected icon._active_capture to be mock_capture, got {icon._active_capture}"
            )


class TestCaptureWindow:
    def test_captures_active_window_and_opens_editor(self, qapp, tmp_config) -> None:
        app = QApplication.instance()
        icon = TrayIcon(app, tmp_config)
        mock_pixmap = MagicMock(spec=QPixmap)
        mock_pixmap.isNull.return_value = False
        mock_editor = MagicMock()
        with patch("verdiclip.capture.window.WindowCapture") as mock_wc, \
             patch("verdiclip.editor.canvas.EditorWindow", return_value=mock_editor):
            mock_wc.capture_active_window.return_value = mock_pixmap
            icon.capture_window()
            mock_wc.capture_active_window.assert_called_once()
            mock_editor.show.assert_called_once()

    def test_passes_window_decorations_config(self, qapp, tmp_config) -> None:
        app = QApplication.instance()
        icon = TrayIcon(app, tmp_config)
        mock_pixmap = MagicMock(spec=QPixmap)
        mock_pixmap.isNull.return_value = False
        with patch("verdiclip.capture.window.WindowCapture") as mock_wc, \
             patch("verdiclip.editor.canvas.EditorWindow"):
            mock_wc.capture_active_window.return_value = mock_pixmap
            icon.capture_window()
            call_kwargs = mock_wc.capture_active_window.call_args
            assert "include_decorations" in call_kwargs.kwargs, (
                f"Expected 'include_decorations' in kwargs, got {call_kwargs.kwargs}"
            )


class TestCaptureScreen:
    def test_captures_all_monitors_and_opens_editor(self, qapp, tmp_config) -> None:
        app = QApplication.instance()
        icon = TrayIcon(app, tmp_config)
        mock_pixmap = MagicMock(spec=QPixmap)
        mock_pixmap.isNull.return_value = False
        mock_editor = MagicMock()
        with patch("verdiclip.capture.screen.ScreenCapture") as mock_sc, \
             patch("verdiclip.editor.canvas.EditorWindow", return_value=mock_editor):
            mock_sc.capture_all_monitors.return_value = mock_pixmap
            icon.capture_screen()
            mock_sc.capture_all_monitors.assert_called_once()
            mock_editor.show.assert_called_once()


class TestOpenEditor:
    def test_creates_editor_window_and_shows_it(self, qapp, tmp_config) -> None:
        app = QApplication.instance()
        icon = TrayIcon(app, tmp_config)
        mock_pixmap = MagicMock(spec=QPixmap)
        mock_editor = MagicMock()
        with patch("verdiclip.editor.canvas.EditorWindow", return_value=mock_editor):
            icon._open_editor(mock_pixmap)
            mock_editor.show.assert_called_once()

    def test_appends_editor_to_list(self, qapp, tmp_config) -> None:
        app = QApplication.instance()
        icon = TrayIcon(app, tmp_config)
        mock_editor = MagicMock()
        with patch("verdiclip.editor.canvas.EditorWindow", return_value=mock_editor):
            icon._open_editor(MagicMock(spec=QPixmap))
            assert mock_editor in icon._editors, (
                f"Expected editor in _editors list, got {icon._editors}"
            )


class TestOpenImage:
    def test_opens_file_dialog_and_loads_image(self, qapp, tmp_config) -> None:
        app = QApplication.instance()
        icon = TrayIcon(app, tmp_config)
        mock_pixmap = MagicMock(spec=QPixmap)
        mock_pixmap.isNull.return_value = False
        with (
            patch(
                "PySide6.QtWidgets.QFileDialog.getOpenFileName",
                return_value=("test.png", ""),
            ),
            patch("verdiclip.tray.icon.QPixmap", return_value=mock_pixmap),
            patch("verdiclip.editor.canvas.EditorWindow") as mock_ew,
        ):
            icon._open_image()
            mock_ew.assert_called_once()

    def test_does_nothing_when_dialog_cancelled(self, qapp, tmp_config) -> None:
        app = QApplication.instance()
        icon = TrayIcon(app, tmp_config)
        with (
            patch(
                "PySide6.QtWidgets.QFileDialog.getOpenFileName",
                return_value=("", ""),
            ),
            patch("verdiclip.editor.canvas.EditorWindow") as mock_ew,
        ):
            icon._open_image()
            mock_ew.assert_not_called()

    def test_does_not_open_editor_for_null_pixmap(self, qapp, tmp_config) -> None:
        app = QApplication.instance()
        icon = TrayIcon(app, tmp_config)
        mock_pixmap = MagicMock(spec=QPixmap)
        mock_pixmap.isNull.return_value = True
        with (
            patch(
                "PySide6.QtWidgets.QFileDialog.getOpenFileName",
                return_value=("bad.png", ""),
            ),
            patch("verdiclip.tray.icon.QPixmap", return_value=mock_pixmap),
            patch("verdiclip.editor.canvas.EditorWindow") as mock_ew,
        ):
            icon._open_image()
            mock_ew.assert_not_called()


class TestShowSettings:
    def test_creates_and_shows_settings_dialog(self, qapp, tmp_config) -> None:
        app = QApplication.instance()
        icon = TrayIcon(app, tmp_config)
        mock_dialog = MagicMock()
        with patch(
            "verdiclip.ui.settings_dialog.SettingsDialog",
            return_value=mock_dialog,
        ):
            icon._show_settings()
            mock_dialog.exec.assert_called_once()
            mock_dialog.settings_saved.connect.assert_called_once()


class TestAutoSave:
    """Tests for auto-save triggering in _handle_capture."""

    def test_auto_save_called_when_enabled(self, qapp, tmp_config) -> None:
        """FileExporter.auto_save is called when save.auto_save_enabled is True."""
        tmp_config.set("save.auto_save_enabled", True)
        app = QApplication.instance()
        icon = TrayIcon(app, tmp_config)
        mock_pixmap = MagicMock(spec=QPixmap)
        mock_pixmap.isNull.return_value = False
        with (
            patch("verdiclip.export.file_export.FileExporter") as mock_exporter,
            patch("verdiclip.editor.canvas.EditorWindow") as mock_ew,
        ):
            mock_exporter.auto_save.return_value = "C:\\saved.png"
            mock_ew.return_value = MagicMock()
            icon._handle_capture(mock_pixmap)
            mock_exporter.auto_save.assert_called_once_with(mock_pixmap, tmp_config), (
                "Expected FileExporter.auto_save to be called once with the pixmap and config"
            )

    def test_auto_save_not_called_when_disabled(self, qapp, tmp_config) -> None:
        """FileExporter.auto_save is NOT called when save.auto_save_enabled is False."""
        tmp_config.set("save.auto_save_enabled", False)
        app = QApplication.instance()
        icon = TrayIcon(app, tmp_config)
        mock_pixmap = MagicMock(spec=QPixmap)
        mock_pixmap.isNull.return_value = False
        with (
            patch("verdiclip.export.file_export.FileExporter") as mock_exporter,
            patch("verdiclip.editor.canvas.EditorWindow") as mock_ew,
        ):
            mock_ew.return_value = MagicMock()
            icon._handle_capture(mock_pixmap)
            mock_exporter.auto_save.assert_not_called(), (
                "Expected FileExporter.auto_save to NOT be called when auto_save_enabled is False"
            )

    def test_editor_opens_regardless_of_autosave_enabled(self, qapp, tmp_config) -> None:
        """Editor opens even when auto-save is enabled."""
        tmp_config.set("save.auto_save_enabled", True)
        app = QApplication.instance()
        icon = TrayIcon(app, tmp_config)
        mock_pixmap = MagicMock(spec=QPixmap)
        mock_pixmap.isNull.return_value = False
        mock_editor = MagicMock()
        with (
            patch("verdiclip.export.file_export.FileExporter") as mock_exporter,
            patch("verdiclip.editor.canvas.EditorWindow", return_value=mock_editor),
        ):
            mock_exporter.auto_save.return_value = "C:\\saved.png"
            icon._handle_capture(mock_pixmap)
            mock_editor.show.assert_called_once(), (
                "Expected editor.show() to be called once even with auto-save enabled"
            )

    def test_editor_opens_regardless_of_autosave_disabled(self, qapp, tmp_config) -> None:
        """Editor opens when auto-save is disabled."""
        tmp_config.set("save.auto_save_enabled", False)
        app = QApplication.instance()
        icon = TrayIcon(app, tmp_config)
        mock_pixmap = MagicMock(spec=QPixmap)
        mock_pixmap.isNull.return_value = False
        mock_editor = MagicMock()
        with (
            patch("verdiclip.export.file_export.FileExporter"),
            patch("verdiclip.editor.canvas.EditorWindow", return_value=mock_editor),
        ):
            icon._handle_capture(mock_pixmap)
            mock_editor.show.assert_called_once(), (
                "Expected editor.show() to be called once even with auto-save disabled"
            )


class TestShowAbout:
    def test_creates_and_shows_about_dialog(self, qapp, tmp_config) -> None:
        app = QApplication.instance()
        icon = TrayIcon(app, tmp_config)
        mock_dialog = MagicMock()
        with patch("verdiclip.ui.about_dialog.AboutDialog", return_value=mock_dialog) as mock_cls:
            icon._show_about()
            mock_cls.assert_called_once()
            mock_dialog.exec.assert_called_once()
