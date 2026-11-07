"""Pure candidate scrolling lifecycle, deliberately not connected to native I/O.

The caller supplies a qualified content rectangle and scroll range, never an
inferred provider rectangle. Native execution must recheck each request's exact
precondition and absolute deadline immediately before writing. The native adapter
also owns its three-second command watchdog. Settled observations are helper-verified
aggregates: three equal pixels/state samples, not merely three arbitrary reads.
The one-pixel allowance and 0.2-second quiet interval are candidate policies,
not a claim of provider-family or mixed-DPI qualification.
"""

import math
from dataclasses import dataclass
from enum import Enum, auto

from verdiclip.capture.scroll_stitch import (
    MAX_FRAMES,
    MAX_OUTPUT_PIXELS,
    MAX_OUTPUT_ROWS,
    ScrollFrame,
    ScrollStitcher,
    StitchedImage,
    StitchError,
)

MAX_SECONDS = 90.0
MIN_QUIET_SECONDS = 0.2
MIN_SAMPLES = 3
PIXEL_TOLERANCE = 1.0
_PERCENT = 100.0
_MAX_INPUT_TICK = 0xFFFFFFFF


@dataclass(frozen=True)
class PhysicalRect:
    """Physical XYWH geometry, explicitly distinct from native LTRB rectangles."""

    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        """Require integral coordinates while preserving negative screen origins."""
        if any(
            type(value) is not int
            for value in (self.x, self.y, self.width, self.height)
        ):
            raise ValueError("Physical rectangle coordinates must be integers")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Physical rectangle must be nonempty")

    @classmethod
    def from_ltrb(cls, left: int, top: int, right: int, bottom: int) -> "PhysicalRect":
        """Convert named native edges once without reinterpreting them as sizes."""
        if any(type(value) is not int for value in (left, top, right, bottom)):
            raise ValueError("Native rectangle edges must be integers")
        return cls(left, top, right - left, bottom - top)


@dataclass(frozen=True)
class QualifiedPanel:
    """Already qualified physical content bounds and continuous scroll metrics."""

    viewport: PhysicalRect
    scroll_range_pixels: float
    metric_basis_height: int
    horizontal_scrollable: bool

    def __post_init__(self) -> None:
        """Reject impossible or over-limit geometry before any native request."""
        if type(self.viewport) is not PhysicalRect:
            raise ValueError("Qualified viewport must be an explicit PhysicalRect")
        if (
            type(self.metric_basis_height) is not int
            or not 0 < self.metric_basis_height <= MAX_OUTPUT_ROWS
        ):
            raise ValueError(
                "Qualified metric basis height must be a bounded positive integer"
            )
        extent = self.viewport.height + self.scroll_range_pixels
        if not math.isfinite(self.scroll_range_pixels) or self.scroll_range_pixels <= 0:
            raise ValueError("Qualified scroll range must be finite and positive")
        if extent > MAX_OUTPUT_ROWS or self.viewport.width * extent > MAX_OUTPUT_PIXELS:
            raise ValueError("Qualified output extent exceeds capture limits")


@dataclass(frozen=True)
class ScrollGuard:
    """Exact native identity, ownership, geometry and provider readback."""

    target: tuple[int, ...]
    foreground: tuple[int, int]
    viewport: PhysicalRect
    display_layout: tuple[int, ...]
    input_epoch: int
    vertical_percent: float
    horizontal_percent: float
    view_percent: float
    visible_unoccluded: bool


@dataclass(frozen=True)
class SettledObservation:
    """Helper-verified identical guarded samples spanning the quiet interval."""

    guard: ScrollGuard
    frame: ScrollFrame
    sample_count: int
    quiet_seconds: float


@dataclass(frozen=True)
class ScrollRequest:
    """One guarded write; preserve horizontal percent, never substitute -1."""

    request_id: int
    vertical_percent: float
    horizontal_percent: float
    precondition: ScrollGuard
    expires_at: float


@dataclass(frozen=True)
class ScrollOutcome:
    """Terminal result; failures never expose partial or unrestored images."""

    image: StitchedImage | None
    failure: str | None
    restored: bool
    position_uncertain: bool


class _Phase(Enum):
    NEW = auto()
    TOP = auto()
    FORWARD = auto()
    ENDPOINT = auto()
    REPLAY = auto()
    RESTORE = auto()
    DONE = auto()


