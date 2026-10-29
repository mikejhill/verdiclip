"""Read-only hosted status-box identity sampler; importing this never imports Qt."""

from __future__ import annotations

import os
import sys
from abc import abstractmethod
from collections.abc import Mapping, Sequence
from typing import Protocol, cast
from weakref import ReferenceType, ref

PREDECESSOR = (
    "tests/ux/test_app_journeys.py::TestUpdates::"
    "test_numeric_checkout_checks_the_stable_release"
)
MARKER = "manual-update-status"
MAX_TOKENS = 16
MAX_TOP_LEVELS = 256


class Probe:
    """One bounded weak-identity sample, never a fixture-leak attribution oracle."""

    def __init__(self) -> None:
        """Do not query Qt, environment or external resources."""
        self._attempted = False
        self._valid = False
        self._tokens: tuple[ReferenceType[object], ...] = ()

    def register(self, tokens: tuple[object, ...]) -> None:
        """Register exactly once; invalid admission stays unknown and propagates."""
        self._valid = False
        if self._attempted:
            self._tokens = ()
            raise RuntimeError("Status sampling cannot be repeated")
        self._attempted = True
        if type(tokens) is not tuple or len(tokens) > MAX_TOKENS:
            raise ValueError("Invalid bounded status-token sample")
        references: list[ReferenceType[object]] = []
        for token in tokens:
            if token is None:
                raise ValueError("Missing window token in sampled status box")
            if not any(reference() is token for reference in references):
                references.append(ref(token))
        self._tokens = tuple(references)
        self._valid = True

    def match(self, focus: object | None) -> dict[str, object]:
        """Compare only Python identity; never query or format a Qt object."""
        lost = False
        matched = False
        for reference in self._tokens:
            token = reference()
            if token is None:
                lost = True
            elif token is focus:
                matched = True
        answer: bool | None = None
        if self._valid and self._tokens:
            if matched:
                answer = True
            elif focus is None or not lost:
                answer = False
        return {
            "manual_status_sampling_valid": self._valid,
            "manual_status_focus_match": answer,
            "manual_status_sample_empty": self._valid and not self._tokens,
            "manual_status_weak_identity_lost": lost,
            "focus_present": focus is not None,
            "identity_match_only": True,
        }


class ReadOnlyPorts(Protocol):
    """Explicit fakeable Qt query boundary, invoked only by predecessor sampling."""

    @abstractmethod
    def top_levels(self) -> Sequence[object]:
        """Return existing top-level objects without creating any."""

    @abstractmethod
    def is_message_box(self, widget: object) -> bool:
        """Check the known QMessageBox class only."""

    @abstractmethod
    def object_name(self, widget: object) -> str:
        """Invoke QObject's fixed getter, bypassing Python overrides."""

    @abstractmethod
    def window_token(self, widget: object) -> object | None:
        """Invoke QWidget's fixed existing-window getter without winId creation."""


def sample_from_ports(ports: ReadOnlyPorts) -> tuple[object, ...]:
    """Pure orchestration; use fake ports locally, real Qt ports only hosted."""
    widgets = ports.top_levels()
    if len(widgets) > MAX_TOP_LEVELS:
        raise OverflowError("Too many existing top-level widgets to sample")
    tokens: list[object] = []
    for widget in widgets:
        if not ports.is_message_box(widget):
            continue
        name = ports.object_name(widget)
        if type(name) is not str:
            raise TypeError("Non-string status marker")
        if name != MARKER:
            continue
        token = ports.window_token(widget)
        if token is None:
            raise RuntimeError("Marked status box has no existing QWindow")
        tokens.append(token)
        if len(tokens) > MAX_TOKENS:
            raise OverflowError("Too many marked status windows")
    return tuple(tokens)


def admit_host(platform: str, environment: Mapping[str, str]) -> None:
    """Prevent accidental local Qt imports; this is not a security boundary."""
    if (
        platform != "linux"
        or environment.get("GITHUB_ACTIONS") != "true"
        or environment.get("RUNNER_OS") != "Linux"
        or environment.get("QT_QPA_PLATFORM") != "offscreen"
    ):
        raise RuntimeError(
            "Status sampling requires hosted Linux offscreen clean child"
        )


def sample_manual_status_windows() -> tuple[object, ...]:
    """Hosted clean-child ONLY; sample existing marked boxes once after CALL.

    No application/window creation, activation, event processing, object titles,
    raw handles or validity probes. Root must invoke only at PREDECESSOR post-CALL
    before its teardown, immediately register the returned tuple and release it.
    """
    admit_host(sys.platform, os.environ)
    from PySide6.QtCore import QObject  # noqa: PLC0415 - lazy hosted-only query
    from PySide6.QtWidgets import (  # noqa: PLC0415 - never imported in pure checks
        QApplication,
        QMessageBox,
        QWidget,
    )

    if not isinstance(QApplication.instance(), QApplication):
        raise TypeError("No existing QApplication for status sample")

    class QtPorts:
        """Use exact builtin Qt getters, never instance overrides."""

        def top_levels(self) -> Sequence[object]:
            """Read existing widgets without modifying their state."""
            return QApplication.topLevelWidgets()

        def is_message_box(self, widget: object) -> bool:
            """Admit only QMessageBox instances."""
            return isinstance(widget, QMessageBox)

        def object_name(self, widget: object) -> str:
            """Read the exact QObject getter after class admission."""
            if not isinstance(widget, QMessageBox):
                raise TypeError("Status marker getter requires QMessageBox")
            return QObject.objectName(cast("QObject", widget))

        def window_token(self, widget: object) -> object | None:
            """Read the existing QWindow via the fixed QWidget method."""
            if not isinstance(widget, QMessageBox):
                raise TypeError("Status token getter requires QMessageBox")
            return QWidget.windowHandle(cast("QWidget", widget))

    return sample_from_ports(QtPorts())
