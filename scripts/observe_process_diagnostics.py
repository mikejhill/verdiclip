"""Harness-only PID diagnostics and explicit bounded ownership profiles.

One documented Toolhelp snapshot supplies parent PID hints. Retained process
handles, GetProcessId, creation FILETIME and zero-time waits qualify only those
exact requested processes. No job handle, native job query, or executable name
from an unrelated Toolhelp entry is retained.
"""

from __future__ import annotations

import ctypes as c
import math
import ntpath
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol

MAX_PIDS = 8
MAX_ENTRIES = 4096
ENUMERATION_SECONDS = 3.0
NATIVE_CALL_SECONDS = 3.0
PROFILE_PROCESS_COUNT = 3
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


def logged_call[T](journal: Journal, name: str, operation: Callable[[], T]) -> T:
    """Preserve a native failure when an error logger itself raises afterward."""
    primary: BaseException | None = None

    def invoke() -> T:
        nonlocal primary
        try:
            return operation()
        except BaseException as error:
            primary = error
            raise

    try:
        return journal.call(f"topology.{name}", invoke)
    except BaseException as error:
        if primary is not None and primary is not error:
            primary.add_note(f"Journal failure: {error_text(error)}")
            raise primary from error
        raise


def canonical_image(value: object) -> str:
    """Compare bounded absolute Windows paths without discarding namespace prefixes."""
    if (
        type(value) is not str
        or not 0 < len(value) <= MAX_IMAGE_CHARS
        or "\0" in value
        or not ntpath.isabs(value)
        or not ntpath.splitdrive(value)[0]
    ):
        raise ValueError("Invalid absolute Windows executable path")
    return ntpath.normcase(ntpath.normpath(value))


def expected_console_image(journal: Journal, deadline: float) -> str:
    """Derive the sole console image from GetSystemDirectoryW, never environment."""
    if type(deadline) not in (int, float) or not math.isfinite(deadline):
        raise ValueError("Invalid process diagnostic deadline")
    started = time.monotonic()

    def check() -> None:
        now = time.monotonic()
        if now >= deadline or now - started >= NATIVE_CALL_SECONDS:
            raise RuntimeError("System directory call exceeded original deadline")

    def read() -> str:
        check()
        kernel = c.WinDLL("kernel32", use_last_error=True)
        function = kernel.GetSystemDirectoryW
        function.argtypes = [c.POINTER(c.c_wchar), c.c_uint32]
        function.restype = c.c_uint32
        buffer = c.create_unicode_buffer(IMAGE_BUFFER_CHARS)
        size = int(function(buffer, IMAGE_BUFFER_CHARS))
        if size == 0:
            raise native_error("GetSystemDirectoryW")
        check()
        if not 0 < size <= MAX_IMAGE_CHARS:
            raise RuntimeError("System directory exceeds retained character bound")
        directory = canonical_image(c.wstring_at(buffer, size))
        result = canonical_image(ntpath.join(directory, "conhost.exe"))
        check()
        return result

    result = logged_call(journal, "GetSystemDirectoryW", read)
    check()
    return result


def profile_pid(value: object) -> int:
    """Require the exact bounded positive PID type, including at caller boundaries."""
    if type(value) is not int or not 1 <= value < 2**32:
        raise ValueError("Invalid ownership profile PID")
    return value


def profile_record(record: Json) -> tuple[int, str, int]:
    """Admit a retained identity without trusting a basename or status alone."""
    if type(record) is not dict:
        raise ValueError("Invalid ownership profile process record")
    requested = profile_pid(record.get("requested_pid"))
    actual = profile_pid(record.get("process_id"))
    created = record.get("creation_100ns")
    if (
        requested != actual
        or record.get("status") != "available"
        or type(record.get("errors")) is not list
        or record["errors"]
        or type(created) is not int
        or not 0 < created < 2**64
    ):
        raise ValueError("Unavailable or changed ownership profile identity")
    return requested, canonical_image(record.get("image_path")), created


def profile_child(record: Json, created: int, launch: int, launch_created: int) -> None:
    """Both children require an available parent PID and non-earlier creation time."""
    parent = record.get("parent")
    if (
        type(parent) is not dict
        or parent.get("status") != "available"
        or type(parent.get("pid")) is not int
        or parent["pid"] != launch
        or created < launch_created
    ):
        raise ValueError("Unqualified child parent or creation identity")


def validate_profile(  # noqa: PLR0913, PLR0917 - independent fixed ownership expectations
    records: Sequence[Json],
    launch_pid: int,
    executor_pid: int,
    expected_launch_path: str,
    expected_base_path: str,
    expected_console_path: str,
) -> Mapping[str, int]:
    """Admit only the explicit singleton or launcher/executor/console profile."""
    launch, executor = profile_pid(launch_pid), profile_pid(executor_pid)
    launch_image = canonical_image(expected_launch_path)
    base_image = canonical_image(expected_base_path)
    console_image = canonical_image(expected_console_path)
    if not isinstance(records, (list, tuple)) or len(records) not in (
        1,
        PROFILE_PROCESS_COUNT,
    ):
        raise ValueError("Unknown ownership process profile")
    identities: dict[int, tuple[str, int]] = {}
    by_pid: dict[int, Json] = {}
    for record in records:
        pid, path, created = profile_record(record)
        if pid in identities:
            raise ValueError("Duplicate ownership profile PID")
        identities[pid], by_pid[pid] = (path, created), record
    if launch not in identities or executor not in identities:
        raise ValueError("Missing launcher or executor identity")
    if launch == executor:
        if (
            len(identities) != 1
            or identities[launch][0] != launch_image
            or launch_image != base_image
        ):
            raise ValueError("Singleton does not match both executable expectations")
        return MappingProxyType({"launcher": launch, "executor": executor})
    if len(identities) != PROFILE_PROCESS_COUNT:
        raise ValueError("Incomplete launcher/executor/console profile")
    console = (set(identities) - {launch, executor}).pop()
    if (
        identities[launch][0] != launch_image
        or identities[executor][0] != base_image
        or identities[console][0] != console_image
    ):
        raise ValueError("Unexpected full process image in ownership profile")
    for child in (executor, console):
        profile_child(
            by_pid[child], identities[child][1], launch, identities[launch][1]
        )
    return MappingProxyType(
        {"launcher": launch, "executor": executor, "console": console}
    )


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
        return logged_call(self.journal, name, operation)

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

    def observe(self) -> Json:
        """Recheck identities without closing pins or repeating the snapshot."""
        if self.closed:
            raise RuntimeError("Process diagnostic pins are already closed")
        self._check()
        for pin in self.pins:
            self._recheck(pin)
        self._check()
        return {
            "processes": [pin.value() for pin in self.pins],
            "cleanup_errors": list(self.cleanup_errors),
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
