"""Finite dedicated-thread input ownership, independent of Qt's popup routing."""

from __future__ import annotations

import logging
import math
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar

from verdiclip.capture.input_native import (
    InputPlatform,
    KeyboardPacket,
    MousePacket,
    Win32InputPlatform,
)
from verdiclip.capture.input_policy import (
    CaptureInputPolicy,
    InputDecision,
    InputKind,
    SelectionInput,
)
from verdiclip.capture.input_raw import RawPointer
from verdiclip.capture.input_routing import MoveMode
from verdiclip.capture.native_pointer import PointerPhase
from verdiclip.capture.native_pointer_win32 import NativePointerSession
from verdiclip.exceptions import CaptureError, PlatformError
from verdiclip.geometry import Point, Rect

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class InputWorkerOptions:
    """Finite safety budgets and an explicit fixture-only injected-input profile."""

    max_seconds: float = 120
    heartbeat_seconds: float = 1
    drain_seconds: float = 0.5
    injected_move_mode: MoveMode | None = None
    MAX_SELECTION_SECONDS: ClassVar[float] = 120
    MAX_HEARTBEAT_SECONDS: ClassVar[float] = 2
    MAX_DRAIN_SECONDS: ClassVar[float] = 1

    def __post_init__(self) -> None:
        """No indefinite hooks or disabled watchdog can be configured."""
        if not all(
            math.isfinite(value) and value > 0
            for value in (self.max_seconds, self.heartbeat_seconds, self.drain_seconds)
        ):
            raise ValueError("Capture input budgets must be finite and positive")
        if (
            self.max_seconds > self.MAX_SELECTION_SECONDS
            or self.heartbeat_seconds > self.MAX_HEARTBEAT_SECONDS
            or self.drain_seconds > self.MAX_DRAIN_SECONDS
        ):
            raise ValueError("Capture input budgets exceed their safety limits")


