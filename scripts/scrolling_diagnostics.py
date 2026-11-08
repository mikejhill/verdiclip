"""Native diagnostic samples with ordinary prose, not colored pixel landmarks.

Run only on the existing disposable hosted Windows desktop. This reuses the
actual UIA helper and exact PNG acquisition. A successful capture is only a
capture/restoration smoke result, not an independent completeness proof.
"""

from __future__ import annotations

import ctypes as c
import ctypes.wintypes as w
import html
import os
import subprocess
import sys
from pathlib import Path
from typing import override
from unittest.mock import patch

from PySide6.QtWidgets import QWidget
from scripts import simple_scroll_app as app
from scripts import simple_scroll_native as native
from scripts.simple_scroll_handoff import Desktop

from verdiclip.capture.scrolling import ScrollResult
from verdiclip.geometry import Point


def document(layout: str) -> str:
    """Lay out project documentation as normal prose with realistic boundaries."""
    source = Path("docs/design/philosophy.md").read_text(encoding="utf-8")
    paragraphs = []
    for block in source.split("\n\n"):
        if not block.strip() or block.startswith(("```", "|", "---")):
            continue
        if block.startswith("#"):
            paragraphs.append(f"<h2>{html.escape(block.lstrip('# ').strip())}</h2>")
        else:
            paragraphs.append(f"<p>{html.escape(block)}</p>")
    article = "<article>" + "".join(paragraphs) + "</article>"
    if layout == "edge-panel":
        article = (
            "<h1>Project design notes</h1><p>Scroll the document panel below.</p>"
            '<section tabindex="0" aria-label="Design document">'
            + article
            + '</section><p class="outside">Related project resources</p>'
        )
    elif layout == "edge-sidebar":
        article += (
            "<aside><h2>On this page</h2><p>Purpose and principles</p>"
            "<p>Architecture</p><p>Testing strategy</p></aside>"
        )
    return (
        '<!doctype html><meta charset="utf-8">'
        "<title>VerdiClip scrolling check</title><style>"
        "body{margin:0;background:#fff;color:#24292f;font:15px/1.45 Arial}"
        "article{max-width:650px;margin:0 auto;padding:24px}"
        "h1,h2{font-weight:600;line-height:1.2;margin:1.6em 0 .7em}"
        "p{margin:0 0 1.1em;white-space:pre-line}"
        "section{height:385.5px;width:740.5px;margin:24px;"
        "border:3px solid #b1b5ba;padding:12px;overflow:scroll}"
        "section article{width:820px;max-width:none;margin:0}"
        "body>h1,body>p{margin-left:24px}.outside{height:800px}"
        "aside{position:fixed;right:15px;top:24px;width:170px;"
        "background:#f6f8fa;padding:12px;font-size:13px}"
        + ("article{margin-left:0;max-width:610px}" if layout == "edge-sidebar" else "")
        + "</style>"
        + article
    )


def _inspect_result(
    result: ScrollResult, output: Path, report: dict[str, object]
) -> None:
    del output
    native._require(result.image is not None, result.message)
    report["oracle"] = "capture/restoration smoke only; inspect exact PNG content"


def _case(kind: str, output: Path, report: dict[str, object]) -> None:
    report["oracle"] = "capture/restoration smoke only; inspect exact PNG content"
    report["gesture_hold_ms"] = 120
    app._case(kind, output, report)


class HumanDesktop(Desktop):
    """Separate press and release with event processing, as physical input does."""

    @override
    def click(
        self, widget: QWidget, point: Point, *, enter: bool = False, hold_ms: int = 120
    ) -> None:
        """Complete a held gesture on the already-verified owned test widget."""
        super().click(widget, point, enter=enter, hold_ms=hold_ms)


def _explorer(
    _kind: str, directory: Path, report: dict[str, object]
) -> subprocess.Popen[bytes]:
    """Open a disposable folder in Explorer; never touch user files or settings."""
    folder = directory / "VerdiClip scrolling check"
    folder.mkdir()
    for number in range(160):
        name = f"Project note {number:03d} - design and testing.txt"
        (folder / name).write_text(
            "Owned scrolling diagnostic input.\n", encoding="utf-8"
        )
    executable = Path(os.environ["WINDIR"]) / "explorer.exe"
    report["executable"] = str(executable)
    native._record_version(executable, report)
    # A separate Explorer process is requested; existing cleanup verifies PID
    # ownership before closing a window and never kills the desktop shell.
    # Explorer parses comma-delimited fields, not conventional argv switches.
    return subprocess.Popen([str(executable), "/separate,", str(folder)])  # noqa: S603


class ExplorerCase:
    """Require an independently owned Explorer process before capture setup."""

    def __init__(self) -> None:
        self.process: subprocess.Popen[bytes] | None = None

    def launch(
        self, kind: str, directory: Path, report: dict[str, object]
    ) -> subprocess.Popen[bytes]:
        """Retain the exact launched process rather than infer ownership by title."""
        self.process = _explorer(kind, directory, report)
        return self.process

    def run(self, kind: str, output: Path, report: dict[str, object]) -> None:
        """Refuse shared-shell targets without closing or killing their windows."""
        try:
            window = native._window()
        except RuntimeError:
            report["explorer_launcher_pid"] = (
                None if self.process is None else self.process.pid
            )
            report["explorer_launcher_exit"] = (
                None if self.process is None else self.process.poll()
            )
            native._desktop_evidence(output, report)
            raise
        user = c.WinDLL("user32", use_last_error=True)
        user.GetWindowThreadProcessId.argtypes = (w.HWND, c.POINTER(w.DWORD))
        user.GetWindowThreadProcessId.restype = w.DWORD
        pid = w.DWORD()
        thread = user.GetWindowThreadProcessId(window.handle, c.byref(pid))
        process = self.process
        report["explorer_window_pid"] = pid.value
        report["explorer_launcher_pid"] = None if process is None else process.pid
        native._require(
            process is not None
            and process.poll() is None
            and thread
            and pid.value == process.pid,
            "Explorer did not create a verified owned separate process; "
            "shared-shell capture setup was not attempted",
        )
        _case(kind, output, report)


def main() -> None:
    """Keep diagnostics separate from the colored-row qualification oracle."""
    kind = sys.argv[1]
    layout = kind.removesuffix("-click").removesuffix("-enter")
    native._require(
        layout in ("edge-prose", "edge-panel", "edge-sidebar", "explorer")
        and kind.endswith(("-click", "-enter")),
        "Unknown representative document layout",
    )
    explorer = ExplorerCase()
    with (
        patch.object(native, "_document", lambda **_kwargs: document(layout)),
        patch.object(app, "_verify_edge_rows", _inspect_result),
        patch.object(app, "Desktop", HumanDesktop),
        patch.object(
            native,
            "_launch",
            explorer.launch if layout == "explorer" else native._launch,
        ),
    ):
        native.main(gui=True, case=explorer.run if layout == "explorer" else _case)


if __name__ == "__main__":
    main()
