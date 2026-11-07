"""Portable ABI and deadline contracts, not native process-tree qualification."""

from __future__ import annotations

import ctypes as c
import json
from collections.abc import Callable
from functools import partial
from typing import Any
from unittest.mock import Mock

import pytest
from scripts import observe_process_diagnostics as diagnostic


class Kernel:
    """Fake documented Win32 calls with real ctypes output-buffer writes."""

    def __init__(self) -> None:
        self.now = 1.0
        self.last_error = 0
        self.entries = [(11, 1, "secret-unretained-name"), (22, 11, "also-unretained")]
        self.index = 0
        self.fail: set[tuple[str, int]] = set()
        self.exceptions: dict[tuple[str, int], BaseException] = {}
        self.paths: dict[int, str] = {}
        self.created: dict[int, int] = {}
        self.actual_pids: dict[int, int] = {}
        self.exited: set[int] = set()
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.advance: dict[str, float] = {}
        self.on_enumerated: Callable[[], None] = lambda: None
        self.functions = {
            name: Mock(side_effect=partial(self.invoke, name))
            for name in (
                "OpenProcess",
                "GetProcessId",
                "GetProcessTimes",
                "WaitForSingleObject",
                "QueryFullProcessImageNameW",
                "CreateToolhelp32Snapshot",
                "Process32FirstW",
                "Process32NextW",
                "CloseHandle",
            )
        }

    def __getattr__(self, name: str) -> Mock:
        """Expose only functions explicitly defined by the fake ABI."""
        return self.functions[name]

    def invoke(self, name: str, *args: Any) -> int:  # noqa: ANN401, C901, PLR0911, PLR0912 - explicit heterogeneous Win32 ABI
        """Write the same output pointer types used by the real native functions."""
        self.calls.append((name, args))
        self.now += self.advance.get(name, 0.0)
        if name == "OpenProcess":
            assert args[:2] == (0x00101000, 0)
            pid = int(args[2])
        elif name == "CreateToolhelp32Snapshot":
            assert args == (0x00000002, 0)
            pid = 0
        else:
            pid = int(args[0]) - 1000 if args[0] >= 1000 else 0
        key = (name, pid)
        if key in self.exceptions:
            raise self.exceptions[key]
        if key in self.fail:
            self.last_error = 5
            if name == "CreateToolhelp32Snapshot":
                return diagnostic.INVALID_HANDLE_VALUE or -1
            return 0xFFFFFFFF if name == "WaitForSingleObject" else 0
        if name == "OpenProcess":
            return pid + 1000
        if name == "GetProcessId":
            return self.actual_pids.get(pid, pid)
        if name == "GetProcessTimes":
            for pointer, value in zip(
                args[1:], (self.created.get(pid, 100 + pid), 0, 0, 0), strict=True
            ):
                c.cast(pointer, c.POINTER(c.c_uint64))[0] = value
            return 1
        if name == "WaitForSingleObject":
            assert args[1] == 0
            return 0 if pid in self.exited else diagnostic.WAIT_TIMEOUT
        if name == "QueryFullProcessImageNameW":
            assert args[1] == 0
            count = c.cast(args[3], c.POINTER(c.c_uint32))
            assert count[0] == 32768
            path = self.paths.get(pid, rf"C:\owned\process-{pid}.exe")
            args[2].value = path
            count[0] = len(path)
            return 1
        if name == "CreateToolhelp32Snapshot":
            return 900
        if name in ("Process32FirstW", "Process32NextW"):
            assert args[0] == 900
            entry = c.cast(args[1], c.POINTER(diagnostic.ProcessEntry32W)).contents
            assert entry.dwSize == c.sizeof(diagnostic.ProcessEntry32W)
            self.index = 0 if name == "Process32FirstW" else self.index + 1
            if self.index >= len(self.entries):
                self.last_error = 18
                return 0
            process, parent, secret = self.entries[self.index]
            entry.th32ProcessID, entry.th32ParentProcessID = process, parent
            for index, char in enumerate(secret):
                entry.szExeFile[index] = ord(char)
            return 1
        assert name == "CloseHandle"
        if args[0] == 900:
            self.on_enumerated()
        return 1


