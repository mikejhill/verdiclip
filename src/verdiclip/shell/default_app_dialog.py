"""Walk the user through choosing VerdiClip as the default image app."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Final

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from verdiclip import APP_NAME
from verdiclip.exceptions import AppError

logger = logging.getLogger(__name__)

POLL_MS: Final = 1000
MIN_WIDTH: Final = 420
DONE_TEXT: Final = "✓ VerdiClip"
PENDING_TEXT: Final = "Another app"
UNKNOWN_TEXT: Final = "Unknown"
STEPS: Final = (
    f"Windows only lets you pick default apps yourself, so {APP_NAME} opened "
    f"its page in Windows Settings (Apps > Default apps > {APP_NAME}).<ol>"
    "<li>In Settings, click a file type below that isn't ticked yet.</li>"
    f"<li>In the list that opens, choose <b>{APP_NAME}</b>, "
    "then click <b>Set default</b>.</li>"
    "<li>Repeat for each type. This list ticks them off as you go.</li></ol>"
)
ALL_DONE_TEXT: Final = f"{APP_NAME} now opens all of these image types."


class DefaultAppDialog(QDialog):
    """List each image type's default and refresh it while Settings is open.

    ``read_defaults`` returns, per extension in ``extensions``, whether
    VerdiClip is its default.
    """

    open_settings_requested = Signal()

    def __init__(
        self,
        extensions: Sequence[str],
        read_defaults: Callable[[], Mapping[str, bool]],
        parent: QWidget | None = None,
        *,
        poll_ms: int = POLL_MS,
    ) -> None:
        super().__init__(parent)
        self._read_defaults = read_defaults
        self.setWindowTitle(f"Make {APP_NAME} the default image app")
        self.setMinimumWidth(MIN_WIDTH)
        self._steps = QLabel(STEPS)
        self._steps.setWordWrap(True)
        self._summary = QLabel()
        self._states: dict[str, QLabel] = {}
        self._open_settings = QPushButton("Open Windows Settings again")
        self._open_settings.clicked.connect(self.open_settings_requested)
        self._buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self._buttons.rejected.connect(self.reject)
        self._timer = QTimer(self)
        self._timer.setInterval(poll_ms)
        self._timer.timeout.connect(self.refresh)
        self.finished.connect(self._timer.stop)
        self._build(extensions)
        self.refresh()
        self._timer.start()

    def state_text(self, extension: str) -> str:
        """Return the status shown for ``extension`` (for tests)."""
        return self._states[extension].text()

    def steps_text(self) -> str:
        """Return the instructions (for tests)."""
        return self._steps.text()

    def summary_text(self) -> str:
        """Return the line under the list (for tests)."""
        return self._summary.text()

    def open_settings_button(self) -> QPushButton:
        """Return the button that reopens Settings (for tests)."""
        return self._open_settings

    def refresh(self) -> None:
        """Re-read each type's default and update the list."""
        try:
            defaults = dict(self._read_defaults())
        except AppError as err:
            logger.warning("Could not read default apps: %s", err)
            for label in self._states.values():
                label.setText(UNKNOWN_TEXT)
            self._summary.setText(str(err))
            return
        done = {extension for extension, chosen in defaults.items() if chosen}
        for extension, label in self._states.items():
            label.setText(DONE_TEXT if extension in done else PENDING_TEXT)
        remaining = [e for e in self._states if e not in done]
        if remaining:
            self._summary.setText(f"{len(remaining)} of {len(self._states)} to go.")
        else:
            self._summary.setText(ALL_DONE_TEXT)

    def _build(self, extensions: Sequence[str]) -> None:
        """Lay out the steps, one row per extension, and the buttons."""
        grid = QGridLayout()
        for row, extension in enumerate(extensions):
            state = QLabel()
            self._states[extension] = state
            grid.addWidget(QLabel(extension), row, 0)
            grid.addWidget(state, row, 1)
        layout = QVBoxLayout(self)
        layout.addWidget(self._steps)
        layout.addLayout(grid)
        layout.addWidget(self._summary)
        layout.addWidget(self._open_settings)
        layout.addWidget(self._buttons)
