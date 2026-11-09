"""Real owned status disposal at the fixture boundary; no global window cleanup."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Protocol, cast

import pytest
from PySide6.QtTest import QSignalSpy
from pytestqt.qtbot import QtBot
from tests.update.test_service import FakeFeed, Spawns
from tests.ux.test_app_journeys import PYTHONW, AppFactory, make_app

from verdiclip.update.install import Installation, InstallKind
from verdiclip.update.service import UpdateLauncher, Updater

pytestmark = pytest.mark.ux


class FixtureBody(Protocol):
    """The wrapped generator retained by pytest's fixture decorator."""

    @property
    @abstractmethod
    def __wrapped__(self) -> Callable[..., Iterator[AppFactory]]:
        """Return the original generator without invoking pytest's fixture guard."""


@pytest.mark.parametrize("surfaces", ["update", "scrolling", "both"])
def test_app_fixture_completes_owned_status_disposal(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, surfaces: str
) -> None:
    """Teardown cannot return with its status widget awaiting deferred deletion."""
    fixture = cast(FixtureBody, make_app).__wrapped__
    lifetime = fixture(qtbot, tmp_path, monkeypatch)
    factory = next(lifetime)
    try:
        updater = Updater(
            Installation(InstallKind.CHECKOUT, "0.11.0", PYTHONW, tmp_path),
            FakeFeed("0.11.0"),
            UpdateLauncher(tmp_path / "update", Spawns()),
        )
        app = factory(updater=updater)
        if surfaces != "scrolling":
            app.controller.tray.action("update").trigger()
        if surfaces != "update":
            app.controller._scrolling.failed.emit("Unsupported scrolling area")
        statuses = [
            status
            for status in (
                app.controller._update_status,
                app.controller._scroll_status,
            )
            if status is not None
        ]
        assert len(statuses) == (2 if surfaces == "both" else 1)
        destroyed = [QSignalSpy(status.destroyed) for status in statuses]
    finally:
        with pytest.raises(StopIteration):
            next(lifetime)
    assert all(spy.count() == 1 for spy in destroyed), (
        "Fixture teardown returned before status destruction"
    )
