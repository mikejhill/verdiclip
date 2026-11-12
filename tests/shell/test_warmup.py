"""Warm-up builds and discards a throwaway overlay and editor."""

from __future__ import annotations

import logging

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from verdiclip.settings import EditorSettings, OutputSettings
from verdiclip.shell import warmup


def _settle() -> None:
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QCoreApplication.processEvents()


def test_warm_up_leaves_no_window_behind(
    qapp: QApplication, caplog: pytest.LogCaptureFixture
) -> None:
    """Nothing is shown and every throwaway widget is deleted."""
    _settle()
    before = set(map(id, qapp.topLevelWidgets()))
    with caplog.at_level(logging.INFO, logger=warmup.__name__):
        warmup.warm_up(EditorSettings(), OutputSettings())
    _settle()
    assert not [w for w in qapp.topLevelWidgets() if w.isVisible()]
    assert set(map(id, qapp.topLevelWidgets())) <= before
    assert "Warmed up" in caplog.text


def test_failed_warm_up_is_logged_not_raised(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failure only costs speed, so it never escapes."""
    del qapp

    def broken() -> None:
        raise RuntimeError("no screen")

    monkeypatch.setattr(warmup, "_warm_overlay", broken)
    with caplog.at_level(logging.ERROR, logger=warmup.__name__):
        warmup.warm_up(EditorSettings(), OutputSettings())
    assert "Warm-up failed" in caplog.text
