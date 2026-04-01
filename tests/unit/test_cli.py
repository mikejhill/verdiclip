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
        assert isinstance(parser, argparse.ArgumentParser), (
            f"Expected parser to be instance of argparse.ArgumentParser, got {type(parser)}"
        )

    def test_version_flag(self) -> None:
        parser = build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--version"])
        assert exc_info.value.code == 0, (
            f"Expected exc_info.value.code to equal 0, got {exc_info.value.code}"
        )

    def test_capture_subcommand_parses(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["capture", "screen", "-o", "out.png"])
        assert args.command == "capture", (
            f"Expected args.command to equal 'capture', got {args.command}"
        )
        assert args.mode == "screen", f"Expected args.mode to equal 'screen', got {args.mode}"
        assert args.output == "out.png", (
            f"Expected args.output to equal 'out.png', got {args.output}"
        )

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
        assert args.mode == "region", f"Expected args.mode to equal 'region', got {args.mode}"
        assert args.region == "10,20,300,400", (
            f"Expected args.region to equal '10,20,300,400', got {args.region}"
        )
        assert args.format == "jpg", f"Expected args.format to equal 'jpg', got {args.format}"
        assert args.quality == 80, f"Expected args.quality to equal 80, got {args.quality}"
        assert args.delay == 2.5, f"Expected args.delay to equal 2.5, got {args.delay}"

    def test_capture_clipboard_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["capture", "screen", "--clipboard"])
        assert args.clipboard is True, f"Expected args.clipboard to be True, got {args.clipboard}"

    def test_open_subcommand_parses(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["open", "image.png"])
        assert args.command == "open", f"Expected args.command to equal 'open', got {args.command}"
        assert args.file == "image.png", f"Expected args.file to equal 'image.png', got {args.file}"

    def test_no_subcommand_gives_none(self) -> None:
        parser = build_parser()
        args = parser.parse_args([])
        assert args.command is None, f"Expected args.command to be None, got {args.command}"

    def test_capture_monitor_option(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["capture", "screen", "--monitor", "1"])
        assert args.monitor == 1, f"Expected args.monitor to equal 1, got {args.monitor}"


# ---------------------------------------------------------------------------
# _parse_region
# ---------------------------------------------------------------------------


class TestParseRegion:
    def test_valid_region(self) -> None:
        assert _parse_region("100,200,800,600") == (100, 200, 800, 600), (
            f"Expected _parse_region('100,200,800,600') to equal (100, 200, 800, 600),"
            f" got {_parse_region('100,200,800,600')}"
        )

    def test_with_spaces(self) -> None:
        assert _parse_region(" 10 , 20 , 300 , 400 ") == (10, 20, 300, 400), (
            f"Expected _parse_region(' 10 , 20 , 300 , 400 ') to equal (10, 20, 300, 400),"
            f" got {_parse_region(' 10 , 20 , 300 , 400 ')}"
        )

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
        assert path.suffix == ".png", f"Expected path.suffix to equal '.png', got {path.suffix}"
        assert path.name.startswith("verdiclip_"), (
            "Expected path.name.startswith('verdiclip_') to be truthy"
        )

    def test_jpg_extension(self) -> None:
        path = _generate_output_path("jpg")
        assert path.suffix == ".jpg", f"Expected path.suffix to equal '.jpg', got {path.suffix}"

    def test_path_is_in_cwd(self) -> None:
        path = _generate_output_path("png")
        assert path.parent == Path.cwd(), (
            f"Expected path.parent to equal Path.cwd(), got {path.parent}"
        )


# ---------------------------------------------------------------------------
# run_cli — capture screen
# ---------------------------------------------------------------------------


