"""Portable ownership tests; real process lifetime requires the native smoke gate."""

import ctypes
import ctypes.wintypes
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from verdiclip.platform import owned_helper as owned
from verdiclip.platform.owned_helper import (
    HelperCommand,
    NativeProcess,
    OwnedHelper,
    Win32HelperApi,
)

_EXE = r"C:\Python\python.exe"
_COMMAND = HelperCommand(_EXE, ("-I", "-S", "worker.py"), r"C:\private session")


class FakeApi:
    """Record lifecycle operations and let tests independently acknowledge death."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail: set[str] = set()
        self.live: tuple[int, ...] = (101,)
        self.code: int | None = None
        self.executable = _EXE
        self.closed: list[int] = []

    def _call(self, name: str) -> None:
        self.calls.append(name)
        if name in self.fail:
            raise OSError(name)

    def create_job(self) -> int:
        """Create one non-inherited fake handle."""
        self._call("create_job")
        return 10

    def kill_on_close(self, job: int) -> None:
        """Require the created job."""
        assert job == 10
        self._call("kill_on_close")

    def create(self, job: int, command: HelperCommand) -> NativeProcess:
        """Creation is already contained and suspended."""
        assert job == 10
        assert command == _COMMAND
        self._call("create")
        return NativeProcess(101, 20, 21)

    def resume(self, thread: int) -> None:
        """Execution begins only after setup."""
        assert thread == 21
        self._call("resume")

    def image(self, process: int) -> str:
        """Return a kernel image independently of the ready message."""
        assert process == 20
        self._call("image")
        return self.executable

    def members(self, job: int) -> tuple[int, ...]:
        """Return the entire owned process tree."""
        assert job == 10
        self._call("members")
        return self.live

    def exit_code(self, process: int) -> int | None:
        """No wait or sleep is hidden here."""
        assert process == 20
        self._call("exit_code")
        return self.code

    def terminate(self, job: int) -> None:
        """Termination is a request, deliberately not an immediate acknowledgment."""
        assert job == 10
        self._call("terminate")

    def close_handle(self, handle: int) -> None:
        """Record each handle once, including failures."""
        self.closed.append(handle)
        self._call(f"close_{handle}")


def running() -> tuple[OwnedHelper, FakeApi]:
    """Launch a fake helper without confusing creation with verified readiness."""
    api = FakeApi()
    helper = OwnedHelper(api)
    status = helper.start(_COMMAND)
    assert not status.producer_verified
    assert status.pid == 101
    assert not status.cleanup_confirmed
    assert api.calls[:4] == ["create_job", "kill_on_close", "create", "resume"]
    assert api.closed == [21]
    return helper, api


def test_ready_and_nonblocking_cleanup() -> None:
    """Do not confirm cleanup until both producer and descendant membership end."""
    helper, api = running()
    assert helper.verify_ready(101, _EXE.lower()).producer_verified
    api.live = (101, 102)
    status = helper.close()
    assert status.termination_requested
    assert not status.closed
    assert not status.cleanup_confirmed
    assert status.remaining_pids == (101, 102)
    api.code = 1
    api.live = (102,)
    assert not helper.poll().cleanup_confirmed
    api.live = ()
    status = helper.poll()
    assert status.closed
    assert status.cleanup_confirmed
    assert api.closed == [21, 20, 10]
    calls = list(api.calls)
    assert helper.close() == status
    assert helper.verify_ready(101, _EXE) == status
    assert api.calls == calls


@pytest.mark.parametrize(
    "operation", ["create_job", "kill_on_close", "create", "resume"]
)
def test_startup_failures_remain_contained(operation: str) -> None:
    """Failure at each startup boundary terminates the exact job and keeps evidence."""
    api = FakeApi()
    api.fail.add(operation)
    if operation != "resume":
        api.live = ()
    helper = OwnedHelper(api)
    status = helper.start(_COMMAND)
    assert status.failure is not None
    assert "startup failed" in status.failure
    if operation == "resume":
        assert not status.cleanup_confirmed
        assert api.closed == [21]
        api.code, api.live = 1, ()
        status = helper.poll()
    assert status.closed
    assert status.cleanup_confirmed
    assert api.calls.count("terminate") == (0 if operation == "create_job" else 1)
    assert api.closed == (
        []
        if operation == "create_job"
        else [21, 20, 10]
        if operation == "resume"
        else [10]
    )


@pytest.mark.parametrize("pid", [102, True, 101.0])
def test_launcher_or_invalid_ready_pid_is_rejected(pid: object) -> None:
    """A child of a launcher cannot masquerade as the directly owned producer."""
    helper, api = running()
    status = helper.verify_ready(cast("int", pid), _EXE)
    assert not status.producer_verified
    assert status.termination_requested
    assert api.calls.count("terminate") == 1


@pytest.mark.parametrize("reported", [r"C:\Other\python.exe", None])
def test_wrong_reported_image_is_rejected(reported: object) -> None:
    """An untrusted ready path cannot override kernel identity."""
    helper, _ = running()
    assert helper.verify_ready(101, cast("str", reported)).termination_requested


def test_kernel_image_mismatch_and_missing_membership() -> None:
    """Reported identity is insufficient without matching live kernel evidence."""
    helper, api = running()
    api.executable = r"C:\uv\launcher.exe"
    assert helper.verify_ready(101, _EXE).termination_requested
    helper, api = running()
    api.live = (102,)
    assert helper.verify_ready(101, _EXE).termination_requested
    helper, api = running()
    api.code = 0
    assert helper.verify_ready(101, _EXE).termination_requested


def test_known_exit_kills_remaining_descendants() -> None:
    """A clean direct-process exit is not proof that its children are gone."""
    helper, api = running()
    helper.verify_ready(101, _EXE)
    api.code, api.live = 0, (102,)
    status = helper.poll()
    assert status.termination_requested
    assert not status.cleanup_confirmed
    api.live = ()
    assert helper.poll().cleanup_confirmed


@pytest.mark.parametrize("ready", [True, False])
def test_natural_exit_before_and_after_ready(*, ready: bool) -> None:
    """Exit before ready is startup failure, even if the process returns zero."""
    helper, api = running()
    if ready:
        helper.verify_ready(101, _EXE)
    api.code, api.live = 0, ()
    status = helper.poll()
    assert status.cleanup_confirmed
    assert (status.failure is None) == ready


@pytest.mark.parametrize("operation", ["members", "exit_code", "image"])
def test_native_inspection_failure_is_not_cleanup_proof(operation: str) -> None:
    """Failed queries trigger termination without claiming process-tree death."""
    helper, api = running()
    api.fail.add(operation)
    status = helper.verify_ready(101, _EXE) if operation == "image" else helper.poll()
    assert status.termination_requested
    assert not status.cleanup_confirmed
    api.fail.clear()
    api.code, api.live = 1, ()
    status = helper.poll()
    assert status.closed
    assert status.cleanup_confirmed == (operation == "image")


@pytest.mark.parametrize("handle", [10, 20, 21])
def test_close_failure_remains_visible_without_handle_retry(handle: int) -> None:
    """Do not silently claim cleanup or retry a potentially stale numeric handle."""
    api = FakeApi()
    api.fail.add(f"close_{handle}")
    helper = OwnedHelper(api)
    helper.start(_COMMAND)
    api.code, api.live = 1, ()
    status = helper.close()
    assert status.closed
    assert not status.cleanup_confirmed
    assert status.cleanup_errors
    helper.close()
    assert api.closed.count(handle) == 1


def test_failed_termination_closes_job_as_independent_kill_path() -> None:
    """KILL_ON_JOB_CLOSE remains usable when explicit termination reports failure."""
    helper, api = running()
    api.fail.add("terminate")
    status = helper.close()
    assert 10 in api.closed
    assert not status.cleanup_confirmed
    api.code = 1
    status = helper.poll()
    assert status.closed
    assert not status.cleanup_confirmed
    assert api.closed == [21, 10, 20]


def test_cannot_restart_or_ready_before_launch() -> None:
    """One lifetime cannot accidentally become a persistent reusable helper."""
    helper, _ = running()
    with pytest.raises(RuntimeError, match="restarted"):
        helper.start(_COMMAND)
    idle = OwnedHelper(FakeApi())
    assert not idle.poll().closed
    status = idle.verify_ready(101, _EXE)
    assert status.termination_requested
    with pytest.raises(RuntimeError, match="restarted"):
        idle.start(_COMMAND)
    assert idle.close().cleanup_confirmed
    closed = OwnedHelper(FakeApi())
    assert closed.close().cleanup_confirmed
    with pytest.raises(RuntimeError, match="restarted"):
        closed.start(_COMMAND)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("executable", "python.exe"),
        ("directory", "relative"),
        ("arguments", ["-I"]),
        ("arguments", ("bad\0argument",)),
        ("arguments", (1,)),
        ("arguments", ("x" * 32768,)),
        ("directory", None),
    ],
)
def test_invalid_commands(field: str, value: object) -> None:
    """Reject mutable, malformed or unbounded launch descriptions."""
    with pytest.raises(ValueError, match="Helper"):
        replace(_COMMAND, **{field: value})


def test_base_interpreter_is_explicit_and_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No missing-base fallback is allowed to introduce a detached relay."""
    monkeypatch.setattr(owned.sys, "_base_executable", _EXE)
    command = HelperCommand.base_python(("worker.py",), Path(r"C:\private session"))
    assert command == _COMMAND
    monkeypatch.delattr(owned.sys, "_base_executable")
    with pytest.raises(RuntimeError, match="unavailable"):
        HelperCommand.base_python((), Path(r"C:\private session"))


