"""Hosted-only Qt stderr tee; preserve pytest-qt capture and event-drain behavior."""

from __future__ import annotations

import sys
from collections.abc import Callable, Generator, Iterator, Sequence
from contextlib import contextmanager
from importlib.metadata import version
from typing import TextIO, cast

QtHandler = Callable[[object, object, str], None]
InstallHandler = Callable[[QtHandler | None], QtHandler | None]


@contextmanager
def tee_qt_messages(install: InstallHandler, destination: TextIO) -> Iterator[None]:
    """Flush each message before forwarding it to the exact previous handler."""
    previous: QtHandler | None = None

    def handle(kind: object, context: object, message: str) -> None:
        try:
            destination.write(f"[IPC Qt {kind}] {message}\n")
            destination.flush()
        finally:
            if previous is not None:
                previous(kind, context, message)

    previous = install(handle)
    try:
        yield
    finally:
        install(previous)


def main(arguments: Sequence[str]) -> int:
    """Run pytest once, teeing test calls and unchanged pytest-qt event drains."""
    # Imports are deliberately deferred: pure forwarding tests never load Qt.
    import pytest  # noqa: PLC0415 - pure forwarding tests must never import Qt
    from PySide6 import QtCore  # noqa: PLC0415 - hosted execution only
    from pytestqt import plugin as qt_plugin  # noqa: PLC0415 - hosted execution only

    if version("pytest-qt") != "4.5.0":
        raise RuntimeError(
            "Re-review the private event-drain adapter after pytest-qt changes"
        )
    install = cast("InstallHandler", QtCore.qInstallMessageHandler)
    destination = sys.__stderr__ or sys.stderr
    original_process_events = qt_plugin._process_events

    def process_events() -> None:
        with tee_qt_messages(install, destination):
            original_process_events()

    class MessageTee:
        """Preserve the previously installed pytest-qt handler during each call."""

        @pytest.hookimpl(wrapper=True, trylast=True)
        def pytest_runtest_call(self) -> Generator[None, object, object]:
            """Forward all original hook results and exceptions unchanged."""
            with tee_qt_messages(install, destination):
                return (yield)

    qt_plugin._process_events = process_events
    try:
        return int(pytest.main(list(arguments), plugins=[MessageTee()]))
    finally:
        qt_plugin._process_events = original_process_events


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
