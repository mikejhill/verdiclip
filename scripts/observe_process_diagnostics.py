"""Bounded requested-PID diagnostics, never ancestry or process authorization.

One documented Toolhelp snapshot supplies parent PID hints. Retained process
handles, GetProcessId, creation FILETIME and zero-time waits qualify only those
exact requested processes. No job handle, native job query, or executable name
from an unrelated Toolhelp entry is retained.
"""

from __future__ import annotations

import ctypes as c
import math
import sys
import time
from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Protocol

MAX_PIDS = 8
MAX_ENTRIES = 4096
ENUMERATION_SECONDS = 3.0
IMAGE_BUFFER_CHARS = 32768
MAX_IMAGE_CHARS = 1024
MAX_ERROR_CHARS = 256
QUERY_LIMITED_SYNCHRONIZE = 0x00101000
TH32CS_SNAPPROCESS = 0x00000002
WAIT_TIMEOUT = 258
WAIT_OBJECT_0 = 0
ERROR_NO_MORE_FILES = 18
INVALID_HANDLE_VALUE = c.c_void_p(-1).value
Json = dict[str, Any]


class Journal(Protocol):
    """Existing external watchdog boundary, including its original deadline."""

    def call[T](self, name: str, operation: Callable[[], T]) -> T:
        """Log and bound one complete operation without restarting any budget."""
        ...


class ProcessEntry32W(c.Structure):
    """Documented PROCESSENTRY32W ABI, including pointer-width default heap ID."""

    _fields_ = [
        ("dwSize", c.c_uint32),
        ("cntUsage", c.c_uint32),
        ("th32ProcessID", c.c_uint32),
        ("th32DefaultHeapID", c.c_size_t),
        ("th32ModuleID", c.c_uint32),
        ("cntThreads", c.c_uint32),
        ("th32ParentProcessID", c.c_uint32),
        ("pcPriClassBase", c.c_int32),
        ("dwFlags", c.c_uint32),
        # Windows WCHAR is always 16 bits, including in portable ABI tests.
        ("szExeFile", c.c_uint16 * 260),
    ]


class StaleProcessError(RuntimeError):
    """The retained process exited or no longer matches the frozen identity."""


@dataclass
class Pin:
    """Only one explicitly requested PID and its retained process handle."""

    requested_pid: int
    handle: int | None = None
    process_id: int | None = None
    creation_100ns: int | None = None
    image_path: str | None = None
    status: str = "unavailable"
    errors: list[str] = field(default_factory=list)
    parent: Json = field(default_factory=lambda: {"status": "unavailable"})

    def value(self) -> Json:
        """Detach bounded metadata; no native handles leave this owner."""
        return {
            "requested_pid": self.requested_pid,
            "process_id": self.process_id,
            "creation_100ns": self.creation_100ns,
            "image_path": self.image_path,
            "status": self.status,
            "errors": list(self.errors),
            "parent": dict(self.parent),
        }


def error_text(error: BaseException) -> str:
    """Bound diagnostics without truncating a successful process identity."""
    return repr(error)[:MAX_ERROR_CHARS]


def native_error(operation: str) -> OSError:
    """Retain the immediately observed Win32 error before another native call."""
    return OSError(c.get_last_error(), f"{operation} failed")