class FakeFunction:
    """ctypes-like callable allowing the production adapter to bind signatures."""

    def __init__(self, call: Callable[..., int]) -> None:
        self.call = call
        self.argtypes: object = None
        self.restype: object = None

    def __call__(self, *arguments: object) -> int:
        """Dispatch the recorded fake export."""
        return self.call(*arguments)


class FakeKernel:
    """Exercise the actual ABI adapter while substituting only WinDLL functions."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.fail: str | None = None
        self.size = 512
        self.count = 1
        self.wait = 258
        self.flags = 0
        self.job_attribute = 0
        self.inherited: object = None

    def __getattr__(self, name: str) -> FakeFunction:
        """Create a signature-bindable fake export on demand."""
        function = FakeFunction(lambda *arguments: self.invoke(name, arguments))
        setattr(self, name, function)
        return function

    def invoke(self, name: str, args: tuple[object, ...]) -> int:
        """Fill the few native out-parameters needed by the ownership primitive."""
        self.calls.append((name, args))
        if self.fail == name:
            return 0xFFFFFFFF if name == "ResumeThread" else 0
        if name == "CreateJobObjectW":
            assert args == (None, None)
            return 10
        if name == "SetInformationJobObject":
            info = ctypes.cast(
                cast("ctypes.c_void_p", args[2]), ctypes.POINTER(owned._ExtendedLimits)
            ).contents
            self.flags = int(info.basic.flags)
        elif name == "InitializeProcThreadAttributeList":
            ctypes.cast(
                cast("ctypes.c_void_p", args[3]), ctypes.POINTER(ctypes.c_size_t)
            ).contents.value = self.size
            return int(args[0] is not None)
        elif name == "UpdateProcThreadAttribute":
            self.job_attribute = cast("int", args[2])
            jobs = ctypes.cast(
                cast("ctypes.c_void_p", args[3]), ctypes.POINTER(ctypes.wintypes.HANDLE)
            )
            assert jobs[0] == 10
        elif name == "CreateProcessW":
            self.inherited = args[4]
            assert args[5] == 0x08080004
            startup = ctypes.cast(
                cast("ctypes.c_void_p", args[8]), ctypes.POINTER(owned._StartupInfoEx)
            ).contents
            assert startup.startup.cb == ctypes.sizeof(owned._StartupInfoEx)
            assert startup.attributes
            info = ctypes.cast(
                cast("ctypes.c_void_p", args[9]), ctypes.POINTER(owned._ProcessInfo)
            ).contents
            info.pid, info.process, info.thread = 101, 20, 21
        elif name == "QueryInformationJobObject":
            info = ctypes.cast(
                cast("ctypes.c_void_p", args[2]), ctypes.POINTER(owned._ProcessIds)
            ).contents
            info.assigned, info.count, info.ids[0] = self.count, self.count, 101
        elif name == "WaitForSingleObject":
            assert args[1] == 0
            return self.wait
        elif name == "GetExitCodeProcess":
            ctypes.cast(
                cast("ctypes.c_void_p", args[1]), ctypes.POINTER(ctypes.wintypes.DWORD)
            ).contents.value = 7
        else:
            self.fill_image(name, args)
        return 1

    @staticmethod
    def fill_image(name: str, args: tuple[object, ...]) -> None:
        """Fill a bounded image path when that export is requested."""
        if name == "QueryFullProcessImageNameW":
            buffer = cast("ctypes.Array[ctypes.c_wchar]", args[2])
            buffer.value = _EXE


@pytest.fixture
def native_api(monkeypatch: pytest.MonkeyPatch) -> tuple[Win32HelperApi, FakeKernel]:
    """Replace unavailable Windows exports without replacing production logic."""
    kernel = FakeKernel()
    monkeypatch.setattr(
        ctypes, "WinDLL", lambda *_args, **_kwargs: kernel, raising=False
    )
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(
        ctypes,
        "WinError",
        lambda code: OSError(code, "fake native error"),
        raising=False,
    )
    return Win32HelperApi(), kernel


def test_native_atomic_launch_flags_and_zero_wait(
    native_api: tuple[Win32HelperApi, FakeKernel],
) -> None:
    """Assert OS parameters implementing containment and nonblocking operation."""
    api, kernel = native_api
    job = api.create_job()
    api.kill_on_close(job)
    process = api.create(job, _COMMAND)
    assert process == NativeProcess(101, 20, 21)
    assert kernel.flags == 0x2000
    assert kernel.job_attribute == 0x0002000D
    assert kernel.inherited is False
    api.resume(process.thread)
    assert api.image(process.process) == _EXE
    assert api.members(job) == (101,)
    assert api.exit_code(process.process) is None
    kernel.wait = 0
    assert api.exit_code(process.process) == 7
    api.terminate(job)
    api.close_handle(job)
    names = [name for name, _ in kernel.calls]
    assert (
        names.index("UpdateProcThreadAttribute")
        < names.index("CreateProcessW")
        < names.index("ResumeThread")
    )
    assert names.count("DeleteProcThreadAttributeList") == 1


@pytest.mark.parametrize("size", [0, 65_537])
def test_native_attribute_size_bound(
    native_api: tuple[Win32HelperApi, FakeKernel], size: int
) -> None:
    """Reject invalid allocation sizes before native process creation."""
    api, kernel = native_api
    kernel.size = size
    with pytest.raises(OSError, match="attribute list size"):
        api.create(10, _COMMAND)
    assert "CreateProcessW" not in [name for name, _ in kernel.calls]


@pytest.mark.parametrize("operation", ["UpdateProcThreadAttribute", "CreateProcessW"])
def test_native_failed_launch_deletes_attributes(
    native_api: tuple[Win32HelperApi, FakeKernel], operation: str
) -> None:
    """Every post-initialization launch failure releases its attribute list."""
    api, kernel = native_api
    kernel.fail = operation
    with pytest.raises(OSError, match="fake native error"):
        api.create(10, _COMMAND)
    assert [name for name, _ in kernel.calls][-1] == "DeleteProcThreadAttributeList"


def test_native_job_overflow_and_wait_error(
    native_api: tuple[Win32HelperApi, FakeKernel],
) -> None:
    """Incomplete membership or failed waits cannot establish cleanup."""
    api, kernel = native_api
    kernel.count = 257
    with pytest.raises(OSError, match="fixed bound"):
        api.members(10)
    kernel.wait = 0xFFFFFFFF
    with pytest.raises(OSError, match="fake native error"):
        api.exit_code(20)


@pytest.mark.parametrize(
    ("exit_code", "members"),
    [(None, (101,)), (0, (102,)), (None, ())],
)
def test_watchdog_releases_pending_or_stale_membership(
    exit_code: int | None, members: tuple[int, ...]
) -> None:
    """A stalled process or stale empty membership cannot retain handles forever."""
    helper, api = running()
    helper.verify_ready(101, _EXE)
    api.code, api.live = exit_code, members
    pending = helper.close()
    assert not pending.closed
    assert not pending.cleanup_confirmed
    status = helper.release_unconfirmed("Cleanup watchdog expired")
    assert status.closed
    assert status.termination_requested
    assert not status.cleanup_confirmed
    assert status.remaining_pids is None
    assert status.failure == "Cleanup watchdog expired"
    assert status.cleanup_errors == ("Cleanup unconfirmed: Cleanup watchdog expired",)
    assert api.closed == [21, 20, 10]
    assert api.calls.count("terminate") == 1
    calls = list(api.calls)
    assert helper.release_unconfirmed("Another deadline") == status
    assert helper.poll() == status
    assert helper.close() == status
    assert helper.terminate() == status
    assert helper.verify_ready(101, _EXE) == status
    assert api.calls == calls


@pytest.mark.parametrize("operation", ["terminate", "close_20", "close_10"])
def test_watchdog_retains_native_release_errors(operation: str) -> None:
    """Failed kill or close calls remain visible and are never retried by polling."""
    helper, api = running()
    api.fail.add(operation)
    status = helper.release_unconfirmed("Cleanup deadline")
    assert status.closed
    assert not status.cleanup_confirmed
    assert status.termination_requested
    assert status.failure == "Cleanup deadline"
    assert len(status.cleanup_errors) == 2
    assert sorted(api.closed) == [10, 20, 21]
    calls = list(api.calls)
    assert helper.release_unconfirmed("Again") == status
    assert helper.poll() == status
    assert helper.close() == status
    assert api.calls == calls


def test_watchdog_does_not_downgrade_confirmed_cleanup() -> None:
    """A delayed watchdog callback cannot replace an already confirmed outcome."""
    helper, api = running()
    helper.verify_ready(101, _EXE)
    api.code, api.live = 0, ()
    status = helper.poll()
    assert status.cleanup_confirmed
    assert status.closed
    calls = list(api.calls)
    assert helper.release_unconfirmed("Late timer") == status
    assert api.calls == calls


def test_watchdog_preserves_earlier_failure_and_handles_no_launch() -> None:
    """Retain the original failure while recording the later cleanup uncertainty."""
    helper, api = running()
    helper.verify_ready(999, _EXE)
    earlier = helper.status.failure
    status = helper.release_unconfirmed("Cleanup deadline")
    assert status.failure == earlier
    assert status.cleanup_errors == ("Cleanup unconfirmed: Cleanup deadline",)
    assert api.calls.count("terminate") == 1
    untouched = FakeApi()
    idle = OwnedHelper(untouched)
    result = idle.release_unconfirmed("Startup deadline")
    assert result.closed
    assert not result.cleanup_confirmed
    assert idle.close() == result
    assert untouched.calls == []
    with pytest.raises(RuntimeError, match="restarted"):
        idle.start(_COMMAND)