class Journal:
    """Failing log seams do not replace or hide a native operation result."""

    def __init__(self) -> None:
        self.names: list[str] = []
        self.before: set[str] = set()
        self.after: set[str] = set()
        self.mask_error = False

    def call[T](self, name: str, operation: Callable[[], T]) -> T:
        """Log exactly one complete operation and optionally simulate disk failure."""
        self.names.append(name)
        assert name.startswith("topology.")
        if name in self.before:
            raise RuntimeError("journal before failure")
        try:
            value = operation()
        except BaseException:
            if self.mask_error:
                raise RuntimeError("journal masked failure") from None
            raise
        if name in self.after:
            raise RuntimeError("journal after failure")
        return value


@pytest.fixture
def kernel(monkeypatch: pytest.MonkeyPatch) -> Kernel:
    """No real native DLL, process, snapshot, or job is opened by these tests."""
    value = Kernel()

    def dll(name: str, *, use_last_error: bool) -> Kernel:
        assert (name, use_last_error) == ("kernel32", True)
        return value

    monkeypatch.setattr(diagnostic.c, "WinDLL", dll, raising=False)
    monkeypatch.setattr(
        diagnostic.c, "get_last_error", lambda: value.last_error, raising=False
    )
    monkeypatch.setattr(diagnostic.time, "monotonic", lambda: value.now)
    return value


def test_toolhelp_structure_is_documented_pointer_width_abi() -> None:
    """Windows WCHAR remains 16 bits and the heap field is ULONG_PTR, not DWORD."""
    fields = {item[0]: item[1] for item in diagnostic.ProcessEntry32W._fields_}
    assert fields["th32DefaultHeapID"] is c.c_size_t
    assert c.sizeof(fields["szExeFile"]) == 520
    if c.sizeof(c.c_void_p) == 8:
        assert diagnostic.ProcessEntry32W.th32DefaultHeapID.offset == 16
        assert diagnostic.ProcessEntry32W.th32ParentProcessID.offset == 32
        assert c.sizeof(diagnostic.ProcessEntry32W) == 568
    else:
        assert diagnostic.ProcessEntry32W.th32DefaultHeapID.offset == 12
        assert diagnostic.ProcessEntry32W.th32ParentProcessID.offset == 24
        assert c.sizeof(diagnostic.ProcessEntry32W) == 556


def test_exact_pins_single_snapshot_and_detached_bounded_results(
    kernel: Kernel,
) -> None:
    """Only requested metadata and parent PID hints survive one native snapshot."""
    kernel.entries.insert(1, (999, 888, "unrelated-sensitive-process-name"))
    journal = Journal()
    pins = diagnostic.PinnedProcesses((11, 22), 10.0, journal)
    result = pins.collect()
    assert result["enumeration"] == {
        "started_monotonic": 1.0,
        "finished_monotonic": 1.0,
        "entries": 3,
        "complete": True,
        "error": None,
    }
    assert [value["requested_pid"] for value in result["processes"]] == [11, 22]
    assert [value["process_id"] for value in result["processes"]] == [11, 22]
    assert [value["creation_100ns"] for value in result["processes"]] == [111, 122]
    assert [value["parent"] for value in result["processes"]] == [
        {"status": "available", "pid": 1},
        {"status": "available", "pid": 11},
    ]
    assert "999" not in json.dumps(result)
    assert "unretained" not in json.dumps(result)
    assert "sensitive-process" not in json.dumps(result)
    assert "handle" not in json.dumps(result)
    assert journal.names.count("topology.Toolhelp.enumerate") == 1
    kernel.CreateToolhelp32Snapshot.assert_called_once_with(2, 0)
    assert kernel.CloseHandle.call_args_list[0].args == (900,)
    result["processes"][0]["parent"]["pid"] = 999
    final = pins.finish()
    assert final["processes"][0]["parent"]["pid"] == 1
    assert final["cleanup_errors"] == []
    assert [call.args[0] for call in kernel.CloseHandle.call_args_list] == [
        900,
        1011,
        1022,
    ]
    final["processes"].clear()
    assert len(pins.finish()["processes"]) == 2
    assert pins.close() == []
    assert kernel.CloseHandle.call_count == 3
    with pytest.raises(RuntimeError, match="one-shot"):
        pins.collect()


