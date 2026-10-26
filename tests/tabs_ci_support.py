"""A hardware-free pytest profile used only by the repository tabs CI target."""

from __future__ import annotations

import ctypes
import os
from collections.abc import Iterator
from typing import NoReturn

import mss
import pytest
from hypothesis import settings
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QApplication

settings.register_profile("tabs_ci", database=None, print_blob=True)


def pytest_configure(config: pytest.Config) -> None:
    """Fail before application construction if the target is not simulated."""
    del config
    if os.environ.get("QT_QPA_PLATFORM") != "offscreen":
        raise pytest.UsageError("Tabs CI requires QT_QPA_PLATFORM=offscreen")


def refuse_native(*_arguments: object, **_options: object) -> NoReturn:
    """Trip on accidental desktop/backend use instead of silently depending on it."""
    raise AssertionError("Tabs CI forbids real native DLL, screen or shell access")


def install_simulated_backends(patch: pytest.MonkeyPatch) -> None:
    """Keep backend refusal independently testable without reaching a desktop."""
    patch.setattr(ctypes, "WinDLL", refuse_native, raising=False)
    patch.setattr(mss, "MSS", refuse_native)
    patch.setattr(QDesktopServices, "openUrl", refuse_native)


@pytest.fixture(autouse=True)
def simulated_desktop_only(qapp: QApplication) -> Iterator[None]:
    """Allow explicit test doubles but never load real Python native backends."""
    del qapp
    assert QGuiApplication.platformName() == "offscreen"
    # Own this patch lifetime; consuming the test's monkeypatch fixture would
    # change its teardown order relative to the shared clipboard cleanup.
    with pytest.MonkeyPatch.context() as patch:
        install_simulated_backends(patch)
        yield
