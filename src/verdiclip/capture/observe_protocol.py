"""Pure one-frame owned-WPF proof schema; unused by native/application paths.

Capture bounds are the complete selected panel, including borders and scrollbars.
Visibility fields describe limited checks, not universal occlusion knowledge.
Canonical digests describe validated values, not original JSON lexical spelling.
The pairing owner retains no pixels and cannot authenticate native peers or EOF;
its caller must supply those events from the separately verified transport.
DPI 96 here is metadata validation only: PMv2 establishment, native guard reads
and the truth of provider evidence require separate native qualification.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Never, cast

from verdiclip.capture.frame_envelope import (
    HEADER_BYTES,
    FrameHeader,
    FrameIdentity,
    FrameStream,
)
from verdiclip.platform.helper_protocol import MAX_REQUESTS, decode

MAX_TARGET_BYTES = 1024
MAX_OBSERVATION_BYTES = 8192
MAX_WIDTH = 1024
MAX_HEIGHT = 512
MAX_READ_BYTES = 65536
OBSERVE_SECONDS = 2.0
_INT32_MIN = -(2**31)
_INT32_MAX = 2**31 - 1
_UINT32_MAX = 2**32 - 1
_UINT64_MAX = 2**64 - 1
_POINT_DIMENSIONS = 2
_MAX_MONITORS = 8
_MAX_RUNTIME_ID = 32
_MIN_QUIET_NS = 200_000_000
_MAX_QUIET_NS = 2_000_000_000


class ObservationError(ValueError):
    """Invalid proof metadata or a permanently failed one-shot operation."""


def _bad(reason: str) -> Never:
    raise ObservationError(reason)


def _integer(value: object, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        _bad("Invalid bounded integer")
    return value


def _object(value: object, names: str) -> dict[str, object]:
    if type(value) is not dict or set(value) != set(names.split()):
        _bad("Invalid object fields")
    return cast("dict[str, object]", value)


def _array(value: object, low: int, high: int) -> list[object]:
    if type(value) is not list or not low <= len(value) <= high:
        _bad("Invalid bounded array")
    return cast("list[object]", value)


def _constant(value: object, expected: object) -> None:
    if type(value) is not type(expected) or value != expected:
        _bad("Invalid fixed proof value")


def _epoch(value: object) -> str:
    if (
        type(value) is not str
        or re.fullmatch("[0-9a-f]{32}", value) is None
        or value == "0" * 32
    ):
        _bad("Invalid session epoch")
    return value


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _parse(data: bytes, limit: int) -> dict[str, object]:
    if type(data) is not bytes or not 0 < len(data) <= limit:
        _bad("Metadata byte bound exceeded")
    # Reuse strict duplicate, nonfinite, nesting and node-count validation.
    return decode(
        b'{"version":1,"kind":"request","request_id":1,"payload":' + data + b"}"
    ).payload


def _rect(value: object) -> tuple[int, int, int, int]:
    parts = [_integer(v, _INT32_MIN, _INT32_MAX) for v in _array(value, 4, 4)]
    left, top, right, bottom = parts
    if left >= right or top >= bottom:
        _bad("Empty physical rectangle")
    return left, top, right, bottom


def _contains(outer: tuple[int, ...], inner: tuple[int, ...]) -> bool:
    return (
        outer[0] <= inner[0] < inner[2] <= outer[2]
        and outer[1] <= inner[1] < inner[3] <= outer[3]
    )


@dataclass(frozen=True, slots=True)
class ObserveTarget:
    """Pinned fixture correlation, not authorization for arbitrary applications."""

    pid: int
    process_created_100ns: int
    hwnd: int
    point: tuple[int, int]

    def __post_init__(self) -> None:
        """Reject mutable/ambiguous native identity values."""
        _integer(self.pid, 1, _UINT32_MAX)
        _integer(self.process_created_100ns, 1, _UINT64_MAX)
        _integer(self.hwnd, 1, _UINT64_MAX)
        if type(self.point) is not tuple or len(self.point) != _POINT_DIMENSIONS:
            _bad("Invalid immutable point")
        for coordinate in self.point:
            _integer(coordinate, _INT32_MIN, _INT32_MAX)

    def value(self) -> dict[str, object]:
        """Return a detached JSON value; mutating it cannot alter this identity."""
        return {
            "pid": self.pid,
            "process_created_100ns": self.process_created_100ns,
            "hwnd": self.hwnd,
            "point": list(self.point),
        }

    def serialize(self) -> bytes:
        """Encode the fixed observe request with no command/path selectors."""
        return _canonical(
            {"operation": "observe", "target_schema": 1, "target": self.value()}
        )

    @classmethod
    def parse(cls, data: bytes) -> ObserveTarget:
        """Validate bounded request bytes independently of the outer wire ID."""
        value = _object(
            _parse(data, MAX_TARGET_BYTES), "operation target_schema target"
        )
        _constant(value["operation"], "observe")
        _constant(value["target_schema"], 1)
        return _target(value["target"])


def _target(value: object) -> ObserveTarget:
    fields = _object(value, "pid process_created_100ns hwnd point")
    point = _array(fields["point"], 2, 2)
    return ObserveTarget(
        _integer(fields["pid"], 1, _UINT32_MAX),
        _integer(fields["process_created_100ns"], 1, _UINT64_MAX),
        _integer(fields["hwnd"], 1, _UINT64_MAX),
        (
            _integer(point[0], _INT32_MIN, _INT32_MAX),
            _integer(point[1], _INT32_MIN, _INT32_MAX),
        ),
    )


def _visibility(value: object) -> None:
    expected = {
        "scope": "owned-wpf-fixture-external-check-v1",
        "root_visible": True,
        "root_minimized": False,
        "root_cloaked": False,
        "external_intersection_count": 0,
        "point_root_checks": 6,
        "internal_overlay_assessment": "fixture-assumption-only",
    }
    fields = _object(value, " ".join(expected))
    for key, constant in expected.items():
        _constant(fields[key], constant)


def _monitors(value: object, capture: tuple[int, ...]) -> None:
    entries = _array(value, 1, _MAX_MONITORS)
    previous = 0
    containing = 0
    primary = 0
    for entry in entries:
        parts = _array(entry, 6, 6)
        handle = _integer(parts[0], 1, _UINT64_MAX)
        if handle <= previous:
            _bad("Monitor handles must be sorted and unique")
        previous = handle
        rect = _rect(parts[1:5])
        if type(parts[5]) is not bool:
            _bad("Invalid monitor primary flag")
        primary += int(parts[5])
        containing += int(_contains(rect, capture))
    if primary != 1 or containing != 1:
        _bad("Ambiguous monitor containment or primary")


def _guard(value: object, target: ObserveTarget) -> None:
    fields = _object(
        value,
        "foreground_hwnd foreground_pid input_tick root_client_rect target_rect "
        "capture_rect dpi monitors visibility_evidence",
    )
    _constant(fields["foreground_hwnd"], target.hwnd)
    _constant(fields["foreground_pid"], target.pid)
    _integer(fields["input_tick"], 0, _UINT32_MAX)
    root = _rect(fields["root_client_rect"])
    outer = _rect(fields["target_rect"])
    capture = _rect(fields["capture_rect"])
    if capture != outer or not _contains(root, capture):
        _bad("Capture must preserve the entire panel within root client bounds")
    if capture[2] - capture[0] > MAX_WIDTH or capture[3] - capture[1] > MAX_HEIGHT:
        _bad("Full panel exceeds one-observe bounds")
    if not (
        capture[0] <= target.point[0] < capture[2]
        and capture[1] <= target.point[1] < capture[3]
    ):
        _bad("Selected point is outside full panel")
    for dpi in _array(fields["dpi"], 2, 2):
        _constant(dpi, 96)
    _monitors(fields["monitors"], capture)
    _visibility(fields["visibility_evidence"])


def _percent(value: object, low: float, high: float) -> float:
    if (
        type(value) is not int and type(value) is not float
    ) or not low <= value <= high:
        _bad("Invalid finite percentage")
    return 0.0 if value == 0 else float(value)


def _scroll(value: object) -> dict[str, object]:
    fields = _object(
        value,
        "horizontal vertical horizontal_view vertical_view "
        "horizontal_scrollable vertical_scrollable",
    )
    _constant(fields["horizontal_scrollable"], expected=False)
    _constant(fields["vertical_scrollable"], expected=True)
    horizontal = _percent(fields["horizontal"], -1.0, 100.0)
    if -1.0 < horizontal < 0.0:
        _bad("Invalid no-scroll sentinel")
    normalized = {
        **fields,
        "horizontal": horizontal,
        "vertical": _percent(fields["vertical"], 0.0, 100.0),
        "horizontal_view": _percent(fields["horizontal_view"], 0.0, 100.0),
        "vertical_view": _percent(fields["vertical_view"], 0.0, 100.0),
    }
    if normalized["horizontal_view"] == 0 or normalized["vertical_view"] == 0:
        _bad("View percentages must be positive")
    return normalized


def _observation(value: object) -> dict[str, object]:
    fields = _object(
        value,
        "observation_schema epoch request_id frame_id target guard scroll settled",
    )
    _constant(fields["observation_schema"], 1)
    _epoch(fields["epoch"])
    _integer(fields["request_id"], 1, MAX_REQUESTS - 1)
    _constant(fields["frame_id"], 1)
    identity = _object(
        fields["target"], "pid process_created_100ns hwnd point runtime_id framework"
    )
    target = _target(
        {k: v for k, v in identity.items() if k not in {"runtime_id", "framework"}}
    )
    _constant(identity["framework"], "WPF")
    for entry in _array(identity["runtime_id"], 1, _MAX_RUNTIME_ID):
        _integer(entry, _INT32_MIN, _INT32_MAX)
    _guard(fields["guard"], target)
    settled = _object(fields["settled"], "samples quiet_ns")
    _constant(settled["samples"], 3)
    _integer(settled["quiet_ns"], _MIN_QUIET_NS, _MAX_QUIET_NS)
    return {**fields, "scroll": _scroll(fields["scroll"])}


@dataclass(frozen=True, slots=True)
class Observation:
    """Deeply immutable canonical value, containing no image pixels."""

    canonical: bytes

    def __post_init__(self) -> None:
        """Direct construction must contain the validated canonical representation."""
        normalized = _canonical(
            _observation(_parse(self.canonical, MAX_OBSERVATION_BYTES))
        )
        if self.canonical != normalized:
            _bad("Use Observation.parse to normalize metadata values")

    @classmethod
    def parse(cls, data: bytes) -> Observation:
        """Accept equivalent lexical forms, then freeze the normalized value."""
        return cls(_canonical(_observation(_parse(data, MAX_OBSERVATION_BYTES))))

    @property
    def digest(self) -> bytes:
        """Hash the canonical validated value, not the outer control envelope."""
        return hashlib.sha256(self.canonical).digest()

    @property
    def target(self) -> ObserveTarget:
        """Recover a detached immutable target for request correlation."""
        identity = json.loads(self.canonical)["target"]
        return _target(
            {k: v for k, v in identity.items() if k not in {"runtime_id", "framework"}}
        )

    def frame_identity(self) -> FrameIdentity:
        """Bind the existing envelope to complete outer panel geometry."""
        value = json.loads(self.canonical)
        left, top, right, bottom = value["guard"]["capture_rect"]
        return FrameIdentity(
            bytes.fromhex(value["epoch"]),
            value["request_id"],
            1,
            left,
            top,
            right - left,
            bottom - top,
            self.digest,
        )


class ObservePairing:
    """One-shot metadata/header/body/EOF verification, with no pixel retention.

    Construct before request serialization/write or supply its original deadline.
    No progress extends the fixed
    two-second deadline. EOF is a trusted transport event, never inferred from
    payload length. Caller must backpressure body reads until payload_ready.
    """

    def __init__(  # noqa: PLR0913 - explicit original deadline and clock contract
        self,
        target: ObserveTarget,
        epoch: str,
        request_id: int,
        *,
        clock: Callable[[], float],
        cancelled: Callable[[], bool],
        deadline: float | None = None,
    ) -> None:
        if type(target) is not ObserveTarget:
            _bad("Expected immutable target")
        self._target = target
        self._epoch = _epoch(epoch)
        self._request_id = _integer(request_id, 1, MAX_REQUESTS - 1)
        self._clock, self._cancelled = clock, cancelled
        self._last_time = self._time()
        if deadline is not None and (
            (type(deadline) is not int and type(deadline) is not float)
            or not math.isfinite(deadline)
            or not self._last_time < deadline <= self._last_time + OBSERVE_SECONDS
        ):
            _bad("Invalid original observe deadline")
        self._deadline = (
            self._last_time + OBSERVE_SECONDS if deadline is None else deadline
        )
        self._failure: str | None = None
        self._done = False
        self._metadata: Observation | None = None
        self._header = b""
        self._stream: FrameStream | None = None
        self.check()

    def _time(self) -> float:
        value = self._clock()
        if type(value) not in (int, float) or not math.isfinite(value):
            _bad("Invalid monotonic clock")
        return float(value)

    @property
    def failure(self) -> str | None:
        """Preserve the first failure even when late callbacks arrive."""
        return self._failure

    @property
    def payload_ready(self) -> bool:
        """Report correlation readiness, never a substitute for deadline checks."""
        return self._stream is not None and not self._done and self._failure is None

    def _fail(self, error: Exception) -> Never:
        if self._failure is None:
            self._failure = str(error) or type(error).__name__
        self._done = True
        raise ObservationError(self._failure) from error

    def check(self) -> None:
        """Allow polling to latch cancellation/expiry without receiving data."""
        try:
            if self._failure is not None:
                _bad(self._failure)
            if self._done:
                _bad("Observe operation is terminal")
            cancelled = self._cancelled()
            if type(cancelled) is not bool:
                _bad("Invalid cancellation callback result")
            if cancelled:
                _bad("Observe cancelled")
            now = self._time()
            if now < self._last_time or now >= self._deadline:
                _bad("Observe deadline expired or clock regressed")
            self._last_time = now
        except Exception as error:  # noqa: BLE001 - callback failure is terminal
            self._fail(error)

    def metadata(self, data: bytes) -> None:
        """Accept one control observation, before or after the fixed header."""
        self.check()
        try:
            if self._metadata is not None:
                _bad("Duplicate observation")
            observation = Observation.parse(data)
            identity = observation.frame_identity()
            if (
                observation.target != self._target
                or identity.session_id.hex() != self._epoch
                or identity.request_id != self._request_id
            ):
                _bad("Observation does not match outstanding request")
            self._metadata = observation
            self._pair()
            self.check()
        except Exception as error:  # noqa: BLE001 - preserve terminal first failure
            self._fail(error)

    def header(self, chunk: bytes) -> None:
        """Accumulate at most 128 bytes, never buffering unpaired pixel data."""
        self.check()
        try:
            if type(chunk) is not bytes or not 0 < len(chunk) <= HEADER_BYTES - len(
                self._header
            ):
                _bad("Invalid or duplicate frame header")
            self._header += chunk
            self._pair()
            self.check()
        except Exception as error:  # noqa: BLE001 - preserve terminal first failure
            self._fail(error)

    def _pair(self) -> None:
        if self._metadata is not None and len(self._header) == HEADER_BYTES:
            self._stream = FrameStream(self._header, self._metadata.frame_identity())

    def feed(self, chunk: bytes) -> None:
        """Hash one bounded body chunk only after matching metadata and header."""
        self.check()
        try:
            if self._stream is None:
                _bad("Payload arrived before metadata/header correlation")
            if type(chunk) is not bytes or not 0 < len(chunk) <= MAX_READ_BYTES:
                _bad("Invalid bounded body chunk")
            self._stream.feed(chunk)
            self.check()
        except Exception as error:  # noqa: BLE001 - preserve terminal first failure
            self._fail(error)

    def eof(self) -> FrameHeader:
        """Accept actual EOF once, returning metadata only after all gates pass."""
        self.check()
        try:
            if self._stream is None:
                _bad("EOF before metadata/header correlation")
            result = self._stream.finish()
            self.check()
            self._done = True
        except Exception as error:  # noqa: BLE001 - preserve terminal first failure
            self._fail(error)
        else:
            return result