@pytest.mark.parametrize(
    "pids", [(), tuple(range(1, 10)), (11, 11), (False,), (0,), (-1,), (2**32,), (1.0,)]
)
def test_invalid_requested_pids_never_open_anything(
    kernel: Kernel, pids: tuple[int, ...]
) -> None:
    """No inferred, duplicated, overflowed, or ninth process is ever inspected."""
    with pytest.raises(ValueError, match="Invalid"):
        diagnostic.PinnedProcesses(pids, 10.0, Journal())
    assert kernel.calls == []


@pytest.mark.parametrize("deadline", [float("nan"), float("inf"), True])
def test_nonfinite_or_bool_deadline_is_not_admitted(
    kernel: Kernel, deadline: float
) -> None:
    """The constructor cannot invent a usable bound from invalid deadline data."""
    with pytest.raises(ValueError, match="Invalid"):
        diagnostic.PinnedProcesses((11,), deadline, Journal())
    assert kernel.calls == []


@pytest.mark.parametrize(
    "api",
    [
        "OpenProcess",
        "GetProcessId",
        "GetProcessTimes",
        "WaitForSingleObject",
        "QueryFullProcessImageNameW",
    ],
)
def test_one_process_api_failure_preserves_other_results(
    kernel: Kernel, api: str
) -> None:
    """A failed requested PID is unavailable without discarding a healthy neighbor."""
    kernel.fail.add((api, 11))
    pins = diagnostic.PinnedProcesses((11, 22), 10.0, Journal())
    result = pins.collect()
    bad, good = result["processes"]
    assert bad["requested_pid"] == 11
    assert bad["status"] == "unavailable"
    assert bad["parent"] == {"status": "unavailable"}
    assert good["status"] == "available"
    assert good["parent"]["pid"] == 11
    assert all(len(error) <= 256 for error in bad["errors"])
    pins.finish()
    closed = [call.args[0] for call in kernel.CloseHandle.call_args_list]
    assert 1022 in closed
    assert (1011 in closed) is (api != "OpenProcess")


@pytest.mark.parametrize("case", ["missing", "exited", "reused", "wrong-id"])
def test_parent_hint_cannot_outlive_pinned_identity(kernel: Kernel, case: str) -> None:
    """Missing snapshot entries and post-enumeration lifetime changes stay unknown."""
    pins = diagnostic.PinnedProcesses((11, 22), 10.0, Journal())
    if case == "missing":
        kernel.entries = [(22, 11, "not-retained")]
    elif case == "exited":
        kernel.on_enumerated = lambda: kernel.exited.add(11)
    elif case == "reused":
        kernel.on_enumerated = lambda: kernel.created.update({11: 9999})
    else:
        kernel.on_enumerated = lambda: kernel.actual_pids.update({11: 99})
    result = pins.collect()
    status = "unavailable" if case == "missing" else "stale"
    assert result["processes"][0]["parent"] == {"status": status}
    assert result["processes"][1]["parent"] == {"status": "available", "pid": 11}
    pins.finish()


def test_finish_rechecks_retained_identity_without_new_snapshot(kernel: Kernel) -> None:
    """Death after collection invalidates the parent hint before pins are released."""
    pins = diagnostic.PinnedProcesses((11,), 10.0, Journal())
    assert pins.collect()["processes"][0]["status"] == "available"
    kernel.exited.add(11)
    result = pins.finish()
    assert result["processes"][0]["status"] == "stale"
    assert result["processes"][0]["parent"] == {"status": "stale"}
    assert result["cleanup_errors"] == []
    kernel.CreateToolhelp32Snapshot.assert_called_once()


