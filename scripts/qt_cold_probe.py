"""Separate hosted-only cold constituent probes; never a product latency gate."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from scripts.latency_trace import LatencyTrace

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication

MODES = ("spinbox", "lineedit", "text-layout", "style-hints")


def measure_operation(
    mode: str,
    operations: Mapping[str, Callable[[], object]],
    metadata: Callable[[], dict[str, object]],
    trace: LatencyTrace,
) -> object:
    """Invoke exactly one constituent, retaining its lifetime through metadata."""
    operation = operations[mode]
    with trace.phase(mode):
        retained = operation()
    # Inventory can touch font files; it must never precede the measured path.
    trace.metadata.update(metadata())
    return retained


def font_inventory(directory: str | None) -> dict[str, object]:
    """Match Qt FreeType's direct-directory extensions after measurement only."""
    result: dict[str, object] = {"configured_font_directory": directory}
    if directory is None or directory == "":
        return result
    try:
        files = [
            entry
            for entry in Path(directory).iterdir()
            if entry.suffix.lower() in {".ttf", ".pfa", ".pfb", ".otf"}
            and entry.is_file()
        ]
        result["matching_direct_font_files"] = len(files)
        result["matching_direct_font_bytes"] = sum(p.stat().st_size for p in files)
    except OSError as error:
        result["font_inventory_error"] = type(error).__name__
    return result


def _metadata(app: QApplication) -> dict[str, object]:
    from PySide6.QtCore import qVersion  # noqa: PLC0415 - no Qt before hosted admission

    return {
        "python": sys.version.split()[0],
        "qt": qVersion(),
        "qpa": app.platformName(),
        "style": app.style().objectName(),
        "os_font_cache": "unknown; not reset",
        "process_font_cache": (
            "application constructed; no earlier explicit probe/widget; "
            "caches not reset"
        ),
        "evidence_limit": (
            "Independent constituent probes, not internal C++ phase accounting; "
            "not native Windows latency or foreground/input qualification"
        ),
        **font_inventory(os.environ.get("QT_QPA_FONTDIR")),
    }


def _run_qt(mode: str) -> str:
    # Qt is imported only after the entry point admits a hosted Windows job.
    from PySide6.QtGui import QTextLayout  # noqa: PLC0415 - hosted-only native imports
    from PySide6.QtWidgets import (  # noqa: PLC0415 - hosted-only native imports
        QApplication,
        QLineEdit,
        QSpinBox,
        QStyle,
    )

    trace = LatencyTrace(metadata={"mode": mode, "process_isolated": True})
    with trace.on_failure():
        with trace.phase("application-construction"):
            app = QApplication([])

        def text_layout() -> object:
            layout = QTextLayout("", app.font())
            layout.beginLayout()
            line = layout.createLine()
            layout.endLayout()
            return (layout, line.ascent())

        def style_hints() -> object:
            style = app.style()
            return [
                style.styleHint(hint)
                for hint in (
                    QStyle.StyleHint.SH_SpinBox_ClickAutoRepeatRate,
                    QStyle.StyleHint.SH_SpinBox_ClickAutoRepeatThreshold,
                    QStyle.StyleHint.SH_LineEdit_PasswordCharacter,
                    QStyle.StyleHint.SH_LineEdit_PasswordMaskDelay,
                )
            ]

        operations: dict[str, Callable[[], object]] = {
            "spinbox": QSpinBox,
            "lineedit": QLineEdit,
            "text-layout": text_layout,
            "style-hints": style_hints,
        }
        retained = measure_operation(mode, operations, lambda: _metadata(app), trace)
        # No show, input, clipboard, settings or application code is involved.
        # Owned Qt objects remain alive until all measured phases are finished.
        del retained
        return trace.describe()


def main(argv: list[str] | None = None) -> None:
    """One constituent per new hosted process; local Qt execution is refused."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=MODES)
    args = parser.parse_args(argv)
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_OS") != "Windows"
    ):
        raise SystemExit("This diagnostic is restricted to hosted Windows CI.")
    # The same default as tests/conftest.py, without importing its Qt fixtures.
    fonts = Path("C:/Windows/Fonts")
    if fonts.is_dir():
        os.environ.setdefault("QT_QPA_FONTDIR", str(fonts))
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.stdout.write(_run_qt(args.mode) + "\n")


if __name__ == "__main__":
    main()
