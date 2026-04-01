"""Tests for verdiclip.cli module."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtGui import QPixmap

from verdiclip.cli import (
    _generate_output_path,
    _parse_region,
    build_parser,
    run_cli,
)

# ---------------------------------------------------------------------------
# build_parser
# ---------------------------------------------------------------------------


class TestBuildParser:
    def test_returns_argument_parser(self) -> None:
        parser = build_parser()
        assert isinstance(parser, argparse.ArgumentParser)

    def test_version_flag(self) -> None:
        parser = build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--version"])
        assert exc_info.value.code == 0

    def test_capture_subcommand_parses(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["capture", "screen", "-o", "out.png"])
        assert args.command == "capture"
        assert args.mode == "screen"
        assert args.output == "out.png"

    def test_capture_with_all_options(self) -> None:
        parser = build_parser()
        args = parser.parse_args([
            "capture", "region",
            "--region", "10,20,300,400",
            "-o", "test.jpg",
            "--format", "jpg",
            "--quality", "80",
            "--delay", "2.5",
        ])
        assert args.mode == "region"
        assert args.region == "10,20,300,400"
        assert args.format == "jpg"
        assert args.quality == 80
        assert args.delay == 2.5

    def test_capture_clipboard_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["capture", "screen", "--clipboard"])
        assert args.clipboard is True

    def test_open_subcommand_parses(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["open", "image.png"])
        assert args.command == "open"
        assert args.file == "image.png"

    def test_no_subcommand_gives_none(self) -> None:
        parser = build_parser()
        args = parser.parse_args([])
        assert args.command is None

    def test_capture_monitor_option(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["capture", "screen", "--monitor", "1"])
        assert args.monitor == 1


# ---------------------------------------------------------------------------
# _parse_region
# ---------------------------------------------------------------------------


class TestParseRegion:
    def test_valid_region(self) -> None:
        assert _parse_region("100,200,800,600") == (100, 200, 800, 600)

    def test_with_spaces(self) -> None:
        assert _parse_region(" 10 , 20 , 300 , 400 ") == (10, 20, 300, 400)

    def test_too_few_parts(self) -> None:
        with pytest.raises(ValueError, match="must be X,Y,W,H"):
            _parse_region("100,200")

    def test_too_many_parts(self) -> None:
        with pytest.raises(ValueError, match="must be X,Y,W,H"):
            _parse_region("1,2,3,4,5")

    def test_zero_width(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            _parse_region("10,20,0,100")

    def test_negative_height(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            _parse_region("10,20,100,-50")

    def test_non_numeric(self) -> None:
        with pytest.raises(ValueError):
            _parse_region("abc,def,ghi,jkl")


# ---------------------------------------------------------------------------
# _generate_output_path
# ---------------------------------------------------------------------------


class TestGenerateOutputPath:
    def test_returns_path_with_correct_extension(self) -> None:
        path = _generate_output_path("png")
        assert path.suffix == ".png"
        assert path.name.startswith("verdiclip_")

    def test_jpg_extension(self) -> None:
        path = _generate_output_path("jpg")
        assert path.suffix == ".jpg"

    def test_path_is_in_cwd(self) -> None:
        path = _generate_output_path("png")
        assert path.parent == Path.cwd()


# ---------------------------------------------------------------------------
# run_cli — capture screen
# ---------------------------------------------------------------------------


class TestCaptureScreen:
    def test_capture_screen_to_file(self, qapp, tmp_path) -> None:
        output = tmp_path / "screen.png"
        parser = build_parser()
        args = parser.parse_args(["capture", "screen", "-o", str(output)])
        result = run_cli(args)
        assert result == 0
        assert output.exists()
        assert output.stat().st_size > 0

    def test_capture_screen_auto_filename(self, qapp, tmp_path) -> None:
        parser = build_parser()
        args = parser.parse_args(["capture", "screen"])
        # Patch CWD to tmp_path
        with patch("verdiclip.cli.Path.cwd", return_value=tmp_path):
            result = run_cli(args)
        assert result == 0
        png_files = list(tmp_path.glob("verdiclip_*.png"))
        assert len(png_files) == 1

    def test_capture_screen_monitor_1(self, qapp, tmp_path) -> None:
        output = tmp_path / "monitor1.png"
        parser = build_parser()
        args = parser.parse_args(["capture", "screen", "--monitor", "1", "-o", str(output)])
        result = run_cli(args)
        assert result == 0
        assert output.exists()

    def test_capture_screen_null_monitor(self, qapp) -> None:
        """Monitor index out of range returns null pixmap → error."""
        from PySide6.QtGui import QPixmap

        parser = build_parser()
        args = parser.parse_args(["capture", "screen", "--monitor", "99"])
        null_pixmap = QPixmap()
        with patch(
            "verdiclip.capture.screen.ScreenCapture.capture_monitor",
            return_value=null_pixmap,
        ):
            result = run_cli(args)
        assert result == 1

    def test_capture_to_clipboard(self, qapp) -> None:
        """--clipboard copies image and returns 0."""
        parser = build_parser()
        args = parser.parse_args(["capture", "screen", "--clipboard"])
        with patch(
            "verdiclip.export.clipboard.ClipboardExporter.copy",
            return_value=True,
        ):
            result = run_cli(args)
        assert result == 0

    def test_capture_to_clipboard_failure(self, qapp) -> None:
        """--clipboard returns 1 when copy fails."""
        parser = build_parser()
        args = parser.parse_args(["capture", "screen", "--clipboard"])
        with patch(
            "verdiclip.export.clipboard.ClipboardExporter.copy",
            return_value=False,
        ):
            result = run_cli(args)
        assert result == 1

    def test_capture_save_failure(self, qapp, tmp_path) -> None:
        """Returns 1 when pixmap.save fails."""
        from PySide6.QtGui import QPixmap

        output = tmp_path / "fail.png"
        parser = build_parser()
        args = parser.parse_args(["capture", "screen", "-o", str(output)])
        mock_pixmap = MagicMock(spec=QPixmap)
        mock_pixmap.isNull.return_value = False
        mock_pixmap.width.return_value = 100
        mock_pixmap.height.return_value = 100
        mock_pixmap.save.return_value = False
        with patch(
            "verdiclip.capture.screen.ScreenCapture.capture_all_monitors",
            return_value=mock_pixmap,
        ):
            result = run_cli(args)
        assert result == 1


# ---------------------------------------------------------------------------
# run_cli — capture region
# ---------------------------------------------------------------------------


class TestCaptureRegion:
    def test_capture_region_to_file(self, qapp, tmp_path) -> None:
        output = tmp_path / "region.png"
        parser = build_parser()
        args = parser.parse_args([
            "capture", "region", "--region", "0,0,200,150", "-o", str(output),
        ])
        result = run_cli(args)
        assert result == 0
        assert output.exists()
        pixmap = QPixmap(str(output))
        assert pixmap.width() == 200
        assert pixmap.height() == 150

    def test_capture_region_missing_flag(self, qapp) -> None:
        parser = build_parser()
        args = parser.parse_args(["capture", "region", "-o", "out.png"])
        result = run_cli(args)
        assert result == 1

    def test_capture_region_bad_format(self, qapp) -> None:
        parser = build_parser()
        args = parser.parse_args(["capture", "region", "--region", "bad", "-o", "out.png"])
        result = run_cli(args)
        assert result == 1


# ---------------------------------------------------------------------------
# run_cli — capture window
# ---------------------------------------------------------------------------


class TestCaptureWindow:
    def test_capture_window_to_file(self, qapp, tmp_path) -> None:
        output = tmp_path / "window.png"
        parser = build_parser()
        args = parser.parse_args(["capture", "window", "-o", str(output)])
        result = run_cli(args)
        assert result == 0
        assert output.exists()


# ---------------------------------------------------------------------------
# run_cli — capture with delay
# ---------------------------------------------------------------------------


class TestCaptureDelay:
    def test_capture_with_delay(self, qapp, tmp_path) -> None:
        output = tmp_path / "delayed.png"
        parser = build_parser()
        args = parser.parse_args([
            "capture", "screen", "-o", str(output), "--delay", "0.1",
        ])
        result = run_cli(args)
        assert result == 0
        assert output.exists()


# ---------------------------------------------------------------------------
# run_cli — formats
# ---------------------------------------------------------------------------


class TestCaptureFormats:
    @pytest.mark.parametrize("fmt", ["png", "jpg", "bmp", "tiff"])
    def test_capture_with_format(self, qapp, tmp_path, fmt) -> None:
        output = tmp_path / f"test.{fmt}"
        parser = build_parser()
        args = parser.parse_args([
            "capture", "screen", "-o", str(output), "--format", fmt,
        ])
        result = run_cli(args)
        assert result == 0
        assert output.exists()

    def test_format_inferred_from_filename(self, qapp, tmp_path) -> None:
        output = tmp_path / "test.bmp"
        parser = build_parser()
        args = parser.parse_args(["capture", "screen", "-o", str(output)])
        result = run_cli(args)
        assert result == 0
        assert output.exists()

    def test_extension_added_when_missing(self, qapp, tmp_path) -> None:
        output = tmp_path / "noext"
        parser = build_parser()
        args = parser.parse_args([
            "capture", "screen", "-o", str(output), "--format", "png",
        ])
        result = run_cli(args)
        assert result == 0
        assert (tmp_path / "noext.png").exists()


# ---------------------------------------------------------------------------
# run_cli — open (non-blocking test)
# ---------------------------------------------------------------------------


class TestOpenSubcommand:
    def test_open_nonexistent_file(self, qapp) -> None:
        parser = build_parser()
        args = parser.parse_args(["open", "does_not_exist.png"])
        result = run_cli(args)
        assert result == 1

    def test_open_invalid_image(self, qapp, tmp_path) -> None:
        bad_file = tmp_path / "bad.png"
        bad_file.write_text("not an image")
        parser = build_parser()
        args = parser.parse_args(["open", str(bad_file)])
        result = run_cli(args)
        assert result == 1

    def test_open_valid_image(self, qapp, tmp_path) -> None:
        """Opens a valid image in editor (mock exec to avoid blocking)."""
        from PySide6.QtGui import QPixmap

        img = tmp_path / "test.png"
        QPixmap(50, 50).save(str(img))
        parser = build_parser()
        args = parser.parse_args(["open", str(img)])
        with (
            patch("verdiclip.editor.canvas.EditorWindow") as mock_ew,
            patch("PySide6.QtWidgets.QApplication.exec", return_value=0),
        ):
            result = run_cli(args)
        assert result == 0
        mock_ew.assert_called_once()
        mock_ew.return_value.show.assert_called_once()


# ---------------------------------------------------------------------------
# run_cli — no subcommand
# ---------------------------------------------------------------------------


class TestNoSubcommand:
    def test_no_args_prints_help(self, qapp, capsys) -> None:
        parser = build_parser()
        args = parser.parse_args([])
        result = run_cli(args)
        assert result == 0