class PinnedProcesses:
    """Own at most eight exact pins and one diagnostic-only Toolhelp snapshot."""

    def __init__(self, pids: Sequence[int], deadline: float, journal: Journal) -> None:
        if (
            not 1 <= len(pids) <= MAX_PIDS
            or any(type(pid) is not int or not 1 <= pid < 2**32 for pid in pids)
            or len(set(pids)) != len(pids)
            or type(deadline) not in (int, float)
            or not math.isfinite(deadline)
        ):
            raise ValueError("Invalid bounded process diagnostic request")
        self.deadline, self.journal = deadline, journal
        self.pins = [Pin(pid) for pid in pids]
        self.cleanup_errors: list[str] = []
        self.collected = False
        self.closed = False
        self.final: Json | None = None
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        self._bind()
        try:
            for pin in self.pins:
                self._pin(pin)
        except BaseException as primary:
            for error in self.close():
                primary.add_note(error)
            raise

    def _bind(self) -> None:
        for name, arguments, result in (
            ("OpenProcess", [c.c_uint32, c.c_int32, c.c_uint32], c.c_void_p),
            ("GetProcessId", [c.c_void_p], c.c_uint32),
            (
                "GetProcessTimes",
                [c.c_void_p, *([c.POINTER(c.c_uint64)] * 4)],
                c.c_int32,
            ),
            ("WaitForSingleObject", [c.c_void_p, c.c_uint32], c.c_uint32),
            (
                "QueryFullProcessImageNameW",
                [c.c_void_p, c.c_uint32, c.POINTER(c.c_wchar), c.POINTER(c.c_uint32)],
                c.c_int32,
            ),
            ("CreateToolhelp32Snapshot", [c.c_uint32, c.c_uint32], c.c_void_p),
            ("Process32FirstW", [c.c_void_p, c.POINTER(ProcessEntry32W)], c.c_int32),
            ("Process32NextW", [c.c_void_p, c.POINTER(ProcessEntry32W)], c.c_int32),
            ("CloseHandle", [c.c_void_p], c.c_int32),
        ):
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = arguments, result

    def _check(self, deadline: float | None = None) -> None:
        limit = self.deadline if deadline is None else min(self.deadline, deadline)
        if time.monotonic() >= limit:
            raise RuntimeError("Process diagnostic original deadline expired")

    def _call[T](self, name: str, operation: Callable[[], T]) -> T:
        primary: BaseException | None = None

        def invoke() -> T:
            nonlocal primary
            try:
                return operation()
            except BaseException as error:
                primary = error
                raise

        try:
            return self.journal.call(f"topology.{name}", invoke)
        except BaseException as error:
            if primary is not None and primary is not error:
                primary.add_note(f"Journal failure: {error_text(error)}")
                raise primary from error
            raise

    def _query[T](self, pin: Pin, name: str, operation: Callable[[], T]) -> T:
        def checked() -> T:
            self._check()
            result = operation()
            self._check()
            return result

        return self._call(f"{name}.{pin.requested_pid}", checked)

    def _pin(self, pin: Pin) -> None:
        def acquire() -> None:
            handle = self.kernel.OpenProcess(
                QUERY_LIMITED_SYNCHRONIZE, 0, pin.requested_pid
            )
            if not handle:
                raise native_error("OpenProcess")
            # Retain before the logger can fail after the native call returns.
            pin.handle = handle

        try:
            self._query(pin, "OpenProcess", acquire)
            pin.process_id = self._process_id(pin)
            if pin.process_id != pin.requested_pid:
                raise StaleProcessError("Pinned process ID differs from requested PID")  # noqa: TRY301 - isolate one PID failure
            pin.creation_100ns = self._creation(pin)
            self._alive(pin)
            pin.image_path = self._query(
                pin, "QueryFullProcessImageNameW", lambda: self._image(pin)
            )
            pin.status = "available"
        except Exception as error:  # noqa: BLE001 - retain independent per-PID failures
            self._failed(pin, error)

    @staticmethod
    def _failed(pin: Pin, error: Exception) -> None:
        pin.status = "stale" if isinstance(error, StaleProcessError) else "unavailable"
        pin.errors.append(error_text(error))
        pin.parent = {"status": pin.status}

    def _process_id(self, pin: Pin) -> int:
        def read() -> int:
            value = int(self.kernel.GetProcessId(pin.handle))
            if not value:
                raise native_error("GetProcessId")
            return value

        return self._query(pin, "GetProcessId", read)

    def _creation(self, pin: Pin) -> int:
        def read() -> int:
            times = [c.c_uint64() for _ in range(4)]
            if not self.kernel.GetProcessTimes(
                pin.handle, *(c.byref(t) for t in times)
            ):
                raise native_error("GetProcessTimes")
            if times[0].value == 0:
                raise RuntimeError("Missing process creation FILETIME")
            return times[0].value

        return self._query(pin, "GetProcessTimes", read)

    def _alive(self, pin: Pin) -> None:
        def read() -> None:
            state = self.kernel.WaitForSingleObject(pin.handle, 0)
            if state == WAIT_OBJECT_0:
                raise StaleProcessError("Pinned process exited")
            if state != WAIT_TIMEOUT:
                raise native_error("WaitForSingleObject")

        self._query(pin, "WaitForSingleObject", read)

    def _image(self, pin: Pin) -> str:
        buffer = c.create_unicode_buffer(IMAGE_BUFFER_CHARS)
        size = c.c_uint32(IMAGE_BUFFER_CHARS)
        if not self.kernel.QueryFullProcessImageNameW(
            pin.handle, 0, buffer, c.byref(size)
        ):
            raise native_error("QueryFullProcessImageNameW")
        if not 0 < size.value <= MAX_IMAGE_CHARS:
            raise RuntimeError("Process image exceeds retained character bound")
        value = c.wstring_at(buffer, size.value)
        if "\0" in value:
            raise RuntimeError("Process image contains incomplete native output")
        return value

    def _recheck(self, pin: Pin) -> None:
        if pin.handle is None or pin.creation_100ns is None:
            pin.parent = {"status": pin.status}
            return
        try:
            self._alive(pin)
            if (
                self._process_id(pin) != pin.process_id
                or self._creation(pin) != pin.creation_100ns
            ):
                raise StaleProcessError("Pinned process identity changed")  # noqa: TRY301 - isolate one PID failure
            self._alive(pin)
        except Exception as error:  # noqa: BLE001 - retain independent per-PID failures
            self._failed(pin, error)

    def _raw_close(self, handle: int) -> None:
        if not self.kernel.CloseHandle(handle):
            raise native_error("CloseHandle")

    def _enumerate(self, metadata: Json) -> dict[int, int]:
        limit = min(self.deadline, metadata["started_monotonic"] + ENUMERATION_SECONDS)
        self._check(limit)
        snapshot = self.kernel.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snapshot or snapshot == INVALID_HANDLE_VALUE:
            raise native_error("CreateToolhelp32Snapshot")
        try:
            self._check(limit)
            wanted = {pin.requested_pid for pin in self.pins}
            parents: dict[int, int] = {}
            entry = ProcessEntry32W()
            entry.dwSize = c.sizeof(entry)
            present = self.kernel.Process32FirstW(snapshot, c.byref(entry))
            while present:
                self._check(limit)
                metadata["entries"] += 1
                if entry.th32ProcessID in wanted:
                    if entry.th32ProcessID in parents:
                        raise RuntimeError(
                            "Duplicate requested PID in Toolhelp snapshot"
                        )
                    parents[entry.th32ProcessID] = entry.th32ParentProcessID
                    if len(parents) == len(wanted):
                        self._check(limit)
                        metadata["complete"] = True
                        return parents
                # Do not ask the kernel for a 4097th entry, even at exact capacity.
                if metadata["entries"] >= MAX_ENTRIES:
                    raise RuntimeError("Toolhelp process entry bound reached")
                self._check(limit)
                entry.dwSize = c.sizeof(entry)
                present = self.kernel.Process32NextW(snapshot, c.byref(entry))
            code = c.get_last_error()
            if code != ERROR_NO_MORE_FILES:
                raise OSError(code, "Toolhelp process enumeration failed")
            self._check(limit)
            metadata["complete"] = True
            return parents
        finally:
            primary = sys.exception()
            try:
                self._raw_close(snapshot)
            except BaseException as error:
                self.cleanup_errors.append(error_text(error))
                if primary is None:
                    raise
                primary.add_note(f"Snapshot cleanup failed: {error_text(error)}")

    def collect(self) -> Json:
        """Run one logged snapshot, then recheck every retained process identity."""
        if self.collected or self.closed:
            raise RuntimeError("Process diagnostic collection is one-shot")
        self.collected = True
        metadata: Json = {
            "started_monotonic": time.monotonic(),
            "finished_monotonic": None,
            "entries": 0,
            "complete": False,
            "error": None,
        }
        parents: dict[int, int] = {}
        try:
            parents = self._call(
                "Toolhelp.enumerate", lambda: self._enumerate(metadata)
            )
        except Exception as error:  # noqa: BLE001 - diagnostics must not discard the other PIDs
            metadata.update(complete=False, error=error_text(error))
        finally:
            metadata["finished_monotonic"] = time.monotonic()
        for pin in self.pins:
            self._recheck(pin)
            if (
                pin.status == "available"
                and metadata.get("complete") is True
                and pin.requested_pid in parents
            ):
                pin.parent = {"status": "available", "pid": parents[pin.requested_pid]}
            elif pin.status != "available":
                pin.parent = {"status": pin.status}
            else:
                pin.parent = {"status": "unavailable"}
        return {
            "processes": [pin.value() for pin in self.pins],
            "enumeration": metadata,
        }

    def finish(self) -> Json:
        """Recheck pinned state, then close everything without renewing any budget."""
        if self.final is not None:
            return deepcopy(self.final)
        try:
            for pin in self.pins:
                self._recheck(pin)
        finally:
            self.close()
        self.final = {
            "processes": [pin.value() for pin in self.pins],
            "cleanup_errors": list(self.cleanup_errors),
        }
        return deepcopy(self.final)

    def close(self) -> list[str]:
        """Close all acquired handles once even if the journal fails before a call."""
        if self.closed:
            return list(self.cleanup_errors)
        self.closed = True
        for pin in self.pins:
            handle, pin.handle = pin.handle, None
            if handle is None:
                continue
            attempted = False

            def release(handle: int = handle) -> None:
                nonlocal attempted
                attempted = True
                self._raw_close(handle)

            try:
                self._call(f"CloseHandle.{pin.requested_pid}", release)
            except BaseException as error:  # noqa: BLE001 - attempt every retained handle
                self.cleanup_errors.append(error_text(error))
                if not attempted:
                    try:
                        release()
                    except BaseException as fallback:  # noqa: BLE001 - retain all cleanup faults
                        self.cleanup_errors.append(error_text(fallback))
        return list(self.cleanup_errors)
