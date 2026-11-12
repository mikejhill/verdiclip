"""Scripted stand-in for the scrolling helper child (see test_scroll_helper)."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from PySide6.QtGui import QImage


def main() -> None:
    """Behave as ``SCROLL_CHILD`` says, using the real argument layout."""
    _x, _y, directory = sys.argv[1:]
    mode = os.environ.get("SCROLL_CHILD", "success")
    if mode == "hang":
        time.sleep(30)
    if mode == "crash":
        sys.exit(3)
    if mode == "garbage":
        sys.stdout.write("not json\n")
        return
    if mode == "wait-cancel":
        sys.stdin.readline()
    image = QImage(10, 30, QImage.Format.Format_RGB32)
    image.fill(0xFF112233)
    image.save(str(Path(directory) / "capture.png"))
    result = {"success": True, "message": "", "warning": "note", "frames": 3}
    if mode == "failure":
        result = {"success": False, "message": "no", "warning": "", "frames": 0}
    sys.stdout.write(json.dumps(result) + "\n")


if __name__ == "__main__":
    main()
