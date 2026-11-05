"""CPU evidence is optional and never changes the measured operation."""

from __future__ import annotations

import pytest
from tests.ux.test_budgets import cpu_elapsed_ms, gui_cpu_time


def test_cpu_sample_uses_the_current_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retain the real clock value without replacing it with process CPU time."""
    monkeypatch.setattr("tests.ux.test_budgets.time.thread_time", lambda: 1.25)
    assert gui_cpu_time() == 1.25


@pytest.mark.parametrize("error_type", [AttributeError, OSError])
def test_unavailable_cpu_clock_does_not_interrupt_operation(
    monkeypatch: pytest.MonkeyPatch, error_type: type[Exception]
) -> None:
    """A missing API or failed native clock becomes unavailable evidence."""

    def unavailable() -> float:
        raise error_type("CPU clock unavailable")

    monkeypatch.setattr("tests.ux.test_budgets.time.thread_time", unavailable)
    assert gui_cpu_time() is None


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [(None, None, None), (None, 1.0, None), (1.0, None, None), (1.0, 1.125, 125.0)],
)
def test_cpu_phase_requires_both_samples(
    start: float | None, end: float | None, expected: float | None
) -> None:
    """Unavailable CPU evidence cannot masquerade as a zero-duration phase."""
    assert cpu_elapsed_ms(start, end) == expected
