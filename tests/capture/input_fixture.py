"""Replay a native input boundary without installing hooks or reading a desktop."""

from __future__ import annotations

import ctypes
import queue
import threading
from collections.abc import Callable
from typing import override

from PySide6.QtCore import QObject, QTimer

from verdiclip.capture.input_broker import InputBroker
from verdiclip.capture.input_native import HookCallback, KeyboardPacket, MousePacket
from verdiclip.capture.input_policy import SelectionInput
from verdiclip.capture.input_raw import RawPointer, RawPointerCallback
from verdiclip.geometry import Point, Rect


class ReplayInputPlatform:
    """A deterministic clock, packet queue and observable native-resource owner."""

    def __init__(self) -> None:
        self.time = 0.0
        self.owner = 42
        self.safe = True
        self.recipient_supported = True
        self.held = False
        self.control = False
        self.shift = False
        self.held_keys: frozenset[int] = frozenset()
        self.anchor = Point(262, 168)
        self.install_error = False
        self.cursor_error = False
        self.close_ok = True
        self.installed = False
        self.closed = False
        self.install_thread = self.close_thread = 0
        self.results: list[int] = []
        self.forwarded: list[int] = []
        self.actions: queue.Queue[Callable[[], None]] = queue.Queue()
        self._mouse: HookCallback = self._noop
        self._keyboard: HookCallback = self._noop
        self._raw: RawPointerCallback = self._ignore_raw
        self._yield = threading.Event()

    @staticmethod
    def _noop(_code: int, _message: int, _address: int) -> int:
        return 0

    def clock(self) -> float:
        """Advance only when the scripted message pump runs."""
        return self.time

    def foreground(self) -> int:
        """Return only the synthetic owner."""
        return self.owner

    def cursor(self) -> Point:
        """Keep a frozen OS anchor or simulate a native read failure."""
        if self.cursor_error:
            raise ValueError("Fixture cursor unavailable")
        return self.anchor

    def desktop_safe(self) -> bool:
        """No WTS or native desktop call happens here."""
        return self.safe

    def buttons_held(self) -> bool:
        """Represent a press that existed before ownership."""
        return self.held

    def recipient_safe(self, _foreground: int) -> bool:
        """Model UIPI rejection without inspecting real process security tokens."""
        return self.recipient_supported

    def control_held(self) -> bool:
        """Return a synthetic modifier."""
        return self.control

    def shift_held(self) -> bool:
        """Return a synthetic modifier for backwards control selection."""
        return self.shift

    def keys_held(self) -> frozenset[int]:
        """Return only scripted initial key state."""
        return self.held_keys

    @staticmethod
    def _ignore_raw(_event: RawPointer) -> None:
        pass

    def install(
        self, mouse: HookCallback, keyboard: HookCallback, raw: RawPointerCallback
    ) -> None:
        """Retain callbacks, creating no OS hook."""
        self._mouse, self._keyboard = mouse, keyboard
        self._raw = raw
        self.installed = True
        self.install_thread = threading.get_ident()
        if self.install_error:
            raise OSError("Fixture partial installation failure")

    def pump(self) -> bool:
        """One scripted packet or a deterministic watchdog tick."""
        self.time += 0.01
        try:
            action = self.actions.get_nowait()
        except queue.Empty:
            self._yield.wait(0.001)
        else:
            action()
        return True

    def forward(self, _code: int, message: int, _address: int) -> int:
        """Record semantic pass-through without sending input anywhere."""
        self.forwarded.append(message)
        return 99

    def close(self) -> bool:
        """Record cleanup order and simulate its acknowledgement."""
        self.close_thread = threading.get_ident()
        self.closed = True
        return self.close_ok

    def mouse(self, message: int, point: Point, *, injected: bool = True) -> None:
        """Queue a native-shaped packet whose storage lives through its callback."""

        def deliver() -> None:
            packet = MousePacket()
            packet.point.x, packet.point.y = int(point.x), int(point.y)
            packet.flags = int(injected)
            self.results.append(self._mouse(0, message, ctypes.addressof(packet)))

        self.actions.put(deliver)

    def key(self, message: int, code: int) -> None:
        """Queue only a synthetic virtual key; no text or hardware is accessed."""

        def deliver() -> None:
            packet = KeyboardPacket()
            packet.code = code
            self.results.append(self._keyboard(0, message, ctypes.addressof(packet)))

        self.actions.put(deliver)

    def raw(self, event: RawPointer) -> None:
        """Queue an explicitly relative/absolute report, with no device access."""
        self.actions.put(lambda: self._raw(event))


class ReplayInputBroker(InputBroker):
    """Explicit service-test boundary; no hooks, grabs, activation or OS input."""

    def __init__(self, owner: QObject) -> None:
        super().__init__(owner)
        self._active = False
        self._busy = False
        self.defer_cleanup = False
        self.defer_ready = False
        self.starts = 0
        self.releases = 0
        self._cleanup = QTimer(self)
        self._cleanup.setSingleShot(True)
        self._cleanup.timeout.connect(self.acknowledge_cleanup)

    @property
    @override
    def active(self) -> bool:
        return self._active

    @property
    @override
    def busy(self) -> bool:
        return self._busy

    @override
    def start(self, initial: Point, bounds: Rect) -> None:
        """Ready can be synchronous; production must tolerate this reentrancy."""
        del initial, bounds
        self.starts += 1
        self._active = self._busy = True
        if not self.defer_ready:
            self.ready.emit()

    @override
    def release(self) -> None:
        """Keep cleanup deliberately pending for acknowledgement regressions."""
        self.releases += 1
        self._active = False
        if not self.defer_cleanup:
            self.acknowledge_cleanup()

    def acknowledge_cleanup(self) -> None:
        """Emit only a lease that actually existed."""
        if self._busy:
            cleaned = self._busy
            self._busy = False
            self.closed.emit(cleaned, "")

    def feed(self, event: SelectionInput) -> None:
        """Replay semantic input without native Qt popup routing or physical input."""
        if self._active:
            self.input_event.emit(event)

    def interrupt(self, message: str, *, cleaned: bool = True) -> None:
        """Replay a spontaneous deadline/failure receipt after selection takeover."""
        self._active = False
        if cleaned:
            self._busy = False
        self.closed.emit(cleaned, message)
