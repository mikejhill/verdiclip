"""Bounded Windows process trees for synthetic panel feasibility fixtures only."""

from __future__ import annotations

import ctypes
import subprocess
import time
from ctypes import wintypes
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


MAX_ATTRIBUTE_BYTES = 65_536
RESUME_FAILED = 0xFFFFFFFF
WAIT_TIMEOUT = 258
INHERIT_HANDLES = False


class _Limits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_int64),
        ("job_time", ctypes.c_int64),
        ("flags", wintypes.DWORD),
        ("minimum", ctypes.c_size_t),
        ("maximum", ctypes.c_size_t),
        ("active", wintypes.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority", wintypes.DWORD),
        ("scheduling", wintypes.DWORD),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", _Limits),
        ("io_counters", ctypes.c_uint64 * 6),
        ("process_memory", ctypes.c_size_t),
        ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t),
        ("peak_job_memory", ctypes.c_size_t),
    ]


class _ProcessIds(ctypes.Structure):
    _fields_ = [
        ("assigned", wintypes.DWORD),
        ("count", wintypes.DWORD),
        ("ids", ctypes.c_size_t * 256),
    ]


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("reserved", wintypes.LPWSTR),
        ("desktop", wintypes.LPWSTR),
        ("title", wintypes.LPWSTR),
        ("x", wintypes.DWORD),
        ("y", wintypes.DWORD),
        ("width", wintypes.DWORD),
        ("height", wintypes.DWORD),
        ("x_chars", wintypes.DWORD),
        ("y_chars", wintypes.DWORD),
        ("fill", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("show", wintypes.WORD),
        ("reserved_size", wintypes.WORD),
        ("reserved_bytes", ctypes.c_void_p),
        ("stdin", wintypes.HANDLE),
        ("stdout", wintypes.HANDLE),
        ("stderr", wintypes.HANDLE),
    ]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [("startup", _StartupInfo), ("attributes", ctypes.c_void_p)]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [
        ("process", wintypes.HANDLE),
        ("thread", wintypes.HANDLE),
        ("pid", wintypes.DWORD),
        ("tid", wintypes.DWORD),
    ]


