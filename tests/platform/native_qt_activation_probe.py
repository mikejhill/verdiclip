"""Qt-only activation diagnostic; default mode creates no application or window."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PySide6.QtCore import qVersion
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPlainTextEdit


def main() -> None:
    """Reproduce activation independently of VerdiClip, drag and native input APIs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-after-handoff", action="store_true")
    args = parser.parse_args()
    if not args.execute_after_handoff:
        sys.stdout.write(
            "PLAN ONLY: two synthetic Qt text windows; native desktop handoff required\n"
        )
        return
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tests.capture.desktop import DesktopSession  # noqa: PLC0415 - read-only gate

    session = DesktopSession.current() if sys.platform == "win32" else None
    if session is None or session.state != 0 or session.flags != 1:
        raise SystemExit("An unlocked active desktop was not positively verified")
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    first = QPlainTextEdit("Synthetic Qt activation diagnostic A")
    second = QPlainTextEdit("Synthetic Qt activation diagnostic B")
    first.setWindowTitle("Qt activation diagnostic A - test only")
    second.setWindowTitle("Qt activation diagnostic B - test only")
    try:
        first.show()
        second.show()
        app.processEvents()
        first.raise_()
        first.activateWindow()
        QTest.qWait(30)
        app.processEvents()
        sys.stdout.write(
            json.dumps(
                {
                    "qt": qVersion(),
                    "platform": app.platformName(),
                    "first_active": first.isActiveWindow(),
                }
            )
            + "\n"
        )
    finally:
        first.close()
        second.close()
        app.processEvents()
    sys.stdout.write("Qt-only activation fixture completed and cleaned up\n")


if __name__ == "__main__":
    main()
