"""The guide that walks the user through choosing VerdiClip as default."""

from __future__ import annotations

import pytest
from pytestqt.qtbot import QtBot

from verdiclip.exceptions import PlatformError
from verdiclip.shell.default_app_dialog import (
    ALL_DONE_TEXT,
    DONE_TEXT,
    PENDING_TEXT,
    UNKNOWN_TEXT,
    DefaultAppDialog,
)

EXTENSIONS = (".png", ".jpg", ".gif")


class Defaults:
    """Stand-in for ``FileAssociations.defaults`` with switchable answers."""

    def __init__(self) -> None:
        """Start with nothing chosen."""
        self.chosen: set[str] = set()
        self.error: PlatformError | None = None

    def __call__(self) -> dict[str, bool]:
        """Report each extension, or raise the injected error."""
        if self.error is not None:
            raise self.error
        return {extension: extension in self.chosen for extension in EXTENSIONS}


@pytest.fixture
def defaults() -> Defaults:
    """Return fresh defaults."""
    return Defaults()


@pytest.fixture
def guide(qtbot: QtBot, defaults: Defaults) -> DefaultAppDialog:
    """Return a guide that polls quickly."""
    dialog = DefaultAppDialog(EXTENSIONS, defaults, poll_ms=10)
    qtbot.addWidget(dialog)
    return dialog


class TestDefaultAppDialog:
    """Steps, live status, and the Settings button."""

    def test_lists_each_type_with_its_status(
        self, qtbot: QtBot, defaults: Defaults
    ) -> None:
        """Types already choosing VerdiClip are ticked; the rest are counted."""
        defaults.chosen = {".png"}
        dialog = DefaultAppDialog(EXTENSIONS, defaults)
        qtbot.addWidget(dialog)

        assert dialog.state_text(".png") == DONE_TEXT
        assert dialog.state_text(".jpg") == PENDING_TEXT
        assert dialog.summary_text() == "2 of 3 to go."
        assert "Set default" in dialog.steps_text()

    def test_follows_choices_made_while_open(
        self, qtbot: QtBot, guide: DefaultAppDialog, defaults: Defaults
    ) -> None:
        """Choosing VerdiClip in Settings ticks the type off without a click."""
        defaults.chosen = set(EXTENSIONS)

        qtbot.waitUntil(lambda: guide.summary_text() == ALL_DONE_TEXT)
        assert guide.state_text(".gif") == DONE_TEXT

    def test_unreadable_defaults_are_shown_as_unknown(
        self, qtbot: QtBot, defaults: Defaults
    ) -> None:
        """A failed lookup is shown, not raised."""
        defaults.error = PlatformError("Could not read the default image apps")
        dialog = DefaultAppDialog(EXTENSIONS, defaults)
        qtbot.addWidget(dialog)

        assert dialog.state_text(".png") == UNKNOWN_TEXT
        assert dialog.summary_text() == "Could not read the default image apps"

    def test_button_asks_to_reopen_settings(
        self, qtbot: QtBot, guide: DefaultAppDialog
    ) -> None:
        """The button reopens Windows Settings through the app."""
        with qtbot.waitSignal(guide.open_settings_requested):
            guide.open_settings_button().click()

    def test_polling_stops_when_closed(self, guide: DefaultAppDialog) -> None:
        """A closed guide stops asking Windows."""
        guide.reject()

        assert not guide._timer.isActive()
