"""Fake Win32 DLL functions and caller-owned ABI buffers; no OS input calls."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
from collections.abc import Callable
from typing import cast

from verdiclip.capture.input_raw import RawInputDevice, RawMouseMessage


class DllFunction:
    """Allow production signature binding while executing only a local fake."""

    def __init__(self, call: Callable[..., object]) -> None:
        self.argtypes: list[object] = []
        self.restype: object = None
        self._call = call

    def __call__(self, *args: object) -> object:
        """Dispatch into a test-owned callable, never a native function pointer."""
        return self._call(*args)


class InputDllFixture:
    """Track native ownership and inject failures without touching a desktop."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.functions: dict[str, DllFunction] = {}
        self.registrations: list[tuple[int, int, int, int]] = []
        self.held: set[int] = set()
        self.window = 0
        self.fail = ""
        self.hook_calls = 0
        self.packet = RawMouseMessage()
        self.packet.header.kind = 0
        self.packet.header.size = ctypes.sizeof(self.packet)
        self.messages: list[w.MSG] = []
        self.peek_flood = False
        self.handlers: dict[str, Callable[..., object]] = {
            "GetRegisteredRawInputDevices": self._registered,
            "RegisterRawInputDevices": self._register,
            "CreateWindowExW": self._create,
            "DestroyWindow": self._destroy,
            "GetRawInputData": self._raw,
            "GetMessageW": self._message,
            "PeekMessageW": self._peek,
            "GetSystemMetrics": self._metrics,
            "GetAsyncKeyState": self._key_state,
            "GetPhysicalCursorPos": self._cursor,
            "GetUserObjectInformationW": self._desktop,
            "SetWindowsHookExW": self._hook,
        }

    def __getattr__(self, name: str) -> DllFunction:
        """Expose a fake entry point with mutable ctypes signature metadata."""
        if name not in self.functions:

            def call(*args: object) -> object:
                self.calls.append(name)
                if self.fail == name:
                    return 0
                handler = self.handlers.get(name)
                return handler(*args) if handler is not None else 1

            self.functions[name] = DllFunction(call)
        return self.functions[name]

    @staticmethod
    def _uint(pointer: object) -> ctypes._Pointer[w.UINT]:
        return ctypes.cast(cast(ctypes.c_void_p, pointer), ctypes.POINTER(w.UINT))

    @staticmethod
    def _address(pointer: object) -> ctypes.c_void_p:
        return cast(ctypes.c_void_p, pointer)

    def _registered(self, *args: object) -> int:
        self._uint(args[1]).contents.value = len(self.registrations)
        if args[0] is None:
            return 0
        values = ctypes.cast(self._address(args[0]), ctypes.POINTER(RawInputDevice))
        for index, (page, usage, flags, target) in enumerate(self.registrations):
            values[index] = RawInputDevice(page, usage, flags, target or None)
        return len(self.registrations)

    def _register(self, *args: object) -> int:
        value = ctypes.cast(
            self._address(args[0]), ctypes.POINTER(RawInputDevice)
        ).contents
        self.registrations = [
            item for item in self.registrations if item[:2] != (value.page, value.usage)
        ]
        if value.flags != 1:
            self.registrations.append(
                (value.page, value.usage, value.flags, int(value.target or 0))
            )
        return 1

    def _create(self, *_args: object) -> int:
        self.window = 1234
        return self.window

    def _destroy(self, *args: object) -> int:
        assert args[0] == self.window
        self.window = 0
        return 1

    def _raw(self, *args: object) -> int:
        size = ctypes.sizeof(self.packet)
        self._uint(args[3]).contents.value = size
        if args[2] is None:
            return 0
        ctypes.memmove(self._address(args[2]), ctypes.byref(self.packet), size)
        return size

    def _message(self, *args: object) -> int:
        if not self.messages:
            return 0
        message = self.messages.pop(0)
        ctypes.memmove(
            self._address(args[0]), ctypes.byref(message), ctypes.sizeof(message)
        )
        return 1

    def _peek(self, *_args: object) -> int:
        return int(self.peek_flood)

    @staticmethod
    def _metrics(*args: object) -> int:
        values = {0: 1920, 1: 1080, 76: -1920, 77: 0, 78: 3840, 79: 1080}
        return values[cast(int, args[0])]

    def _key_state(self, *args: object) -> int:
        return 0x8000 if cast(int, args[0]) in self.held else 0

    @staticmethod
    def _cursor(*args: object) -> int:
        point = ctypes.cast(InputDllFixture._address(args[0]), ctypes.POINTER(w.POINT))
        point.contents.x, point.contents.y = 100, 200
        return 1

    @staticmethod
    def _desktop(*args: object) -> int:
        name = ctypes.create_unicode_buffer("Default")
        ctypes.memmove(InputDllFixture._address(args[2]), name, ctypes.sizeof(name))
        return 1

    def _hook(self, *_args: object) -> int:
        self.hook_calls += 1
        return (
            0
            if self.fail == "second-hook" and self.hook_calls == 2
            else self.hook_calls
        )
