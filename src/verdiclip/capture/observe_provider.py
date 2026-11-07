"""Unused read-only provider orchestration for one owned-WPF full-panel frame.

No launch/runtime/pipe integration, write API or application caller is present.
The returned immutable RGB bytes remain inside the child; they are not a GUI DTO.
The caller supplies the original two-second absolute deadline and an external
owned-process watchdog because COM/MSS/cleanup calls cannot be interrupted here.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

import mss
from mss.base import MSSBase

from verdiclip.capture.frame_envelope import FrameIdentity
from verdiclip.capture.observe_native import NativeObservation, ObserveBudget
from verdiclip.capture.observe_protocol import (
    Observation,
    ObservationError,
    ObserveTarget,
)
from verdiclip.platform.helper_protocol import MAX_REQUESTS

_SAMPLE_INTERVAL = 0.1
_SAMPLE_COUNT = 3
_QUIET_NS = 200_000_000


@dataclass(frozen=True, slots=True)
class ObservedFrame:
    """Child-only result, admitted after all owned resources have closed."""

    observation: Observation
    rgb: bytes


def _metadata(
    snapshot: dict[str, object], epoch: str, request_id: int, quiet_ns: int
) -> Observation:
    return Observation.parse(
        json.dumps(
            {
                "observation_schema": 1,
                "epoch": epoch,
                "request_id": request_id,
                "frame_id": 1,
                **snapshot,
                "settled": {"samples": _SAMPLE_COUNT, "quiet_ns": quiet_ns},
            },
            allow_nan=False,
        ).encode()
    )


def _capture_sample(
    native: NativeObservation,
    screenshotter: MSSBase,
    baseline: dict[str, object],
    identity: FrameIdentity,
) -> bytes:
    budget = native.budget
    budget.check()
    if native.snapshot() != baseline:
        budget.fail(ObservationError("Fixture guards changed before capture"))
    budget.check()
    shot = screenshotter.grab(
        {
            "left": identity.x,
            "top": identity.y,
            "width": identity.width,
            "height": identity.height,
        }
    )
    budget.check()
    if shot.width != identity.width or shot.height != identity.height:
        budget.fail(ObservationError("Capture dimensions differ from full panel"))
    pixels = shot.rgb
    if type(pixels) is not bytes or len(pixels) != identity.payload_bytes:
        budget.fail(ObservationError("Invalid full-panel RGB payload"))
    if native.snapshot() != baseline:
        budget.fail(ObservationError("Fixture guards changed after capture"))
    budget.check()
    return pixels


def _samples(
    native: NativeObservation,
    screenshotter: MSSBase,
    baseline: dict[str, object],
    identity: FrameIdentity,
) -> tuple[bytes, int]:
    budget = native.budget
    rgb = _capture_sample(native, screenshotter, baseline, identity)
    first_at = budget.last
    for index in range(1, _SAMPLE_COUNT):
        budget.check()
        due = first_at + index * _SAMPLE_INTERVAL
        remaining = due - budget.last
        if remaining > 0:
            time.sleep(remaining)
        budget.check()
        if _capture_sample(native, screenshotter, baseline, identity) != rgb:
            budget.fail(ObservationError("Fixture pixels changed"))
    quiet_ns = int((budget.last - first_at) * 1_000_000_000)
    if quiet_ns < _QUIET_NS:
        budget.fail(
            ObservationError("Samples did not span the required quiet interval")
        )
    return rgb, quiet_ns


def observe_once(  # noqa: PLR0913 - explicit correlation and original budget
    target: ObserveTarget,
    epoch: str,
    request_id: int,
    *,
    deadline: float,
    clock: Callable[[], float] = time.monotonic,
    cancelled: Callable[[], bool],
) -> ObservedFrame:
    """Capture exactly three equal guarded full panels; never recover or retry."""
    budget = ObserveBudget(deadline, clock, cancelled)
    if (
        type(target) is not ObserveTarget
        or type(epoch) is not str
        or re.fullmatch("[0-9a-f]{32}", epoch) is None
        or epoch == "0" * 32
        or type(request_id) is not int
        or not 1 <= request_id < MAX_REQUESTS
    ):
        budget.fail(ObservationError("Invalid owned fixture observation correlation"))
    native = NativeObservation(target, budget)
    try:
        # PMv2 is established and verified before MSS instance construction.
        screenshotter = mss.mss(with_cursor=False)
        native.own(screenshotter.close)
        budget.check()
        baseline = native.snapshot()
        metadata = _metadata(baseline, epoch, request_id, _QUIET_NS)
        rgb, quiet_ns = _samples(
            native, screenshotter, baseline, metadata.frame_identity()
        )
        metadata = _metadata(baseline, epoch, request_id, quiet_ns)
        budget.check()
        result = ObservedFrame(metadata, rgb)
    except BaseException as error:
        native.close(error)
        raise
    else:
        native.close()
        budget.check()
        return result
