"""Tests for verdiclip.export.file_export.FileExporter."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap

from verdiclip.config import Config
from verdiclip.export.file_export import FileExporter

# ---------------------------------------------------------------------------
# _get_next_counter
# ---------------------------------------------------------------------------


class TestGetNextCounterEmptyDir:
    def test_returns_one_for_empty_directory(self, tmp_path: Path) -> None:
        assert FileExporter._get_next_counter(tmp_path, "png") == 1


class TestGetNextCounterWithFiles:
    def test_returns_count_plus_one(self, tmp_path: Path) -> None:
        (tmp_path / "shot_001.png").touch()
        (tmp_path / "shot_002.png").touch()
        (tmp_path / "shot_003.png").touch()
        assert FileExporter._get_next_counter(tmp_path, "png") == 4

    def test_ignores_other_extensions(self, tmp_path: Path) -> None:
        (tmp_path / "image.jpg").touch()
        (tmp_path / "image.bmp").touch()
        (tmp_path / "image.png").touch()
        assert FileExporter._get_next_counter(tmp_path, "png") == 2


# ---------------------------------------------------------------------------
# auto_save
# ---------------------------------------------------------------------------


class TestAutoSave:
    def _make_config(self, tmp_path: Path, **overrides) -> Config:
        """Create a Config pointing at *tmp_path* with optional save overrides."""
        cfg = Config(config_path=tmp_path / "config.json")
        cfg.set("save.default_directory", str(tmp_path / "screenshots"))
        cfg.set("save.default_format", "png")
        cfg.set("save.filename_pattern", "Screenshot_{datetime}")
        for key, val in overrides.items():
            cfg.set(f"save.{key}", val)
        return cfg

    def test_creates_file_in_configured_directory(
        self, qapp, tmp_path: Path
    ) -> None:
        cfg = self._make_config(tmp_path)
        pixmap = QPixmap(40, 40)
        pixmap.fill(Qt.GlobalColor.blue)

        result = FileExporter.auto_save(pixmap, cfg)

        assert result is not None
        saved = Path(result)
        assert saved.exists()
        assert saved.parent == tmp_path / "screenshots"
        assert saved.suffix == ".png"

    def test_creates_save_directory_if_missing(
        self, qapp, tmp_path: Path
    ) -> None:
        nested = tmp_path / "a" / "b" / "c"
        cfg = Config(config_path=tmp_path / "config.json")
        cfg.set("save.default_directory", str(nested))

        pixmap = QPixmap(10, 10)
        pixmap.fill(Qt.GlobalColor.green)

        result = FileExporter.auto_save(pixmap, cfg)

        assert result is not None
        assert nested.is_dir()

    def test_uses_filename_pattern_with_counter(
        self, qapp, tmp_path: Path
    ) -> None:
        cfg = self._make_config(
            tmp_path, filename_pattern="capture_{counter}"
        )
        pixmap = QPixmap(10, 10)
        pixmap.fill(Qt.GlobalColor.red)

        result = FileExporter.auto_save(pixmap, cfg)

        assert result is not None
        assert Path(result).stem == "capture_1"

    def test_uses_filename_pattern_with_datetime(
        self, qapp, tmp_path: Path
    ) -> None:
        cfg = self._make_config(
            tmp_path, filename_pattern="shot_{datetime}"
        )
        pixmap = QPixmap(10, 10)
        pixmap.fill(Qt.GlobalColor.red)

        result = FileExporter.auto_save(pixmap, cfg)

        assert result is not None
        assert Path(result).stem.startswith("shot_")

    def test_uses_configured_format(self, qapp, tmp_path: Path) -> None:
        cfg = self._make_config(tmp_path, default_format="bmp")
        pixmap = QPixmap(10, 10)
        pixmap.fill(Qt.GlobalColor.red)

        result = FileExporter.auto_save(pixmap, cfg)

        assert result is not None
        assert Path(result).suffix == ".bmp"

    def test_jpg_quality_from_config(self, qapp, tmp_path: Path) -> None:
        cfg = self._make_config(
            tmp_path, default_format="jpg", jpg_quality=50
        )
        pixmap = QPixmap(10, 10)
        pixmap.fill(Qt.GlobalColor.red)
        mock_save = MagicMock(return_value=True)
        pixmap.save = mock_save  # type: ignore[method-assign]

        FileExporter.auto_save(pixmap, cfg)

        mock_save.assert_called_once()
        _path_arg, fmt_arg, quality_arg = mock_save.call_args[0]
        assert fmt_arg == "JPG"
        assert quality_arg == 50

    def test_returns_file_path_on_success(
        self, qapp, tmp_path: Path
    ) -> None:
        cfg = self._make_config(tmp_path)
        pixmap = QPixmap(10, 10)
        pixmap.fill(Qt.GlobalColor.red)

        result = FileExporter.auto_save(pixmap, cfg)

        assert isinstance(result, str)
        assert Path(result).exists()

    def test_returns_none_on_save_failure(
        self, qapp, tmp_path: Path
    ) -> None:
        cfg = self._make_config(tmp_path)
        pixmap = QPixmap(10, 10)
        pixmap.fill(Qt.GlobalColor.red)
        pixmap.save = MagicMock(return_value=False)  # type: ignore[method-assign]

        result = FileExporter.auto_save(pixmap, cfg)

        assert result is None


# ---------------------------------------------------------------------------
# save_with_dialog
# ---------------------------------------------------------------------------


class TestSaveWithDialog:
    def test_calls_auto_save_when_enabled(
        self, qapp, tmp_path: Path
    ) -> None:
        cfg = Config(config_path=tmp_path / "config.json")
        cfg.set("save.auto_save_enabled", True)
        cfg.set("save.default_directory", str(tmp_path))

        pixmap = QPixmap(10, 10)
        pixmap.fill(Qt.GlobalColor.red)

        with patch.object(
            FileExporter, "auto_save", return_value="/fake/path.png"
        ) as mock_auto:
            result = FileExporter.save_with_dialog(pixmap, cfg)

        mock_auto.assert_called_once_with(pixmap, cfg)
        assert result == "/fake/path.png"

    def test_calls_save_as_when_auto_save_disabled(
        self, qapp, tmp_path: Path
    ) -> None:
        cfg = Config(config_path=tmp_path / "config.json")
        cfg.set("save.auto_save_enabled", False)

        pixmap = QPixmap(10, 10)
        pixmap.fill(Qt.GlobalColor.red)

        with patch.object(
            FileExporter, "save_as", return_value="/dialog/path.png"
        ) as mock_sa:
            result = FileExporter.save_with_dialog(pixmap, cfg)

        mock_sa.assert_called_once()
        assert result == "/dialog/path.png"

    def test_auto_save_success_skips_dialog(
        self, qapp, tmp_path: Path
    ) -> None:
        cfg = Config(config_path=tmp_path / "config.json")
        cfg.set("save.auto_save_enabled", True)
        cfg.set("save.default_directory", str(tmp_path))

        pixmap = QPixmap(10, 10)
        pixmap.fill(Qt.GlobalColor.red)

        with (
            patch.object(
                FileExporter, "auto_save", return_value="/auto/path.png"
            ),
            patch.object(FileExporter, "save_as") as mock_sa,
        ):
            result = FileExporter.save_with_dialog(pixmap, cfg)

        mock_sa.assert_not_called()
        assert result == "/auto/path.png"


# ---------------------------------------------------------------------------
# save_as
# ---------------------------------------------------------------------------


class TestSaveAs:
    def test_returns_none_when_dialog_cancelled(
        self, qapp, tmp_path: Path
    ) -> None:
        pixmap = QPixmap(10, 10)
        pixmap.fill(Qt.GlobalColor.red)

        with patch(
            "verdiclip.export.file_export.QFileDialog.getSaveFileName",
            return_value=("", ""),
        ):
            result = FileExporter.save_as(pixmap)

        assert result is None

    def test_adds_default_extension_when_none_provided(
        self, qapp, tmp_path: Path
    ) -> None:
        cfg = Config(config_path=tmp_path / "config.json")
        cfg.set("save.default_format", "png")

        file_no_ext = str(tmp_path / "myshot")
        pixmap = QPixmap(10, 10)
        pixmap.fill(Qt.GlobalColor.red)
        mock_save = MagicMock(return_value=True)
        pixmap.save = mock_save  # type: ignore[method-assign]

        with patch(
            "verdiclip.export.file_export.QFileDialog.getSaveFileName",
            return_value=(file_no_ext, "PNG Image (*.png)"),
        ):
            result = FileExporter.save_as(pixmap, config=cfg)

        assert result is not None
        assert result.endswith(".png")

    def test_calls_save_with_correct_format_and_quality(
        self, qapp, tmp_path: Path
    ) -> None:
        cfg = Config(config_path=tmp_path / "config.json")
        cfg.set("save.default_format", "jpg")
        cfg.set("save.jpg_quality", 75)

        file_path = str(tmp_path / "photo.jpg")
        pixmap = QPixmap(10, 10)
        pixmap.fill(Qt.GlobalColor.red)
        mock_save = MagicMock(return_value=True)
        pixmap.save = mock_save  # type: ignore[method-assign]

        with patch(
            "verdiclip.export.file_export.QFileDialog.getSaveFileName",
            return_value=(file_path, "JPEG Image (*.jpg *.jpeg)"),
        ):
            FileExporter.save_as(pixmap, config=cfg)

        mock_save.assert_called_once()
        _path_arg, fmt_arg, quality_arg = mock_save.call_args[0]
        assert fmt_arg == "JPG"
        assert quality_arg == 75