class ScrollPlan:
    """Single-flight state machine with immutable epoch and fail-closed output."""

    def __init__(self, panel: QualifiedPanel) -> None:
        self._panel = panel
        self._phase = _Phase.NEW
        self._initial: SettledObservation | None = None
        self._top: ScrollFrame | None = None
        self._last: SettledObservation | None = None
        self._request: ScrollRequest | None = None
        self._outcome: ScrollOutcome | None = None
        self._stitcher = ScrollStitcher()
        self._frame_count = 0
        self._height = 0
        self._started = 0.0
        self._clock = 0.0
        self._sequence = 0
        self._failure: str | None = None

    def start(
        self, initial: SettledObservation, now: float
    ) -> ScrollRequest | ScrollOutcome:
        """Save settled initial pixels/state and request a guarded seek to top."""
        if self._phase is not _Phase.NEW:
            return self._stop("Session has already started", uncertain=True)
        self._started = now
        self._clock = now
        self._initial = initial
        self._last = initial
        problem = self._check_observation(initial)
        if not math.isfinite(now) or problem is not None:
            return self._stop(problem or "Invalid clock")
        return self._issue(_Phase.TOP, 0.0)

    def accept(
        self, request_id: int, observation: SettledObservation, now: float
    ) -> ScrollRequest | ScrollOutcome:
        """Consume one completed operation with settled guarded readback."""
        stopped = self._check_reply(request_id, now)
        if stopped is not None:
            return stopped
        problem = self._check_observation(observation)
        if problem is not None:
            return self._stop(problem)
        previous = self._last_observation()
        self._last = observation
        self._request = None
        handlers = {
            _Phase.TOP: self._accept_top,
            _Phase.FORWARD: self._accept_forward,
            _Phase.ENDPOINT: self._accept_endpoint,
            _Phase.REPLAY: self._accept_replay,
            _Phase.RESTORE: self._accept_restore,
        }
        return handlers[self._phase](observation, previous)

    def fail(
        self,
        request_id: int,
        reason: str,
        now: float,
        *,
        uncertain: bool,
        current: SettledObservation | None = None,
    ) -> ScrollRequest | ScrollOutcome:
        """Restore only a known failure with fresh unchanged guarded readback."""
        stopped = self._check_reply(request_id, now)
        if stopped is not None:
            return stopped
        if uncertain or current is None:
            return self._stop(reason, uncertain=True)
        if (
            self._check_observation(current) is not None
            or current.guard != self._last_observation().guard
        ):
            return self._stop(reason)
        self._last = current
        self._request = None
        return self._reject(reason)

    def cancel(self) -> ScrollOutcome:
        """Latch cancellation; never restore or issue another write afterward."""
        return self._stop("Capture cancelled", uncertain=self._request is not None)

    def _check_reply(self, request_id: int, now: float) -> ScrollOutcome | None:
        if self._outcome is not None:
            return self._outcome
        if self._request is None or self._request.request_id != request_id:
            return self._stop("Unexpected or stale reply", uncertain=True)
        if (
            not math.isfinite(now)
            or now < self._clock
            or now - self._started >= MAX_SECONDS
        ):
            return self._stop("Capture deadline or clock violation", uncertain=True)
        self._clock = now
        return None

    def _check_observation(self, observation: SettledObservation) -> str | None:
        guard = observation.guard
        initial = self._initial_observation()
        if (
            not math.isfinite(guard.vertical_percent)
            or not 0 <= guard.vertical_percent <= _PERCENT
            or not math.isfinite(guard.view_percent)
            or not 0 < guard.view_percent < _PERCENT
        ):
            return "Invalid vertical metrics"
        if not self._horizontal_valid(guard.horizontal_percent):
            return "Invalid horizontal position"
        if (
            not guard.visible_unoccluded
            or guard.viewport != self._panel.viewport
            or type(guard.input_epoch) is not int
            or not 0 <= guard.input_epoch <= _MAX_INPUT_TICK
            or not self._same_ownership(guard, initial.guard)
        ):
            return "Input, ownership, geometry or provider state changed"
        return self._check_frame(observation)

    def _check_frame(self, observation: SettledObservation) -> str | None:
        guard = observation.guard
        width, height = guard.viewport.width, guard.viewport.height
        if (observation.frame.width, observation.frame.height) != (width, height):
            return "Frame does not match qualified content viewport"
        if (
            type(observation.sample_count) is not int
            or observation.sample_count < MIN_SAMPLES
            or not math.isfinite(observation.quiet_seconds)
            or observation.quiet_seconds < MIN_QUIET_SECONDS
        ):
            return "Frame did not settle"
        # Raw UIA view size uses its separately qualified metric basis, which
        # may include chrome excluded from the captured content rectangle.
        basis = self._panel.metric_basis_height
        implied_range = basis * (_PERCENT / guard.view_percent - 1)
        if abs(implied_range - self._panel.scroll_range_pixels) > PIXEL_TOLERANCE:
            return "View size disagrees with qualified scroll range"
        return None

    @staticmethod
    def _same_ownership(current: ScrollGuard, initial: ScrollGuard) -> bool:
        return (
            current.target == initial.target
            and current.foreground == initial.foreground
            and current.display_layout == initial.display_layout
            and current.input_epoch == initial.input_epoch
            and current.horizontal_percent == initial.horizontal_percent
            and current.view_percent == initial.view_percent
        )

    def _horizontal_valid(self, percent: float) -> bool:
        if self._panel.horizontal_scrollable:
            return math.isfinite(percent) and 0 <= percent <= _PERCENT
        return percent == -1.0

    def _accept_top(
        self, observation: SettledObservation, _previous: SettledObservation
    ) -> ScrollRequest | ScrollOutcome:
        if observation.guard.vertical_percent != 0.0:
            return self._reject("Top was not reached")
        step = self._stitcher.append(observation.frame)
        self._top = observation.frame
        self._height = step.height
        self._frame_count = step.frame_count
        return self._next_forward()

    def _accept_forward(
        self, observation: SettledObservation, previous: SettledObservation
    ) -> ScrollRequest | ScrollOutcome:
        change = observation.guard.vertical_percent - previous.guard.vertical_percent
        if change <= 0:
            return self._reject("Provider did not advance monotonically")
        try:
            step = self._stitcher.append(observation.frame)
        except StitchError as error:
            return self._reject(str(error))
        implied = change * self._panel.scroll_range_pixels / _PERCENT
        if abs(step.new_rows - implied) > PIXEL_TOLERANCE:
            return self._reject("Observed displacement disagrees with provider")
        if implied > observation.frame.height // 2 + PIXEL_TOLERANCE:
            return self._reject("Provider advanced beyond the bounded step")
        self._height = step.height
        self._frame_count = step.frame_count
        return self._next_forward()

    def _accept_endpoint(
        self, observation: SettledObservation, previous: SettledObservation
    ) -> ScrollRequest | ScrollOutcome:
        if observation.guard != previous.guard or observation.frame != previous.frame:
            return self._reject("Endpoint changed after repeated bottom request")
        return self._issue(_Phase.REPLAY, 0.0)

    def _accept_replay(
        self, observation: SettledObservation, _previous: SettledObservation
    ) -> ScrollRequest | ScrollOutcome:
        if observation.guard.vertical_percent != 0.0 or observation.frame != self._top:
            return self._reject("Top replay did not match")
        return self._issue(
            _Phase.RESTORE, self._initial_observation().guard.vertical_percent
        )

    def _accept_restore(
        self, observation: SettledObservation, _previous: SettledObservation
    ) -> ScrollOutcome:
        initial = self._initial_observation()
        if observation.guard != initial.guard or observation.frame != initial.frame:
            return self._stop("Original position or pixels were not restored")
        if self._failure is not None:
            return self._stop(self._failure, restored=True)
        image = self._stitcher.finish()
        self._outcome = ScrollOutcome(
            image, None, restored=True, position_uncertain=False
        )
        self._phase = _Phase.DONE
        return self._outcome

    def _next_forward(self) -> ScrollRequest | ScrollOutcome:
        current = self._last_observation().guard.vertical_percent
        if current == _PERCENT:
            extent = self._panel.viewport.height + self._panel.scroll_range_pixels
            if abs(self._height - extent) > PIXEL_TOLERANCE:
                return self._reject("Output extent disagrees with qualified range")
            return self._issue(_Phase.ENDPOINT, _PERCENT)
        if self._frame_count >= MAX_FRAMES:
            return self._reject("Accepted frame limit reached")
        if (
            self._height >= MAX_OUTPUT_ROWS
            or (self._height + 1) * self._panel.viewport.width > MAX_OUTPUT_PIXELS
        ):
            return self._reject("Output size limit reached")
        current = self._last_observation().guard.vertical_percent
        half_viewport = self._panel.viewport.height // 2
        if half_viewport == 0:
            return self._reject("Viewport is too short for a forward step")
        delta = half_viewport * _PERCENT / self._panel.scroll_range_pixels
        return self._issue(_Phase.FORWARD, min(_PERCENT, current + delta))

    def _reject(self, reason: str) -> ScrollRequest | ScrollOutcome:
        if self._phase is _Phase.RESTORE:
            return self._stop(reason)
        self._failure = reason
        return self._issue(
            _Phase.RESTORE, self._initial_observation().guard.vertical_percent
        )

    def _issue(self, phase: _Phase, vertical: float) -> ScrollRequest:
        self._phase = phase
        self._sequence += 1
        guard = self._last_observation().guard
        self._request = ScrollRequest(
            self._sequence,
            vertical,
            guard.horizontal_percent,
            guard,
            self._started + MAX_SECONDS,
        )
        return self._request

    def _stop(
        self, reason: str, *, restored: bool = False, uncertain: bool = False
    ) -> ScrollOutcome:
        if self._outcome is None:
            self._outcome = ScrollOutcome(None, reason, restored, uncertain)
            self._phase = _Phase.DONE
            self._request = None
        return self._outcome

    def _initial_observation(self) -> SettledObservation:
        if self._initial is None:
            raise RuntimeError("No initial observation")
        return self._initial

    def _last_observation(self) -> SettledObservation:
        if self._last is None:
            raise RuntimeError("No acknowledged observation")
        return self._last
