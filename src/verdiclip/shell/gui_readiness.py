"""Explicit font-resource admission before tray/hotkeys become available.

This moves work into controller startup; it does not remove cold work or prove
that every widget, selected annotation font, DPI or rendering path is prepared.
"""

from __future__ import annotations

import math
import time
from abc import abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from verdiclip.exceptions import AppError

DIGITS = "0123456789"
NANOSECONDS_PER_MS = 1_000_000


class GuiReadinessError(AppError):
    """The required GUI font resources could not be prepared."""


class FontResources(Protocol):
    """Only font metadata/metrics; no widgets, event pumping or settings writes."""

    @abstractmethod
    def family_count(self) -> int:
        """Read the catalog used by the editor's font-family selector."""

    @abstractmethod
    def digit_advance(self) -> float:
        """Resolve numeric text metrics for the current application font."""


def elapsed_ms(start: int, end: int) -> float:
    """Keep monotonic timing scopes explicit rather than inventing cold totals."""
    if type(start) is not int or type(end) is not int or end < start:
        raise GuiReadinessError("GUI preparation clock is not monotonic")
    return (end - start) / NANOSECONDS_PER_MS


@dataclass(frozen=True, slots=True)
class FontPreparation:
    """Completed catalog/metrics preparation; font names are not retained."""

    family_count: int
    digit_advance: float
    elapsed_ms: float

    def __post_init__(self) -> None:
        """Reject unavailable resources and malformed measurement receipts."""
        if type(self.family_count) is not int or self.family_count <= 0:
            raise GuiReadinessError("No GUI font families are available")
        if not math.isfinite(self.digit_advance) or self.digit_advance <= 0:
            raise GuiReadinessError("Default GUI numeric font metrics are unavailable")
        if not math.isfinite(self.elapsed_ms) or self.elapsed_ms < 0:
            raise GuiReadinessError("GUI preparation duration is invalid")


@dataclass(frozen=True, slots=True)
class StartupReadiness:
    """Startup admission interval inside start, with font preparation included.

    controller_start_ms ends inside prepare_and_admit before receipt creation,
    controller property assignment, ready logging and other outer bookkeeping.
    It also excludes controller construction, QApplication creation and imports.
    """

    fonts: FontPreparation
    controller_start_ms: float

    def combined_ms(self, first_capture_ms: float) -> float:
        """Add post-start capture to admission time without double-counting fonts.

        This excludes the outer bookkeeping/logging and earlier startup scopes
        listed on StartupReadiness. Hosted tests must report outer make_app and
        capture timing separately for their actual combined wall-clock cost.
        """
        if not math.isfinite(first_capture_ms) or first_capture_ms < 0:
            raise ValueError("First-capture duration is invalid")
        return self.controller_start_ms + first_capture_ms


def qt_font_resources() -> FontResources:
    """Build the lazy GUI-thread adapter; never call in local pure tests."""
    from PySide6.QtGui import QFontDatabase, QFontMetricsF  # noqa: PLC0415
    from PySide6.QtWidgets import QApplication  # noqa: PLC0415

    app = QApplication.instance()
    if not isinstance(app, QApplication):
        raise GuiReadinessError("GUI font preparation requires QApplication")
    if not app.thread().isCurrentThread():
        raise GuiReadinessError("GUI font preparation requires the application thread")

    class QtFontResources:
        """Resolve only resources directly required by the editor toolbar."""

        def family_count(self) -> int:
            """Enumerate the available font-family catalog without retaining names."""
            return len(QFontDatabase.families())

        def digit_advance(self) -> float:
            """Resolve current default text metrics without constructing a widget."""
            return QFontMetricsF(QApplication.font()).horizontalAdvance(DIGITS)

    return QtFontResources()


def prepare_gui_fonts(
    resources: FontResources | None = None,
    clock: Callable[[], int] = time.perf_counter_ns,
) -> FontPreparation:
    """Complete the two explicit resource operations before declaring readiness."""
    started = clock()
    actual = resources if resources is not None else qt_font_resources()
    families = actual.family_count()
    advance = actual.digit_advance()
    return FontPreparation(families, advance, elapsed_ms(started, clock()))


def prepare_and_admit(
    apply_theme: Callable[[], None],
    prepare: Callable[[], FontPreparation],
    admit: Callable[[], None],
    clock: Callable[[], int] = time.perf_counter_ns,
) -> StartupReadiness:
    """Enforce theme→resources→tray/hotkeys admission, publishing only on success."""
    started = clock()
    apply_theme()
    fonts = prepare()
    admit()
    return StartupReadiness(fonts, elapsed_ms(started, clock()))