class TestCaptureScreen:
    def test_capture_screen_to_file(self, qapp, tmp_path) -> None:
        output = tmp_path / "screen.png"
        parser = build_parser()
        args = parser.parse_args(["capture", "screen", "-o", str(output)])
        result = run_cli(args)
        assert result == 0, f"Expected result to equal 0, got {result}"
        assert output.exists(), "Expected output.exists() to be truthy"
        assert output.stat().st_size > 0, (
            f"Expected output.stat().st_size > 0, got {output.stat().st_size}"
        )

    def test_capture_screen_auto_filename(self, qapp, tmp_path) -> None:
        parser = build_parser()
        args = parser.parse_args(["capture", "screen"])
        # Patch CWD to tmp_path
        with patch("verdiclip.cli.Path.cwd", return_value=tmp_path):
            result = run_cli(args)
        assert result == 0, f"Expected result to equal 0, got {result}"
        png_files = list(tmp_path.glob("verdiclip_*.png"))
        assert len(png_files) == 1, f"Expected len(png_files) to equal 1, got {len(png_files)}"

    def test_capture_screen_monitor_1(self, qapp, tmp_path) -> None:
        output = tmp_path / "monitor1.png"
        parser = build_parser()
        args = parser.parse_args(["capture", "screen", "--monitor", "1", "-o", str(output)])
        result = run_cli(args)
        assert result == 0, f"Expected result to equal 0, got {result}"
        assert output.exists(), "Expected output.exists() to be truthy"

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
        assert result == 1, f"Expected result to equal 1, got {result}"

    def test_capture_to_clipboard(self, qapp) -> None:
        """--clipboard copies image and returns 0."""
        parser = build_parser()
        args = parser.parse_args(["capture", "screen", "--clipboard"])
        with patch(
            "verdiclip.export.clipboard.ClipboardExporter.copy",
            return_value=True,
        ):
            result = run_cli(args)
        assert result == 0, f"Expected result to equal 0, got {result}"

    def test_capture_to_clipboard_failure(self, qapp) -> None:
        """--clipboard returns 1 when copy fails."""
        parser = build_parser()
        args = parser.parse_args(["capture", "screen", "--clipboard"])
        with patch(
            "verdiclip.export.clipboard.ClipboardExporter.copy",
            return_value=False,
        ):
            result = run_cli(args)
        assert result == 1, f"Expected result to equal 1, got {result}"

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
        assert result == 1, f"Expected result to equal 1, got {result}"


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
        assert result == 0, f"Expected result to equal 0, got {result}"
        assert output.exists(), "Expected output.exists() to be truthy"
        pixmap = QPixmap(str(output))
        assert pixmap.width() == 200, f"Expected pixmap.width() to equal 200, got {pixmap.width()}"
        assert pixmap.height() == 150, (
            f"Expected pixmap.height() to equal 150, got {pixmap.height()}"
        )

    def test_capture_region_missing_flag(self, qapp) -> None:
        parser = build_parser()
        args = parser.parse_args(["capture", "region", "-o", "out.png"])
        result = run_cli(args)
        assert result == 1, f"Expected result to equal 1, got {result}"

    def test_capture_region_bad_format(self, qapp) -> None:
        parser = build_parser()
        args = parser.parse_args(["capture", "region", "--region", "bad", "-o", "out.png"])
        result = run_cli(args)
        assert result == 1, f"Expected result to equal 1, got {result}"


# ---------------------------------------------------------------------------
# run_cli — capture window
# ---------------------------------------------------------------------------


class TestCaptureWindow:
    def test_capture_window_to_file(self, qapp, tmp_path) -> None:
        output = tmp_path / "window.png"
        parser = build_parser()
        args = parser.parse_args(["capture", "window", "-o", str(output)])
        result = run_cli(args)
        assert result == 0, f"Expected result to equal 0, got {result}"
        assert output.exists(), "Expected output.exists() to be truthy"


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
        assert result == 0, f"Expected result to equal 0, got {result}"
        assert output.exists(), "Expected output.exists() to be truthy"


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
        assert result == 0, f"Expected result to equal 0, got {result}"
        assert output.exists(), "Expected output.exists() to be truthy"

    def test_format_inferred_from_filename(self, qapp, tmp_path) -> None:
        output = tmp_path / "test.bmp"
        parser = build_parser()
        args = parser.parse_args(["capture", "screen", "-o", str(output)])
        result = run_cli(args)
        assert result == 0, f"Expected result to equal 0, got {result}"
        assert output.exists(), "Expected output.exists() to be truthy"

    def test_extension_added_when_missing(self, qapp, tmp_path) -> None:
        output = tmp_path / "noext"
        parser = build_parser()
        args = parser.parse_args([
            "capture", "screen", "-o", str(output), "--format", "png",
        ])
        result = run_cli(args)
        assert result == 0, f"Expected result to equal 0, got {result}"
        assert (tmp_path / "noext.png").exists(), (
            "Expected (tmp_path / 'noext.png').exists() to be truthy"
        )


# ---------------------------------------------------------------------------
# run_cli — open (non-blocking test)
# ---------------------------------------------------------------------------


class TestOpenSubcommand:
    def test_open_nonexistent_file(self, qapp) -> None:
        parser = build_parser()
        args = parser.parse_args(["open", "does_not_exist.png"])
        result = run_cli(args)
        assert result == 1, f"Expected result to equal 1, got {result}"

    def test_open_invalid_image(self, qapp, tmp_path) -> None:
        bad_file = tmp_path / "bad.png"
        bad_file.write_text("not an image")
        parser = build_parser()
        args = parser.parse_args(["open", str(bad_file)])
        result = run_cli(args)
        assert result == 1, f"Expected result to equal 1, got {result}"

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
        assert result == 0, f"Expected result to equal 0, got {result}"
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
        assert result == 0, f"Expected result to equal 0, got {result}"
