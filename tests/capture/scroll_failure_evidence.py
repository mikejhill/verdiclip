"""Bounded test-only failure metadata; never changes capture or test deadlines."""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TypeVar

import pytest

from verdiclip.capture.scroll.settling import FrameSettler, SettleStatus


def diagnostic(action: Callable[[], None]) -> None:
    """Optional evidence must not replace any original result or control signal."""
    with suppress(BaseException):  # Only optional evidence, never product work.
        action()


def annotate_failure(
    error: BaseException, receipt: Callable[[], dict[str, object]]
) -> None:
    """Add one fixed-shape receipt without changing the original exception."""
    diagnostic(lambda: error.add_note("SCROLL_FAILURE " + json.dumps(receipt())))


def safe_receipt(receipt: Callable[[], dict[str, object]]) -> dict[str, object]:
    """An unavailable diagnostic snapshot must not affect the owning journal."""
    result: dict[str, object] = {"diagnostic_unavailable": True}

    def retain() -> None:
        nonlocal result
        result = receipt()

    diagnostic(retain)
    return result


def elapsed(started: float, observed_at: object) -> float | None:
    """Use existing relative timestamps only; invalid timing remains unavailable."""
    if (
        not isinstance(observed_at, (float, int))
        or not math.isfinite(started)
        or not math.isfinite(observed_at)
        or observed_at < started
    ):
        return None
    return observed_at - started


@dataclass
class SettlingEvidence:
    """Keep first-generation evidence and one deadline receipt, never frame data."""

    generations: int = 0
    submissions: int = 0
    # A reset can mean changed pixels OR changed semantic at_end, not pixels alone.
    mismatches: int = 0
    exhausted: int = 0
    _last_exhausted_generation: int = -1
    events: list[dict[str, object]] = field(default_factory=list)
    first_exhausted: dict[str, object] | None = None

    def record(
        self, gate: FrameSettler, phase: str, status: object, observed_at: object
    ) -> None:
        """Inspect scalar state only after an existing operation has completed."""
        if phase == "begin":
            self.generations += 1
        elif phase == "submit":
            self.submissions += 1
            if (
                status in (SettleStatus.WAITING, SettleStatus.STABLE)
                and gate._samples > 1
                and gate._consecutive == 1
            ):
                self.mismatches += 1
        exhausted = status is SettleStatus.EXHAUSTED
        if exhausted and self._last_exhausted_generation != self.generations:
            self.exhausted += 1
            self._last_exhausted_generation = self.generations
        # Retain at most eight first-generation transitions and one exhaustion.
        if not (
            (self.generations == 1 and phase != "expire" and len(self.events) < 8)
            or (exhausted and self.first_exhausted is None)
        ):
            return
        receipt: dict[str, object] = {
            "phase": phase,
            "status": status.value if isinstance(status, SettleStatus) else "begun",
            "samples": gate._samples,
            "consecutive": gate._consecutive,
            "elapsed_seconds": elapsed(gate._started, observed_at),
            "last_observed_seconds": elapsed(gate._started, gate._observed),
            "timing_unavailable": elapsed(gate._started, observed_at) is None
            or elapsed(gate._started, gate._observed) is None,
        }
        if self.generations == 1 and phase != "expire" and len(self.events) < 8:
            self.events.append(receipt)
        if exhausted and self.first_exhausted is None:
            self.first_exhausted = receipt

    def snapshot(self) -> dict[str, object]:
        """Return a constant-size metadata receipt for the existing native journal."""
        return {
            "generations": self.generations,
            "submissions": self.submissions,
            "mismatches": self.mismatches,
            "exhausted": self.exhausted,
            "first_generation": self.events,
            "first_exhausted": self.first_exhausted,
        }


# Explicit TypeVar avoids CodeQL's nested PEP 695 annotation-scope false positive.
_Result = TypeVar("_Result")


def observed(  # noqa: UP047 - retain analyzer-compatible nested return typing
    operation: Callable[[], _Result], record: Callable[[_Result], None]
) -> Callable[[], _Result]:
    """Preserve the exact operation result and exceptions; record returns only."""

    def invoke() -> _Result:
        result = operation()
        diagnostic(lambda: record(result))
        return result

    return invoke


def observe_settling(patch: pytest.MonkeyPatch) -> SettlingEvidence:
    """Wrap only existing settling calls; no extra clocks, samples or Qt work."""
    evidence = SettlingEvidence()
    for name in ("begin", "submit", "expire"):
        original = getattr(FrameSettler, name)

        def wrapper(
            gate: FrameSettler,
            *args: object,
            _original: Callable[..., object] = original,
            _phase: str = name,
            **kwargs: object,
        ) -> object:
            return observed(
                lambda: _original(gate, *args, **kwargs),
                lambda result: evidence.record(
                    gate,
                    _phase,
                    result,
                    getattr(args[1], "observed_at", None)
                    if _phase == "submit"
                    else kwargs.get("now"),
                ),
            )()

        patch.setattr(FrameSettler, name, wrapper)
    return evidence