class CaptureInputWorker:
    """Keep native callbacks off the GUI thread and publish only semantic events.

    Each instance owns one installer thread, two temporary hooks and its timer.
    A Qt heartbeat and an independent hard deadline end interception even when
    the UI stops responding. No event is replayed into an external application.
    """

    QUEUE_LIMIT = 512
    RAW_TIMEOUT_SECONDS = 0.25

    def __init__(
        self,
        initial: Point,
        bounds: Rect,
        *,
        options: InputWorkerOptions | None = None,
        platform_factory: Callable[[], InputPlatform] = Win32InputPlatform,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Construct no native API and intercept nothing until explicit start."""
        self.options = options if options is not None else InputWorkerOptions()
        self._factory, self._clock = platform_factory, clock
        self._policy = CaptureInputPolicy(initial, bounds)
        self._events: queue.Queue[SelectionInput] = queue.Queue(self.QUEUE_LIMIT)
        self.ready = threading.Event()
        self.stopped = threading.Event()
        self._release = threading.Event()
        self._controls = threading.Event()
        self._released_at: float | None = None
        self._beat = clock()
        self._thread: threading.Thread | None = None
        self._api: InputPlatform | None = None
        self._pending_raw: float | None = None
        self._hardware_packets = 0
        self._raw_reports = 0
        self._legacy_buttons: set[tuple[int, int]] = set()
        self._raw_releases: set[tuple[int, int]] = set()
        self.native_pointer: NativePointerSession | None = None
        self.foreground = 0
        self.failure = ""
        self.cleaned = False

    def start(self) -> None:
        """Create exactly one short-lived native installer/message-pump thread."""
        if self._thread is not None:
            raise CaptureError("Capture input is already reserved")
        self._thread = threading.Thread(
            target=self._run, name="VerdiClip capture input", daemon=True
        )
        self._thread.start()

    def heartbeat(self) -> None:
        """Only an actively serviced Qt lease extends the UI responsiveness budget."""
        self._beat = self._clock()

    @property
    def active(self) -> bool:
        """Latch loss of ownership immediately, including bounded release draining."""
        return self.ready.is_set() and not (
            self._release.is_set() or self.stopped.is_set()
        )

    def set_controls_mode(self, *, enabled: bool) -> None:
        """Change semantic navigation without moving the virtual or OS pointer."""
        if enabled:
            self._controls.set()
        else:
            self._controls.clear()

    def release(self) -> None:
        """Request bounded pair draining and removal on the installer thread."""
        if self._released_at is None:
            self._released_at = self._clock()
        if self.native_pointer is not None:
            self.native_pointer.close()
        self._release.set()

    def drain(self) -> tuple[SelectionInput, ...]:
        """Drain a bounded semantic queue; key text is never stored."""
        events: list[SelectionInput] = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                return tuple(events)

    def _publish(self, decision: InputDecision) -> int:
        for event in decision.events:
            if event.kind is InputKind.CANCEL and self.native_pointer is not None:
                self.native_pointer.close()
            try:
                self._events.put_nowait(event)
            except queue.Full:
                self.failure = "Capture input exceeded its event budget"
                self.release()
                break
        return int(decision.consume)

    def _invalidate(self, reason: str) -> None:
        self.failure = reason
        self._publish(InputDecision(consume=False, events=self._policy.cancel(reason)))
        self.release()

    def _run(self) -> None:
        """Install, pump and retire on one thread; cleanup always precedes receipt."""
        try:
            self._normalize(self._serve)
        except (OSError, PlatformError, CaptureError) as error:
            self.failure = str(error)
            logger.warning("Capture input unavailable: %s", error)
        finally:
            try:
                self.cleaned = self._api is None or self._normalize(self._api.close)
                if not self.cleaned:
                    self.failure = "Capture input cleanup could not be confirmed"
            except (OSError, PlatformError, CaptureError) as error:
                self.failure = "Capture input cleanup failed"
                logger.warning("Capture input cleanup failed: %s", error)
            try:
                logger.info(
                    "Capture input worker retired; cleanup_confirmed=%s", self.cleaned
                )
            finally:
                self.stopped.set()

    def _serve(self) -> None:
        started = self._clock()
        api = self._factory()
        self._api = api
        self.foreground = api.foreground()
        if not self.foreground or not api.desktop_safe() or api.buttons_held():
            raise CaptureError("Capture input is unavailable on the current desktop")
        if not api.recipient_safe(self.foreground):
            raise CaptureError("Capture cannot safely own input for this application")
        logger.info("Capture input eligibility admitted; acquiring owned input")
        api.install(self._mouse_callback, self._key_callback, self._raw_pointer)
        held = api.keys_held()
        self._policy.prime_keys(held)
        if (
            held
            & (self._policy.ARROWS.keys() | {self._policy.ENTER, self._policy.ESCAPE})
            or api.buttons_held()
            or not api.recipient_safe(self.foreground)
        ):
            raise CaptureError(
                "Release selection keys and mouse buttons before capture"
            )
        self.ready.set()
        logger.info("Capture input ready")
        while self._continue(started) and api.pump():
            pass
        if not self._release.is_set():
            self.failure = "Capture input message loop ended unexpectedly"

    def _continue(self, started: float) -> bool:
        now = self._clock()
        api = self._api
        if api is None:
            return False
        if not self._release.is_set():
            reason = self._ownership_failure(api, started, now)
            if reason:
                self._invalidate(reason)
        if self._release.is_set():
            self._policy.cancel("capture-ended")
            released_at = self._released_at
            draining = (
                self._policy.owns_pairs or bool(self._legacy_buttons)
            ) and released_at is not None
            if draining and now - released_at >= self.options.drain_seconds:
                if not self.failure:
                    self.failure = "Capture input release reached its deadline"
                return False
            return draining
        return True

    def _ownership_failure(self, api: InputPlatform, started: float, now: float) -> str:
        if now - started >= self.options.max_seconds:
            return "Capture selection reached its time limit"
        if now - self._beat >= self.options.heartbeat_seconds:
            return "Capture stopped because its UI stopped responding"
        if api.foreground() != self.foreground or not api.desktop_safe():
            return "Capture foreground or desktop changed"
        native_failure = self._native_failure()
        if native_failure or not api.recipient_safe(self.foreground):
            return native_failure or "Capture input recipient is no longer supported"
        if (
            self._pending_raw is not None
            and now - self._pending_raw >= self.RAW_TIMEOUT_SECONDS
        ):
            logger.warning(
                "Capture pointer delivery expired; hardware_packets=%d raw_reports=%d",
                self._hardware_packets,
                self._raw_reports,
            )
            return "This pointer source is unavailable for capture"
        return ""

    def _native_failure(self) -> str:
        pointer = self.native_pointer
        if pointer is not None:
            if pointer.route.phase is PointerPhase.CLOSED:
                return "Native pointer ownership ended"
            if pointer.route.phase is PointerPhase.OWNED and not pointer.safe():
                return "Native pointer recipient changed"
            if pointer.route.phase is PointerPhase.WAITING and pointer.expired():
                return "Native cursor ownership was not observed"
        return ""

    @staticmethod
    def _normalize[T](callback: Callable[[], T]) -> T:
        """Immediately re-raise callback bugs as a contained capture-domain error."""
        try:
            return callback()
        except (OSError, PlatformError, CaptureError):
            raise
        except Exception as error:
            raise CaptureError("Capture input callback failed") from error

    def _callback(
        self, callback: Callable[[], int], code: int, message: int, address: int
    ) -> int:
        api = self._api
        if api is None:
            return 0
        try:
            return self._normalize(callback)
        except (OSError, PlatformError, CaptureError) as error:
            logger.warning("Capture input callback stopped: %s", error)
            self._invalidate("Capture input could not be safely routed")
            if code == 0:
                self._reserve_failed_packet(message, address)
                return 1
            return api.forward(code, message, address)

    def _reserve_failed_packet(self, message: int, address: int) -> None:
        if message in CaptureInputPolicy.BUTTON_DOWN:
            point = self._policy.pointer.point
            packet = MousePacket.from_address(address)
            button = (
                int(packet.data >> 16) if message == CaptureInputPolicy.X_DOWN else 0
            )
            self._policy.mouse(message, point, mode=None, anchor=point, button=button)
        elif message in {0x100, 0x104}:
            packet = KeyboardPacket.from_address(address)
            self._policy.key(int(packet.code), pressed=True)

    def _mouse_callback(self, code: int, message: int, address: int) -> int:
        return self._callback(
            lambda: self._mouse(code, message, address), code, message, address
        )

    def _mouse(self, code: int, message: int, address: int) -> int:
        api = self._api
        if api is None:
            return 0
        if code != 0:
            return api.forward(code, message, address)
        packet = MousePacket.from_address(address)
        button = (
            int(packet.data >> 16)
            if message in {CaptureInputPolicy.X_DOWN, CaptureInputPolicy.X_UP}
            else 0
        )
        owned_release = False
        if message in CaptureInputPolicy.BUTTON_DOWN:
            self._raw_releases.discard((message + 1, button))
            self._legacy_buttons.add((message, button))
        elif message in CaptureInputPolicy.BUTTON_UP:
            owned_release = (message - 1, button) in self._legacy_buttons
            self._legacy_buttons.discard((message - 1, button))
        if api.foreground() != self.foreground:
            self._invalidate("Capture foreground changed")
        if self.native_pointer is not None:
            return self._native_mouse(
                packet,
                message,
                address,
                button=button,
                owned_release=owned_release,
            )
        if not packet.flags & 1:
            self._hardware_packets += 1
            if owned_release and (message, button) in self._raw_releases:
                # Semantic Raw Input already completed this owned button pair.
                # Consume its later hook receipt without expecting a second raw up.
                self._raw_releases.discard((message, button))
                return 1
            if self._pending_raw is None:
                self._pending_raw = self._clock()
            return 1
        mode = self.options.injected_move_mode
        decision = self._policy.mouse(
            message,
            Point(packet.point.x, packet.point.y),
            mode=mode,
            anchor=api.cursor()
            if message == CaptureInputPolicy.MOVE
            else self._policy.pointer.point,
            button=button,
        )
        # Raw Input may already have completed the semantic pair. Native hook
        # ownership still owes consumption of the release of its swallowed down.
        consumed = self._publish(decision)
        return consumed or int(owned_release) or api.forward(code, message, address)

    def _native_mouse(
        self,
        packet: MousePacket,
        message: int,
        address: int,
        *,
        button: int,
        owned_release: bool,
    ) -> int:
        pointer, api = self.native_pointer, self._api
        if pointer is None or api is None:
            return 1
        point = Point(packet.point.x, packet.point.y)
        permitted = pointer.move(point)
        if not permitted:
            if (
                pointer.route.phase is PointerPhase.CLOSED
                or message != CaptureInputPolicy.MOVE
            ):
                self._invalidate("Native pointer recipient changed")
        else:
            movement = self._policy.mouse(
                CaptureInputPolicy.MOVE, point, mode=MoveMode.ABSOLUTE, anchor=point
            )
            self._publish(movement)
        if message == CaptureInputPolicy.MOVE:
            return (
                api.forward(0, message, address)
                if permitted and pointer.route.phase is PointerPhase.OWNED
                else 1
            )
        decision = self._policy.mouse(
            message, point, mode=None, anchor=point, button=button
        )
        return (
            self._publish(decision)
            or int(owned_release)
            or api.forward(0, message, address)
        )

    def _raw_pointer(self, event: RawPointer) -> None:
        """Hardware semantics come from its raw flags, never inferred hook flags."""
        self._pending_raw = None
        self._raw_reports += 1
        if self.native_pointer is not None:
            # Native hook coordinates are absolute. Raw reports must not apply
            # relative motion or duplicate button semantics in either order.
            return
        point = self._policy.pointer.point
        self._publish(
            self._policy.mouse(
                CaptureInputPolicy.MOVE, event.point, mode=event.mode, anchor=point
            )
        )
        for message, identity in event.buttons:
            if (message - 1, identity) in self._legacy_buttons:
                self._raw_releases.add((message, identity))
            self._publish(
                self._policy.mouse(
                    message,
                    point,
                    mode=None,
                    anchor=point,
                    button=identity,
                )
            )
        if event.wheel:
            self._publish(
                self._policy.mouse(
                    CaptureInputPolicy.WHEEL,
                    point,
                    mode=None,
                    anchor=point,
                )
            )

    def _key_callback(self, code: int, message: int, address: int) -> int:
        return self._callback(
            lambda: self._key(code, message, address), code, message, address
        )

    def _key(self, code: int, message: int, address: int) -> int:
        api = self._api
        if api is None:
            return 0
        if code != 0 or message not in {0x100, 0x101, 0x104, 0x105}:
            return api.forward(code, message, address)
        if api.foreground() != self.foreground:
            self._invalidate("Capture foreground changed")
        packet = KeyboardPacket.from_address(address)
        decision = self._policy.key(
            int(packet.code),
            pressed=message in {0x100, 0x104},
            control=api.control_held(),
            shift=api.shift_held(),
            controls=self._controls.is_set(),
        )
        pointer = self.native_pointer
        if pointer is not None and not self._controls.is_set():
            for event in decision.events:
                if event.reason.startswith("arrow:") and not pointer.nudge(event.point):
                    self._invalidate("Native pointer keyboard movement failed")
                    return 1
        return self._publish(decision) or api.forward(code, message, address)
