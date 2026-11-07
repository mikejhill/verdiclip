"""Atomically contained Windows helper lifetime, without GUI waits or transport.

All polls use a zero kernel wait. A future Qt owner can schedule them with a
QTimer. ``close`` requests termination; only ``cleanup_confirmed`` proves the
owned job was empty and its handles were released. Parent death closes the sole
non-inherited job handle, so KILL_ON_JOB_CLOSE also covers pre-ready children.
"""

from __future__ import annotations

import ctypes
import ntpath
import subprocess
import sys
from collections.abc import Sequence
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Protocol

_MAX_ATTRIBUTE_BYTES = 65_536
_MAX_JOB_PROCESSES = 256
_MAX_IMAGE_CHARS = 32_768
_WAIT_TIMEOUT = 258
_RESUME_FAILED = 0xFFFFFFFF
_JOB_KILL_ON_CLOSE = 0x2000
_JOB_LIST_ATTRIBUTE = 0x0002000D
_INHERIT_HANDLES = False
_CREATE_FLAGS = 0x00000004 | 0x08000000 | 0x00080000


@dataclass(frozen=True)
class HelperCommand:
    """An explicit executable and argv, never a shell or detached relay."""

    executable: str
    arguments: tuple[str, ...]
    directory: str

    def __post_init__(self) -> None:
        """Reject relative paths, embedded terminators and oversized commands."""
        if type(self.arguments) is not tuple:
            raise ValueError("Helper arguments must be an immutable tuple")
        values = (self.executable, self.directory, *self.arguments)
        if any(type(value) is not str or "\0" in value for value in values):
            raise ValueError("Helper command values must be strings without NUL")
        if not all(
            PureWindowsPath(path).is_absolute()
            for path in (self.executable, self.directory)
        ):
            raise ValueError(
                "Helper executable and directory must be absolute Windows paths"
            )
        if (
            len(subprocess.list2cmdline((self.executable, *self.arguments)))
            >= _MAX_IMAGE_CHARS
        ):
            raise ValueError("Helper command exceeds the Windows command-line bound")

    @classmethod
    def base_python(cls, arguments: Sequence[str], directory: Path) -> HelperCommand:
        """Use the runtime's explicit base interpreter in isolated no-site mode.

        Ready verification still checks the real producer PID and kernel image;
        a filename alone is not evidence that this executable is not a launcher.
        """
        executable = getattr(sys, "_base_executable", None)
        if not isinstance(executable, str) or not executable:
            raise RuntimeError("The base Python interpreter is unavailable")
        return cls(executable, ("-I", "-S", *arguments), str(directory))


@dataclass(frozen=True)
class NativeProcess:
    """Owned native handles returned together by atomic process creation."""

    pid: int
    process: int
    thread: int


@dataclass(frozen=True)
class HelperStatus:
    """Immutable lifetime evidence; a termination request is not cleanup proof."""

    pid: int | None
    producer_verified: bool
    exit_code: int | None
    remaining_pids: tuple[int, ...] | None
    termination_requested: bool
    cleanup_confirmed: bool
    closed: bool
    failure: str | None
    cleanup_errors: tuple[str, ...]


class HelperApi(Protocol):
    """Small typed OS boundary, replaceable by deterministic lifecycle tests."""

    def create_job(self) -> int:
        """Create a non-inheritable job handle."""

    def kill_on_close(self, job: int) -> None:
        """Configure kernel cleanup before any child can be created."""

    def create(self, job: int, command: HelperCommand) -> NativeProcess:
        """Create suspended with JOB_LIST membership established atomically."""

    def resume(self, thread: int) -> None:
        """Resume the already-contained producer."""

    def image(self, process: int) -> str:
        """Read the live executable path from the kernel handle."""

    def members(self, job: int) -> tuple[int, ...]:
        """Query this exact job with a bounded process list."""

    def exit_code(self, process: int) -> int | None:
        """Return the exit code or None without waiting."""

    def terminate(self, job: int) -> None:
        """Request termination of all members, including descendants."""

    def close_handle(self, handle: int) -> None:
        """Release one owned handle; failure must remain visible."""


