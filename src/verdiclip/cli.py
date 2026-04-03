"""Command-line interface for headless screenshot capture."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from verdiclip import __app_name__, __version__


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="verdiclip",
        description=f"{__app_name__} — A screenshot and annotation tool for Windows.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{__app_name__} {__version__}",
    )

    sub = parser.add_subparsers(dest="command")

    # --- capture subcommand ---
    capture_parser = sub.add_parser(
        "capture",
        help="Take a screenshot from the command line.",
    )
    capture_parser.add_argument(
        "mode",
        choices=["screen", "region", "window"],
        help="Capture mode: 'screen' (full screen), 'region' (x,y,w,h), 'window' (active window).",
    )
    capture_parser.add_argument(
        "-o",
        "--output",
        type=str,
        default=None,
        help="Output file path (e.g., screenshot.png). If omitted, auto-generates in CWD.",
    )
    capture_parser.add_argument(
        "--region",
        type=str,
        default=None,
        metavar="X,Y,W,H",
        help="Region coordinates for 'region' mode (e.g., '100,100,800,600').",
    )
    capture_parser.add_argument(
        "--monitor",
        type=int,
        default=None,
        help="Monitor index for 'screen' mode (1-based). Omit to capture all monitors.",
    )
    capture_parser.add_argument(
        "--format",
        type=str,
        default=None,
        choices=["png", "jpg", "bmp", "tiff"],
        help="Image format (default: inferred from filename, or png).",
    )
    capture_parser.add_argument(
        "--quality",
        type=int,
        default=90,
        help="JPEG quality 1-100 (default: 90).",
    )
    capture_parser.add_argument(
        "--delay",
        type=float,
        default=0,
        help="Delay in seconds before capturing (default: 0).",
    )
    capture_parser.add_argument(
        "--clipboard",
        action="store_true",
        help="Copy the screenshot to clipboard instead of saving to file.",
    )

    # --- open subcommand ---
    open_parser = sub.add_parser(
        "open",
        help="Open an image file in the editor.",
    )
    open_parser.add_argument(
        "file",
        type=str,
        help="Path to the image file to open.",
    )

    return parser


def _generate_output_path(fmt: str) -> Path:
    """Generate a timestamped output path in CWD."""
    from datetime import datetime

    now = datetime.now()
    filename = f"verdiclip_{now.strftime('%Y%m%d_%H%M%S')}.{fmt}"
    return Path.cwd() / filename


def _parse_region(region_str: str) -> tuple[int, int, int, int]:
    """Parse 'X,Y,W,H' into (x, y, width, height)."""
    parts = [int(p.strip()) for p in region_str.split(",")]
    if len(parts) != 4:
        raise ValueError(f"Region must be X,Y,W,H — got {region_str!r}")
    x, y, w, h = parts
    if w <= 0 or h <= 0:
        raise ValueError(f"Region width and height must be positive — got {w}x{h}")
    return x, y, w, h


def run_cli(args: argparse.Namespace) -> int:
    """Execute the CLI command. Returns exit code."""
    if args.command == "capture":
        return _handle_capture(args)
    if args.command == "open":
        return _handle_open(args)
    build_parser().print_help()
    return 0


def _handle_capture(args: argparse.Namespace) -> int:
    """Handle the 'capture' subcommand."""
    import time

    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QApplication

    # Minimal QApplication for QPixmap operations
    app = QApplication.instance() or QApplication(sys.argv)

    if args.delay > 0:
        print(f"Waiting {args.delay}s before capture...")
        time.sleep(args.delay)

    from verdiclip.capture.screen import ScreenCapture

    mode = args.mode
    pixmap = None

    if mode == "screen":
        if args.monitor is not None:
            pixmap = ScreenCapture.capture_monitor(args.monitor)
            if pixmap.isNull():
                print(f"Error: Monitor {args.monitor} not found.", file=sys.stderr)
                return 1
        else:
            pixmap = ScreenCapture.capture_all_monitors()
        print(f"Captured full screen: {pixmap.width()}x{pixmap.height()}")

    elif mode == "region":
        if not args.region:
            print("Error: --region X,Y,W,H required for 'region' mode.", file=sys.stderr)
            return 1
        try:
            x, y, w, h = _parse_region(args.region)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        rect = QRect(x, y, w, h)
        pixmap = ScreenCapture.capture_region(rect)
        print(f"Captured region ({x},{y}) {w}x{h}")

    elif mode == "window":
        from verdiclip.capture.window import WindowCapture

        pixmap = WindowCapture.capture_active_window()
        print(f"Captured active window: {pixmap.width()}x{pixmap.height()}")

    if pixmap is None or pixmap.isNull():
        print("Error: Capture failed.", file=sys.stderr)
        return 1

    # Output
    if args.clipboard:
        from verdiclip.export.clipboard import ClipboardExporter

        if ClipboardExporter.copy(pixmap):
            print("Screenshot copied to clipboard.")
            # Process events so clipboard data persists
            app.processEvents()
            return 0
        print("Error: Failed to copy to clipboard.", file=sys.stderr)
        return 1

    fmt = args.format
    if args.output:
        output_path = Path(args.output)
        if not fmt:
            ext = output_path.suffix.lstrip(".").lower()
            fmt = ext if ext in ("png", "jpg", "bmp", "tiff") else "png"
        if not output_path.suffix:
            output_path = output_path.with_suffix(f".{fmt}")
    else:
        fmt = fmt or "png"
        output_path = _generate_output_path(fmt)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    quality = -1
    fmt_upper = fmt.upper()
    if fmt_upper in ("JPG", "JPEG"):
        quality = args.quality

    success = pixmap.save(str(output_path), fmt_upper, quality)
    if success:
        print(f"Saved: {output_path}")
        return 0
    print(f"Error: Failed to save to {output_path}", file=sys.stderr)
    return 1


def _handle_open(args: argparse.Namespace) -> int:
    """Handle the 'open' subcommand — open image in editor."""
    file_path = Path(args.file)
    if not file_path.exists():
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        return 1

    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)

    pixmap = QPixmap(str(file_path))
    if pixmap.isNull():
        print(f"Error: Cannot load image: {file_path}", file=sys.stderr)
        return 1

    from verdiclip.config import Config
    from verdiclip.editor.window import EditorWindow

    config = Config()
    editor = EditorWindow(pixmap, config)
    editor.show()
    print(f"Opened: {file_path} ({pixmap.width()}x{pixmap.height()})")
    return app.exec()
