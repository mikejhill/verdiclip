"""Shared bounded fake-provider retirement wait; never suppress test exceptions."""

from pytestqt.qtbot import QtBot

from verdiclip.capture.scroll.uia_process import UiaProcess


def wait_idle(qtbot: QtBot, process: UiaProcess, *, timeout: int = 5000) -> None:
    """Keep the callback's return outside callers' finally blocks."""
    qtbot.waitUntil(lambda: not process.busy, timeout=timeout)