class OwnedHelper:
    """One native job and producer, with explicit asynchronous cleanup evidence."""

    def __init__(self, api: HelperApi | None = None) -> None:
        self._api = api if api is not None else Win32HelperApi()
        self._command: HelperCommand | None = None
        self._job: int | None = None
        self._process: NativeProcess | None = None
        self._pid: int | None = None
        self._verified = False
        self._exit: int | None = None
        self._members: tuple[int, ...] | None = None
        self._termination = False
        self._confirmed = False
        self._closed = False
        self._failure: str | None = None
        self._cleanup_errors: list[str] = []

    @property
    def status(self) -> HelperStatus:
        """Return current evidence without querying or waiting on the OS."""
        return HelperStatus(
            self._pid,
            self._verified,
            self._exit,
            self._members,
            self._termination,
            self._confirmed,
            self._closed,
            self._failure,
            tuple(self._cleanup_errors),
        )

    def start(self, command: HelperCommand) -> HelperStatus:
        """Launch once; every failure keeps containment and cleanup observable."""
        if self._command is not None or self._closed or self._termination:
            raise RuntimeError("An owned helper cannot be restarted")
        self._command = command
        try:
            self._job = self._api.create_job()
            self._api.kill_on_close(self._job)
            self._process = self._api.create(self._job, command)
            self._pid = self._process.pid
            self._api.resume(self._process.thread)
        except OSError as error:
            self._fail(f"Helper startup failed: {error}")
            self.terminate()
        finally:
            if self._process is not None:
                self._close_handle(self._process.thread)
        if self._cleanup_errors:
            self.terminate()
        return self.poll()

    def verify_ready(self, pid: int, executable: str) -> HelperStatus:
        """Require direct producer identity, job membership and kernel image."""
        if self._closed or self._termination:
            return self.status
        try:
            self._validate_ready(pid, executable)
            self._verified = True
        except OSError as error:
            self._fail(f"Helper identity failed: {error}")
            self.terminate()
        return self.poll()

    def _validate_ready(self, pid: int, executable: str) -> None:
        process, command, job = self._process, self._command, self._job
        if process is None or command is None or job is None:
            raise OSError("Ready arrived without a running helper")
        if type(pid) is not int or pid != process.pid:
            raise OSError("Ready PID is not the directly created producer")
        if type(executable) is not str:
            raise OSError("Ready executable must be an explicit path")
        expected = ntpath.normcase(ntpath.normpath(command.executable))
        actual = ntpath.normcase(ntpath.normpath(self._api.image(process.process)))
        reported = ntpath.normcase(ntpath.normpath(executable))
        if actual != expected or reported != expected:
            raise OSError("Producer executable does not match the base interpreter")
        if (
            pid not in self._api.members(job)
            or self._api.exit_code(process.process) is not None
        ):
            raise OSError("Producer is not alive in its owned job")

    def terminate(self) -> HelperStatus:
        """Request job-wide termination once, without waiting for any process."""
        if not self._termination and not self._closed:
            self._termination = True
            if self._job is not None:
                try:
                    self._api.terminate(self._job)
                except OSError as error:
                    self._cleanup_errors.append(f"TerminateJobObject: {error}")
                    # Closing the sole job handle is the independent kill path.
                    job, self._job = self._job, None
                    self._close_handle(job)
        return self.status

    def poll(self) -> HelperStatus:
        """Inspect termination with zero waits; never infer that descendants exited."""
        if self._closed or self._command is None:
            return self.status
        try:
            if self._process is not None:
                self._exit = self._api.exit_code(self._process.process)
            if self._job is not None:
                self._members = self._api.members(self._job)
        except OSError as error:
            self._cleanup_errors.append(f"Inspect owned helper: {error}")
            self._fail("Could not verify owned helper lifetime")
            self.terminate()
            # Membership can no longer be proved. Close the sole job handle
            # as a fail-closed kill path, and report unconfirmed cleanup.
            self._finish()
            return self.status
        if self._process is not None and self._exit is None:
            return self.status
        if self._process is not None and not self._termination:
            if not self._verified or self._exit != 0:
                self._fail("Helper exited before ready or with an error")
            if bool(self._members):
                self.terminate()
                return self.status
        if bool(self._members) and self._job is not None:
            return self.status
        self._finish()
        return self.status

    def close(self) -> HelperStatus:
        """Begin cleanup; poll until closed, without a blocking GUI-thread join."""
        self.terminate()
        if self._command is None:
            self._finish()
            return self.status
        return self.poll()

    def release_unconfirmed(self, reason: str) -> HelperStatus:
        """End a cleanup watchdog without pretending that process death was proved.

        Termination remains job-wide and nonblocking. Discard cached membership,
        close remaining handles once, and permanently retain the uncertainty.
        An already closed lifetime, including confirmed cleanup, is unchanged.
        """
        if self._closed:
            return self.status
        self._fail(reason)
        self._cleanup_errors.append(f"Cleanup unconfirmed: {reason}")
        self._members = None
        self.terminate()
        self._finish()
        return self.status

    def _finish(self) -> None:
        if self._process is not None:
            process, self._process = self._process, None
            self._close_handle(process.process)
        if self._job is not None:
            job, self._job = self._job, None
            self._close_handle(job)
        self._closed = True
        self._confirmed = not self._cleanup_errors and self._members in ((), None)

    def _close_handle(self, handle: int) -> None:
        try:
            self._api.close_handle(handle)
        except OSError as error:
            # Never retry an ambiguous numeric handle after a failed close.
            self._cleanup_errors.append(f"CloseHandle: {error}")

    def _fail(self, reason: str) -> None:
        if self._failure is None:
            self._failure = reason


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
        ("ids", ctypes.c_size_t * _MAX_JOB_PROCESSES),
    ]


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("reserved", wintypes.LPWSTR),
        ("desktop", wintypes.LPWSTR),
        ("title", wintypes.LPWSTR),
        ("x", wintypes.DWORD),
        ("y", wintypes.DWORD),
        ("width_pixels", wintypes.DWORD),
        ("height_pixels", wintypes.DWORD),
        ("width_chars", wintypes.DWORD),
        ("height_chars", wintypes.DWORD),
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