class OwnedJob:
    """Atomically contain Windows-2025 children before any child code runs."""

    def __init__(self) -> None:
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self._bind()
        self.handle = self.api.CreateJobObjectW(None, None)
        self.processes: list[int] = []
        self.started: list[int] = []
        self._require(success=bool(self.handle))
        limits = _ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        try:
            self._require(
                success=bool(
                    self.api.SetInformationJobObject(
                        self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
                    )
                )
            )
        except BaseException:
            self.api.CloseHandle(self.handle)
            raise

    def _bind(self) -> None:
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            "SetInformationJobObject": (
                [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD],
                wintypes.BOOL,
            ),
            "QueryInformationJobObject": (
                [
                    wintypes.HANDLE,
                    ctypes.c_int,
                    ctypes.c_void_p,
                    wintypes.DWORD,
                    ctypes.c_void_p,
                ],
                wintypes.BOOL,
            ),
            "InitializeProcThreadAttributeList": (
                [
                    ctypes.c_void_p,
                    wintypes.DWORD,
                    wintypes.DWORD,
                    ctypes.POINTER(ctypes.c_size_t),
                ],
                wintypes.BOOL,
            ),
            "UpdateProcThreadAttribute": (
                [
                    ctypes.c_void_p,
                    wintypes.DWORD,
                    ctypes.c_size_t,
                    ctypes.c_void_p,
                    ctypes.c_size_t,
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                ],
                wintypes.BOOL,
            ),
            "DeleteProcThreadAttributeList": ([ctypes.c_void_p], None),
            "CreateProcessW": (
                [
                    wintypes.LPCWSTR,
                    wintypes.LPWSTR,
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                    wintypes.BOOL,
                    wintypes.DWORD,
                    ctypes.c_void_p,
                    wintypes.LPCWSTR,
                    ctypes.POINTER(_StartupInfoEx),
                    ctypes.POINTER(_ProcessInfo),
                ],
                wintypes.BOOL,
            ),
            "ResumeThread": ([wintypes.HANDLE], wintypes.DWORD),
            "WaitForSingleObject": ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            "GetExitCodeProcess": (
                [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)],
                wintypes.BOOL,
            ),
            "TerminateProcess": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes = arguments
            function.restype = result

    @staticmethod
    def _require(*, success: bool) -> None:
        if not success:
            raise ctypes.WinError(ctypes.get_last_error())

    def start(self, command: Sequence[str], cwd: Path) -> tuple[int, int]:
        """Atomically create in this job; there is no uncontained startup gap."""
        size = ctypes.c_size_t()
        self.api.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        if not size.value or size.value > MAX_ATTRIBUTE_BYTES:
            raise RuntimeError("Invalid process attribute list size")
        buffer = ctypes.create_string_buffer(size.value)
        attributes = ctypes.cast(buffer, ctypes.c_void_p)
        self._require(
            success=bool(
                self.api.InitializeProcThreadAttributeList(
                    attributes, 1, 0, ctypes.byref(size)
                )
            )
        )
        try:
            jobs = (wintypes.HANDLE * 1)(self.handle)
            self._require(
                success=bool(
                    self.api.UpdateProcThreadAttribute(
                        attributes,
                        0,
                        0x0002000D,
                        ctypes.byref(jobs),
                        ctypes.sizeof(jobs),
                        None,
                        None,
                    )
                )
            )  # JOB_LIST, Windows 10+
            startup = _StartupInfoEx()
            startup.startup.cb = ctypes.sizeof(startup)
            startup.attributes = attributes
            process = _ProcessInfo()
            self._require(
                success=bool(
                    self.api.CreateProcessW(
                        command[0],
                        ctypes.create_unicode_buffer(subprocess.list2cmdline(command)),
                        None,
                        None,
                        INHERIT_HANDLES,
                        0x00000004
                        | 0x08000000
                        | 0x00080000,  # SUSPENDED | NO_WINDOW | EXTENDED
                        None,
                        str(cwd),
                        ctypes.byref(startup),
                        ctypes.byref(process),
                    )
                )
            )
            # Membership is established by the OS before CreateProcess returns.
            self.processes.append(process.process)
            self.started.append(process.pid)
            try:
                self._require(
                    success=self.api.ResumeThread(process.thread) != RESUME_FAILED
                )
            finally:
                self.api.CloseHandle(process.thread)
            return int(process.pid), int(process.process)
        finally:
            self.api.DeleteProcThreadAttributeList(attributes)

    def pids(self) -> list[int]:
        """Read only the process identities contained by this exact job."""
        info = _ProcessIds()
        self._require(
            success=bool(
                self.api.QueryInformationJobObject(
                    self.handle, 3, ctypes.byref(info), ctypes.sizeof(info), None
                )
            )
        )
        if info.count > len(info.ids):
            raise RuntimeError("Owned job process list exceeded fixed bound")
        return [int(info.ids[index]) for index in range(info.count)]

    def wait(self, process: int, seconds: float) -> int | None:
        """Return a direct process exit code, or None at the fixed deadline."""
        result = self.api.WaitForSingleObject(process, int(seconds * 1000))
        if result == WAIT_TIMEOUT:
            return None
        self._require(success=result == 0)
        code = wintypes.DWORD()
        self._require(
            success=bool(self.api.GetExitCodeProcess(process, ctypes.byref(code)))
        )
        return code.value

    def _wait_terminated(self, process: int) -> None:
        if self.wait(process, 2) is None:
            raise RuntimeError("Owned direct process did not terminate")

    def close(self) -> dict[str, object]:
        """Kill before querying, and preserve every cleanup error separately."""
        errors = []
        remaining: list[int] | None = None
        try:
            try:
                self._require(success=bool(self.api.TerminateJobObject(self.handle, 1)))
            except Exception as error:  # noqa: BLE001 - cleanup must continue
                errors.append({"operation": "terminate_job", "error": repr(error)})
            for process in self.processes:
                try:
                    self._wait_terminated(process)
                except Exception as error:  # noqa: BLE001 - preserve all cleanup results
                    errors.append({"operation": "wait_process", "error": repr(error)})
            try:
                deadline = time.monotonic() + 3
                remaining = self.pids()
                while remaining and time.monotonic() < deadline:
                    time.sleep(0.025)
                    remaining = self.pids()
            except Exception as error:  # noqa: BLE001 - still close all handles
                errors.append({"operation": "query_job", "error": repr(error)})
        finally:
            errors.extend(
                {"operation": "close_handle", "handle": process}
                for process in [*self.processes, self.handle]
                if not self.api.CloseHandle(process)
            )
        return {
            "started_pids": self.started,
            "remaining_pids": remaining,
            "errors": errors,
            "confirmed": remaining == [] and not errors,
        }
