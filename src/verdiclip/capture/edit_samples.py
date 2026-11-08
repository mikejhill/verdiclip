"""Fixed owned EDIT sampling and exact crop, without fixture or GUI dispatch."""

from __future__ import annotations

import time
from typing import Any


def stable_samples(
    read: Any,  # noqa: ANN401 - ctypes and detached diagnostic JSON boundary
    check: Any,  # noqa: ANN401 - ctypes and detached diagnostic JSON boundary
    deadline: float,
    clock: Any = time.monotonic,  # noqa: ANN401 - ctypes and detached diagnostic JSON boundary
    sleep: Any = time.sleep,  # noqa: ANN401 - ctypes and detached diagnostic JSON boundary
) -> Any:  # noqa: ANN401 - ctypes and detached diagnostic JSON boundary
    """Exactly three equal samples spanning200ms, no settling/reset loop."""
    first = None
    first_finished = None
    last_started = None
    for index in range(3):
        if index:
            sleep(0.1)
        check()
        if clock() >= deadline:
            raise RuntimeError("Capture readiness deadline")
        last_started = clock()
        value = read()
        observed = clock()
        if index == 0:
            first_finished = observed
        check()
        if clock() >= deadline:
            raise RuntimeError("Capture readiness deadline")
        if index and value != first:
            raise ValueError("Readiness sample changed; no reset")
        first = value
    if (
        first_finished is None
        or last_started is None
        or last_started - first_finished < 0.2  # noqa: PLR2004 - fixed SDK ABI values and frozen experiment limits
    ):
        raise ValueError("Insufficient readiness span")
    return first


def crop(rgb: bytes, outer: list[int], inner: list[int]) -> bytes:
    """Exact integral pixel slice preserving every client margin pixel."""
    width = outer[2] - outer[0]
    if not (
        outer[0] <= inner[0] < inner[2] <= outer[2]
        and outer[1] <= inner[1] < inner[3] <= outer[3]
    ):
        raise ValueError("Client outside reference")
    if len(rgb) != width * (outer[3] - outer[1]) * 3:
        raise ValueError("Root RGB length mismatch")
    return b"".join(
        rgb[
            ((y - outer[1]) * width + inner[0] - outer[0]) * 3 : (
                (y - outer[1]) * width + inner[2] - outer[0]
            )
            * 3
        ]
        for y in range(inner[1], inner[3])
    )