class Win32HelperApi:
    """Small Windows 10+ adapter; JOB_LIST closes the create/assign race."""

    def __init__(self) -> None:
        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._bind()

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
            "QueryFullProcessImageNameW": (
                [
                    wintypes.HANDLE,
                    wintypes.DWORD,
                    wintypes.LPWSTR,
                    ctypes.POINTER(wintypes.DWORD),
                ],
                wintypes.BOOL,
            ),
            "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self._kernel, name)
            function.argtypes, function.restype = arguments, result

    @staticmethod
    def _require(*, success: bool) -> None:
        if not success:
            raise ctypes.WinError(ctypes.get_last_error())

    def create_job(self) -> int:
        """NULL security attributes produce a non-inheritable job handle."""
        handle = self._kernel.CreateJobObjectW(None, None)
        self._require(success=bool(handle))
        return int(handle)

    def kill_on_close(self, job: int) -> None:
        """Set KILL_ON_JOB_CLOSE before launch, without breakaway permission."""
        limits = _ExtendedLimits()
        limits.basic.flags = _JOB_KILL_ON_CLOSE
        self._require(
            success=bool(
                self._kernel.SetInformationJobObject(
                    job, 9, ctypes.byref(limits), ctypes.sizeof(limits)
                )
            )
        )

    def create(self, job: int, command: HelperCommand) -> NativeProcess:
        """Create suspended, windowless and atomically assigned to the exact job."""
        size = ctypes.c_size_t()
        self._kernel.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        if not 0 < size.value <= _MAX_ATTRIBUTE_BYTES:
            raise OSError("Invalid process attribute list size")
        buffer = ctypes.create_string_buffer(size.value)
        attributes = ctypes.cast(buffer, ctypes.c_void_p)
        self._require(
            success=bool(
                self._kernel.InitializeProcThreadAttributeList(
                    attributes, 1, 0, ctypes.byref(size)
                )
            )
        )
        try:
            jobs = (wintypes.HANDLE * 1)(job)
            self._require(
                success=bool(
                    self._kernel.UpdateProcThreadAttribute(
                        attributes,
                        0,
                        _JOB_LIST_ATTRIBUTE,
                        ctypes.byref(jobs),
                        ctypes.sizeof(jobs),
                        None,
                        None,
                    )
                )
            )
            startup = _StartupInfoEx()
            startup.startup.cb = ctypes.sizeof(startup)
            startup.attributes = attributes
            process = _ProcessInfo()
            argv = subprocess.list2cmdline((command.executable, *command.arguments))
            self._require(
                success=bool(
                    self._kernel.CreateProcessW(
                        command.executable,
                        ctypes.create_unicode_buffer(argv),
                        None,
                        None,
                        _INHERIT_HANDLES,
                        _CREATE_FLAGS,
                        None,
                        command.directory,
                        ctypes.byref(startup),
                        ctypes.byref(process),
                    )
                )
            )
            return NativeProcess(
                int(process.pid), int(process.process), int(process.thread)
            )
        finally:
            self._kernel.DeleteProcThreadAttributeList(attributes)

    def resume(self, thread: int) -> None:
        """Resume only after JOB_LIST containment is established."""
        self._require(success=self._kernel.ResumeThread(thread) != _RESUME_FAILED)

    def image(self, process: int) -> str:
        """Read an explicitly bounded executable path using the process handle."""
        size = wintypes.DWORD(_MAX_IMAGE_CHARS)
        value = ctypes.create_unicode_buffer(size.value)
        self._require(
            success=bool(
                self._kernel.QueryFullProcessImageNameW(
                    process, 0, value, ctypes.byref(size)
                )
            )
        )
        return str(value.value)

    def members(self, job: int) -> tuple[int, ...]:
        """Query only this job; overflow is failure, never silently truncated proof."""
        info = _ProcessIds()
        self._require(
            success=bool(
                self._kernel.QueryInformationJobObject(
                    job, 3, ctypes.byref(info), ctypes.sizeof(info), None
                )
            )
        )
        if info.assigned > _MAX_JOB_PROCESSES or info.count > _MAX_JOB_PROCESSES:
            raise OSError("Owned job process list exceeded fixed bound")
        return tuple(int(info.ids[index]) for index in range(info.count))

    def exit_code(self, process: int) -> int | None:
        """Inspect process completion with a zero-timeout kernel wait."""
        result = self._kernel.WaitForSingleObject(process, 0)
        if result == _WAIT_TIMEOUT:
            return None
        self._require(success=result == 0)
        code = wintypes.DWORD()
        self._require(
            success=bool(self._kernel.GetExitCodeProcess(process, ctypes.byref(code)))
        )
        return int(code.value)

    def terminate(self, job: int) -> None:
        """Request termination of every contained process, without waiting."""
        self._require(success=bool(self._kernel.TerminateJobObject(job, 1)))

    def close_handle(self, handle: int) -> None:
        """Close exactly one owned native handle."""
        self._require(success=bool(self._kernel.CloseHandle(handle)))
