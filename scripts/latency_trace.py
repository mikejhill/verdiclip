"""Small Qt-independent failure diagnostics for latency journeys."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class LatencyTrace:
    """Record inclusive phase clocks; never infer a cause from CPU/wall ratios."""

    metadata: dict[str, Any] = field(default_factory=dict)
    wall: Callable[[], int] = time.perf_counter_ns
    process: Callable[[], int] = time.process_time_ns
    thread: Callable[[], int] = time.thread_time_ns
    phases: list[dict[str, Any]] = field(default_factory=list)

    def wrap(
        self, name: str, operation: Callable[..., object]
    ) -> Callable[..., object]:
        """Forward a dynamically patched operation unchanged through one phase."""

        def timed(*args: object, **kwargs: object) -> object:
            with self.phase(name):
                return operation(*args, **kwargs)

        return timed

    @contextmanager
    def phase(self, name: str) -> Generator[None]:
        """Retain timing even on exceptions, without suppressing the exception."""
        start = (self.wall(), self.process(), self.thread())
        completed = False
        failure: BaseException | None = None
        try:
            yield
            completed = True
        except BaseException as error:
            failure = error
            raise
        finally:
            try:
                end = (self.wall(), self.process(), self.thread())
                self.phases.append(
                    {
                        "name": name,
                        "completed": completed,
                        "wall_ms": (end[0] - start[0]) / 1_000_000,
                        "process_cpu_ms": (end[1] - start[1]) / 1_000_000,
                        "thread_cpu_ms": (end[2] - start[2]) / 1_000_000,
                    }
                )
            except Exception as diagnostic_error:
                if failure is None:
                    raise
                kind = type(diagnostic_error).__name__
                failure.add_note(f"Latency phase clocks unavailable: {kind}")

    def describe(self) -> str:
        """Serialize only when a failing assertion requests its message."""
        return json.dumps(
            {
                "metadata": self.metadata,
                "phases": self.phases,
                "interpretation": (
                    "Inclusive nested phases overlap; low CPU does not distinguish "
                    "native blocking from descheduling. Cold and warm are separate "
                    "product cases."
                ),
            },
            sort_keys=True,
        )

    @contextmanager
    def on_failure(self) -> Generator[None]:
        """Attach diagnostics only on failure and preserve the original exception."""
        try:
            yield
        except BaseException as error:
            try:
                note = self.describe()
            except Exception as diagnostic_error:  # noqa: BLE001 - diagnostics are secondary
                kind = type(diagnostic_error).__name__
                note = (
                    f"Latency diagnostics unavailable: {kind}; "
                    "original failure retained."
                )
            error.add_note(note)
            raise