@pytest.mark.parametrize(
    "api", ["CreateToolhelp32Snapshot", "Process32FirstW", "Process32NextW"]
)
def test_snapshot_failure_preserves_pins_but_never_parent_authority(
    kernel: Kernel, api: str
) -> None:
    """Failed or partial enumeration cannot fabricate parents from its prefix."""
    kernel.fail.add((api, 0))
    pins = diagnostic.PinnedProcesses((11, 22), 10.0, Journal())
    result = pins.collect()
    assert result["enumeration"]["complete"] is False
    assert result["enumeration"]["error"]
    assert all(
        value["parent"] == {"status": "unavailable"} for value in result["processes"]
    )
    pins.finish()
    closed = [call.args[0] for call in kernel.CloseHandle.call_args_list]
    assert (900 in closed) is (api != "CreateToolhelp32Snapshot")
    assert closed[-2:] == [1011, 1022]


def test_empty_snapshot_has_no_invented_parent(kernel: Kernel) -> None:
    """ERROR_NO_MORE_FILES on the first entry is complete empty evidence."""
    kernel.entries = []
    pins = diagnostic.PinnedProcesses((11,), 10.0, Journal())
    result = pins.collect()
    assert result["enumeration"]["entries"] == 0
    assert result["enumeration"]["complete"] is True
    assert result["processes"][0]["parent"] == {"status": "unavailable"}
    pins.close()


@pytest.mark.parametrize("count", [4095, 4096, 4097])
def test_enumeration_stops_before_requesting_entry_4097(
    kernel: Kernel, count: int
) -> None:
    """The bound is strict even if the native snapshot has arbitrarily many rows."""
    kernel.entries = [(10000 + index, 0, "ignored") for index in range(count)]
    pins = diagnostic.PinnedProcesses((11,), 10.0, Journal())
    result = pins.collect()
    assert result["enumeration"]["entries"] == min(count, 4096)
    assert result["enumeration"]["complete"] is (count < 4096)
    assert kernel.Process32NextW.call_count == min(count, 4095)
    assert result["processes"][0]["parent"] == {"status": "unavailable"}
    pins.close()


@pytest.mark.parametrize("slot", [1, 4096])
def test_enumeration_stops_immediately_when_all_requested_pids_are_found(
    kernel: Kernel, slot: int
) -> None:
    """Finding the last requested PID in slot 4096 succeeds without a 4097th read."""
    kernel.entries = [(10000 + i, 0, "unrelated") for i in range(slot - 1)]
    kernel.entries.extend([(11, 7, "ignored"), (99999, 88, "must-not-be-read")])
    pins = diagnostic.PinnedProcesses((11,), 10.0, Journal())
    result = pins.collect()
    assert result["enumeration"]["complete"] is True
    assert result["enumeration"]["entries"] == slot
    assert result["processes"][0]["parent"] == {"status": "available", "pid": 7}
    assert kernel.Process32NextW.call_count == slot - 1
    assert kernel.index == slot - 1
    pins.close()


def test_duplicate_requested_pid_cannot_supply_ambiguous_parents(
    kernel: Kernel,
) -> None:
    """A malformed requested-PID duplicate invalidates the whole snapshot hint."""
    kernel.entries = [(11, 1, "ignored"), (11, 9, "ignored"), (22, 11, "ignored")]
    pins = diagnostic.PinnedProcesses((11, 22), 10.0, Journal())
    result = pins.collect()
    assert result["enumeration"]["complete"] is False
    assert "Duplicate" in result["enumeration"]["error"]
    assert all(
        item["parent"] == {"status": "unavailable"} for item in result["processes"]
    )
    pins.close()


@pytest.mark.parametrize(
    ("deadline", "cost"), [(10.0, 2.999), (10.0, 3.0), (10.0, 3.001), (2.0, 1.0)]
)
def test_enumeration_uses_minimum_of_original_and_three_second_budget(
    kernel: Kernel, deadline: float, cost: float
) -> None:
    """A single late native call or exact deadline crossing invalidates enumeration."""
    pins = diagnostic.PinnedProcesses((11,), deadline, Journal())
    kernel.advance["CreateToolhelp32Snapshot"] = cost
    result = pins.collect()
    assert result["enumeration"]["complete"] is (cost < min(3.0, deadline - 1.0))
    assert result["enumeration"]["finished_monotonic"] == 1.0 + cost
    if cost >= min(3.0, deadline - 1.0):
        assert kernel.Process32FirstW.call_count == 0
    pins.close()
    assert (900,) in [call.args for call in kernel.CloseHandle.call_args_list]


def test_iteration_deadline_is_checked_between_native_rows(kernel: Kernel) -> None:
    """A progressing native iterator cannot reset or evade the same 3-second bound."""
    kernel.entries = [(1000 + i, 0, "ignored") for i in range(100)]
    pins = diagnostic.PinnedProcesses((11,), 10.0, Journal())
    kernel.advance["Process32NextW"] = 1.0
    result = pins.collect()
    assert result["enumeration"]["complete"] is False
    assert result["enumeration"]["entries"] == 3
    assert kernel.Process32NextW.call_count == 3
    pins.close()


@pytest.mark.parametrize("length", [1024, 1025, 32767])
def test_image_output_is_never_truncated_into_a_success(
    kernel: Kernel, length: int
) -> None:
    """Native query capacity is 32768, but retained identity must fit 1024 chars."""
    kernel.paths[11] = "x" * length
    pins = diagnostic.PinnedProcesses((11,), 10.0, Journal())
    value = pins.collect()["processes"][0]
    assert value["image_path"] == ("x" * length if length <= 1024 else None)
    assert value["status"] == ("available" if length <= 1024 else "unavailable")
    pins.close()


def test_eight_unicode_images_fit_bounded_receipt(kernel: Kernel) -> None:
    """The largest retained image fields leave room inside the 128-KiB JSON cap."""
    pids = tuple(range(1, 9))
    kernel.paths = dict.fromkeys(pids, "\U0001f680" * 1024)
    pins = diagnostic.PinnedProcesses(pids, 10.0, Journal())
    result = pins.collect()
    assert len(json.dumps(result).encode()) < 131072
    assert len(result["processes"]) == 8
    pins.close()


@pytest.mark.parametrize("path", ["", "C:\\owned\\\0incomplete"])
def test_incomplete_native_image_cannot_be_an_identity(
    kernel: Kernel, path: str
) -> None:
    """Empty or embedded-NUL native output is rejected rather than shortened."""
    kernel.paths[11] = path
    pins = diagnostic.PinnedProcesses((11,), 10.0, Journal())
    value = pins.collect()["processes"][0]
    assert value["image_path"] is None
    assert value["status"] == "unavailable"
    pins.close()


def test_arbitrary_failure_messages_are_bounded_per_requested_pid(
    kernel: Kernel,
) -> None:
    """An exceptional diagnostic cannot bypass the 256-character error bound."""
    kernel.exceptions[("OpenProcess", 11)] = OSError("x" * 10000)
    pins = diagnostic.PinnedProcesses((11,), 10.0, Journal())
    error = pins.collect()["processes"][0]["errors"][0]
    assert len(error) == 256
    pins.close()


@pytest.mark.parametrize("phase", ["before", "after"])
def test_logger_failure_during_open_still_closes_any_acquired_pin(
    kernel: Kernel, phase: str
) -> None:
    """Ownership is recorded before call-end logging can fail."""
    journal = Journal()
    getattr(journal, phase).add("topology.OpenProcess.11")
    pins = diagnostic.PinnedProcesses((11, 22), 10.0, journal)
    assert pins.collect()["processes"][1]["status"] == "available"
    pins.close()
    closed = [call.args[0] for call in kernel.CloseHandle.call_args_list]
    assert (1011 in closed) is (phase == "after")
    assert 1022 in closed


@pytest.mark.parametrize("phase", ["before", "after"])
def test_logger_failure_during_snapshot_never_leaks_snapshot(
    kernel: Kernel, phase: str
) -> None:
    """The single externally logged enumeration encloses snapshot cleanup."""
    journal = Journal()
    getattr(journal, phase).add("topology.Toolhelp.enumerate")
    pins = diagnostic.PinnedProcesses((11,), 10.0, journal)
    assert pins.collect()["enumeration"]["complete"] is False
    pins.close()
    closed = [call.args[0] for call in kernel.CloseHandle.call_args_list]
    assert (900 in closed) is (phase == "after")
    assert 1011 in closed


@pytest.mark.parametrize("phase", ["before", "after"])
def test_logger_cleanup_failure_cannot_prevent_or_repeat_native_close(
    kernel: Kernel, phase: str
) -> None:
    """If logging never enters close, fallback does; executed closes are not retried."""
    journal = Journal()
    getattr(journal, phase).update(
        {"topology.CloseHandle.11", "topology.CloseHandle.22"}
    )
    pins = diagnostic.PinnedProcesses((11, 22), 10.0, journal)
    errors = pins.close()
    assert len(errors) == 2
    assert [call.args[0] for call in kernel.CloseHandle.call_args_list] == [1011, 1022]
    assert pins.close() == errors
    assert kernel.CloseHandle.call_count == 2


def test_native_cleanup_failure_does_not_skip_other_handles(kernel: Kernel) -> None:
    """A failed close is reported without blind retry or loss of neighboring pins."""
    pins = diagnostic.PinnedProcesses((11, 22), 10.0, Journal())
    kernel.fail.add(("CloseHandle", 11))
    result = pins.finish()
    assert len(result["cleanup_errors"]) == 1
    assert [call.args[0] for call in kernel.CloseHandle.call_args_list] == [1011, 1022]


def test_constructor_interruption_closes_all_retained_handles(kernel: Kernel) -> None:
    """An escaping BaseException cannot leak pins before the caller receives owner."""
    failure = KeyboardInterrupt("stop construction")
    kernel.exceptions[("QueryFullProcessImageNameW", 22)] = failure
    with pytest.raises(KeyboardInterrupt) as caught:
        diagnostic.PinnedProcesses((11, 22), 10.0, Journal())
    assert caught.value is failure
    assert [call.args[0] for call in kernel.CloseHandle.call_args_list] == [1011, 1022]


def test_finish_interruption_preserves_primary_and_closes_all_pins(
    kernel: Kernel,
) -> None:
    """Final recheck interruption still releases every owned process handle."""
    pins = diagnostic.PinnedProcesses((11, 22), 10.0, Journal())
    failure = KeyboardInterrupt("stop recheck")
    kernel.exceptions[("WaitForSingleObject", 11)] = failure
    with pytest.raises(KeyboardInterrupt) as caught:
        pins.finish()
    assert caught.value is failure
    assert [call.args[0] for call in kernel.CloseHandle.call_args_list] == [1011, 1022]


def test_logger_masking_does_not_replace_primary_native_failure(kernel: Kernel) -> None:
    """A broken error logger preserves the native cause in the per-PID result."""
    kernel.fail.add(("GetProcessTimes", 11))
    journal = Journal()
    journal.mask_error = True
    pins = diagnostic.PinnedProcesses((11,), 10.0, journal)
    errors = pins.collect()["processes"][0]["errors"]
    assert "GetProcessTimes failed" in errors[0]
    assert "journal masked" not in errors[0]
    pins.close()


def test_snapshot_cleanup_failure_preserves_enumeration_failure(kernel: Kernel) -> None:
    """Enumeration's primary failure survives a failing snapshot handle close."""
    kernel.fail.update({("Process32NextW", 0), ("CloseHandle", 0)})
    pins = diagnostic.PinnedProcesses((11, 22), 10.0, Journal())
    result = pins.collect()
    assert "enumeration failed" in result["enumeration"]["error"]
    final = pins.finish()
    assert "CloseHandle failed" in final["cleanup_errors"][0]
    assert [call.args[0] for call in kernel.CloseHandle.call_args_list] == [
        900,
        1011,
        1022,
    ]


def test_expired_budget_never_opens_or_enumerates(kernel: Kernel) -> None:
    """Every operation uses the caller's original deadline without a replacement."""
    pins = diagnostic.PinnedProcesses((11, 22), 1.0, Journal())
    result = pins.collect()
    assert result["enumeration"]["complete"] is False
    assert all(value["status"] == "unavailable" for value in result["processes"])
    assert pins.finish()["cleanup_errors"] == []
    assert kernel.calls == []
