"""Portable witness ABI, deadline and cleanup tests; no native qualification."""

from __future__ import annotations

import argparse
import hashlib
import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from scripts import observe_death_witness as witness

Json = dict[str, Any]
NOW = 100_000_000_000
SESSION = "owned-session"
HASH = "a" * 64
EPOCH = "ab" * 16


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Use deterministic clock boundaries without sleeps or native calls."""
    value = [NOW]
    monkeypatch.setattr(witness.time, "monotonic_ns", lambda: value[0])
    monkeypatch.setattr(witness.time, "monotonic", lambda: value[0] / 1e9)
    return value


def journal() -> Mock:
    """Execute logged operations with a controllable cleanup path."""
    log = Mock()
    log.deadline = 115.0
    log.call.side_effect = lambda _name, operation: operation()
    log.cleanup.side_effect = lambda _name, operation: operation()
    log.stream = io.StringIO()
    return log


def executor_record() -> Json:
    """Fixed actual executor identity, not the venv launcher's PID."""
    return {
        "process_id": 21,
        "creation_100ns": 123,
        "image_path": "C:\\Python\\python.exe",
    }


def kernel() -> Mock:
    """ctypes-like functions fill native output storage and record exact handles."""
    api = Mock()
    api.OpenProcess.return_value = 500
    api.GetProcessId.return_value = 21
    api.WaitForSingleObject.return_value = witness.WAIT_TIMEOUT
    api.TerminateProcess.return_value = 1
    api.CloseHandle.return_value = 1

    def times(_handle: int, created: Any, *_rest: object) -> int:  # noqa: ANN401 - ctypes output pointer
        created._obj.value = 123
        return 1

    def image(_handle: int, _flags: int, buffer: Any, size: Any) -> int:  # noqa: ANN401 - ctypes output pointers
        buffer.value = executor_record()["image_path"]
        size._obj.value = len(buffer.value)
        return 1

    def exit_code(_handle: int, code: Any) -> int:  # noqa: ANN401 - ctypes output pointer
        code._obj.value = 17
        return 1

    api.GetProcessTimes.side_effect = times
    api.QueryFullProcessImageNameW.side_effect = image
    api.GetExitCodeProcess.side_effect = exit_code
    return api


def begin_packet() -> Json:
    """Fixed flat begin schema matching the independently frozen GUI reference."""
    return {
        "schema": 1,
        "session": SESSION,
        "case_deadline_ns": NOW + 15_000_000_000,
        "absolute_deadline_ns": NOW + 20_000_000_000,
        "identity": {"epoch": EPOCH},
        "baseline_profile": {},
        "temporary_profile": {},
        "fixture": {},
        "expected": {},
        "reference_initial": {"rect": [0, 0, 900, 640]},
        "before_rgb_sha256": HASH,
        "before_rgb_byte_count": witness.ROOT_BYTES,
    }


def kill_packet() -> Json:
    """A first held capacity token gives no permission to grant or cancel."""
    return {
        "schema": 1,
        "session": SESSION,
        "requested_ns": NOW,
        "hold": {
            "session": SESSION,
            "generation": 1,
            "observe_called_ns": NOW - 500_000_000,
            "token": {
                "credit_schema": 1,
                "epoch": EPOCH,
                "frame_id": 1,
                "chunk_index": 0,
                "offset": 0,
                "count": 65_536,
            },
            "wait_received_ns": NOW - 100_000_000,
            "no_credit_issued": True,
            "no_cancel_issued": True,
            "harness_sha256": HASH,
        },
    }


def test_exact_executor_abi_and_one_death(clock: list[int]) -> None:
    """Only executor handle gets terminate rights and the fixed diagnostic exit17."""
    assert clock[0] == NOW
    api, log = kernel(), journal()
    handle = witness.ExecutorHandle(api, log, executor_record())
    api.OpenProcess.assert_called_once_with(0x00101001, 0, 21)
    result = handle.terminate(NOW + 1)
    assert result == {
        "attempted": True,
        "returned": True,
        "before_ns": NOW,
        "after_ns": NOW,
    }
    api.TerminateProcess.assert_called_once_with(500, 17)
    with pytest.raises(RuntimeError, match="cannot be retried"):
        handle.terminate(NOW + 1)
    handle.close()
    handle.close()
    api.CloseHandle.assert_called_once_with(500)


@pytest.mark.parametrize(
    "fault",
    [
        "open",
        "pid",
        "creation",
        "image",
        "image_bound",
        "dead",
        "time_query",
        "image_query",
        "logger",
    ],
)
def test_acquisition_failure_closes_exact_owned_handle(fault: str) -> None:
    """Every failed identity or post-open logging check releases acquired ownership."""
    api, log = kernel(), journal()
    if fault == "open":
        api.OpenProcess.return_value = 0
    elif fault == "pid":
        api.GetProcessId.return_value = 22
    elif fault == "creation":
        api.GetProcessTimes.side_effect = lambda _handle, *_args: 1
    elif fault == "image":
        api.QueryFullProcessImageNameW.side_effect = lambda _h, _f, b, size: (
            setattr(b, "value", "bad"),
            setattr(size._obj, "value", 3),
            1,
        )[2]
    elif fault == "image_bound":
        api.QueryFullProcessImageNameW.side_effect = lambda _h, _f, _b, size: (
            setattr(size._obj, "value", 0),
            1,
        )[1]
    elif fault == "dead":
        api.WaitForSingleObject.return_value = 0
    elif fault == "time_query":
        api.GetProcessTimes.side_effect = None
        api.GetProcessTimes.return_value = 0
    elif fault == "image_query":
        api.QueryFullProcessImageNameW.side_effect = None
        api.QueryFullProcessImageNameW.return_value = 0
    else:

        def fail_after(_name: str, operation: Any) -> None:  # noqa: ANN401 - fake journal callable
            operation()
            raise RuntimeError("post-call logging failure")

        log.call.side_effect = fail_after
    with pytest.raises(RuntimeError, match=r"Executor|logging"):
        witness.ExecutorHandle(api, log, executor_record())
    assert api.CloseHandle.call_count == (0 if fault == "open" else 1)
    api.TerminateProcess.assert_not_called()


@pytest.mark.parametrize(
    "fault", ["false", "uncertain", "late_before", "late_after", "journal"]
)
def test_death_failure_never_retries(fault: str, clock: list[int]) -> None:
    """A false, uncertain or late side effect permanently consumes the one attempt."""
    api, log = kernel(), journal()
    handle = witness.ExecutorHandle(api, log, executor_record())
    if fault == "false":
        api.TerminateProcess.return_value = 0
    elif fault == "uncertain":
        api.TerminateProcess.side_effect = OSError("uncertain")
    elif fault == "late_before":
        clock[0] += 1
    elif fault == "late_after":

        def late(*_args: object) -> int:
            clock[0] += 1
            return 1

        api.TerminateProcess.side_effect = late
    else:
        log.call.side_effect = RuntimeError("journal failed")
    with pytest.raises((RuntimeError, OSError), match=r"failed|uncertain|expired"):
        handle.terminate(NOW + 1)
    with pytest.raises(RuntimeError, match="cannot be retried"):
        handle.terminate(NOW + 100)
    assert api.TerminateProcess.call_count == (
        0 if fault in {"late_before", "journal"} else 1
    )
    handle.close()


def test_begin_and_fixed_kill_deadlines(clock: list[int]) -> None:
    """The original case clock and conservative public-call2s boundary both apply."""
    begin = begin_packet()
    witness.validate_begin(
        begin, SESSION, begin["case_deadline_ns"], begin["absolute_deadline_ns"]
    )
    assert witness.validate_kill(kill_packet(), begin, SESSION, HASH) == (
        NOW + 1_500_000_000,
        NOW + 5_000_000_000,
    )
    clock[0] += 1_500_000_000
    with pytest.raises(RuntimeError, match="expired"):
        witness.validate_kill(kill_packet(), begin, SESSION, HASH)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("generation", True),
        ("generation", 2),
        ("session", "other"),
        ("observe_called_ns", None),
        ("wait_received_ns", 1),
        ("no_credit_issued", False),
        ("no_cancel_issued", False),
        ("harness_sha256", "b" * 64),
    ],
)
def test_invalid_hold_rejected(field: str, value: object, clock: list[int]) -> None:
    """Missing time, changed identity and any grant/cancel assertion fail closed."""
    assert clock[0] == NOW
    packet = kill_packet()
    packet["hold"][field] = value
    with pytest.raises(ValueError, match="witness"):
        witness.validate_kill(packet, begin_packet(), SESSION, HASH)


@pytest.mark.parametrize(
    "field", ["credit_schema", "epoch", "frame_id", "chunk_index", "offset", "count"]
)
def test_wrong_held_token(field: str, clock: list[int]) -> None:
    """No alternate credit identity or chunk slice qualifies first-wait death."""
    assert clock[0] == NOW
    packet = kill_packet()
    packet["hold"]["token"][field] = "wrong"
    with pytest.raises(ValueError, match="witness"):
        witness.validate_kill(packet, begin_packet(), SESSION, HASH)


@pytest.mark.parametrize("mutation", ["future", "epoch", "extra", "late", "absolute"])
def test_kill_packet_bounds(mutation: str, clock: list[int]) -> None:
    """No future request, unknown field or expired outer budget is admitted."""
    assert clock[0] == NOW
    packet, begin = kill_packet(), begin_packet()
    if mutation == "future":
        packet["requested_ns"] += 1
    elif mutation == "epoch":
        begin["identity"]["epoch"] = "0" * 32
    elif mutation == "extra":
        packet["extra"] = 1
    elif mutation == "late":
        begin["case_deadline_ns"] = NOW
    else:
        begin["absolute_deadline_ns"] = NOW
    with pytest.raises((ValueError, RuntimeError), match=r"witness|epoch|future"):
        witness.validate_kill(packet, begin, SESSION, HASH)


def test_reference_fixed_file_digest_and_bound(tmp_path: Path) -> None:
    """The only accepted artifact is exact bounded raw RGB, retained immutably."""
    raw = bytes(witness.ROOT_BYTES)
    (tmp_path / "before-root.rgb").write_bytes(raw)
    begin = begin_packet()
    begin["before_rgb_sha256"] = hashlib.sha256(raw).hexdigest()
    begin["expected"] = {"rect": [0, 0, 510, 430]}
    smoke = Mock()
    smoke.rect.side_effect = tuple
    smoke.crop.return_value = b"panel"
    root, panel = witness.read_reference(tmp_path, begin, smoke)
    assert root == raw
    assert panel == b"panel"
    (tmp_path / "before-root.rgb").write_bytes(raw + b"x")
    with pytest.raises(ValueError, match="RGB bytes"):
        witness.read_reference(tmp_path, begin, smoke)
    (tmp_path / "before-root.rgb").write_bytes(raw)
    begin["reference_initial"]["rect"] = [0, 0, 800, 640]
    with pytest.raises(ValueError, match="geometry"):
        witness.read_reference(tmp_path, begin, smoke)


def test_retirement_reads_only_retained_handles(clock: list[int]) -> None:
    """Post-death statuses come from the same retained handles, never reopened PIDs."""
    assert clock[0] == NOW
    api, log = kernel(), journal()
    api.WaitForSingleObject.side_effect = [258, 0, 0]
    pins = Mock()
    pins.kernel = api
    pins.pins = [SimpleNamespace(handle=500), SimpleNamespace(handle=501)]
    pins.retired.return_value = {
        "retired": True,
        "cleanup_errors": [],
        "processes": [{"process_id": 21}, {"process_id": 22}],
    }
    result = witness.wait_retired(pins, log, NOW + 1_000_000_000)
    assert [item["exit_code"] for item in result["processes"]] == [17, 17]
    assert [call.args[0] for call in api.GetExitCodeProcess.call_args_list] == [
        500,
        501,
    ]
    api.OpenProcess.assert_not_called()
    api.TerminateProcess.assert_not_called()


@pytest.mark.parametrize("fault", ["wait", "identity", "exit", "late"])
def test_failed_retirement_cannot_qualify(fault: str, clock: list[int]) -> None:
    """Missing signals, identities, statuses and late reads remain failures."""
    api, log = kernel(), journal()
    pins = Mock()
    pins.kernel = api
    pins.pins = [SimpleNamespace(handle=500)]
    pins.retired.return_value = {
        "retired": True,
        "cleanup_errors": [],
        "processes": [{}],
    }
    api.WaitForSingleObject.return_value = 0
    if fault == "wait":
        api.WaitForSingleObject.return_value = 123
    elif fault == "identity":
        pins.retired.return_value["retired"] = False
    elif fault == "exit":
        api.GetExitCodeProcess.side_effect = None
        api.GetExitCodeProcess.return_value = 0
    else:
        clock[0] += 1
    with pytest.raises(RuntimeError, match=r"failed|proved|unavailable|expired"):
        witness.wait_retired(pins, log, NOW + 1)


def test_cleanup_attempts_every_resource_and_downgrades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed close cannot prevent remaining releases or become successful proof."""
    smoke = Mock()
    args = argparse.Namespace(output=tmp_path, session=SESSION)
    owner = witness.Witness(args, smoke)
    owner.journal = journal()
    owner.executor = Mock()
    owner.executor.action = {"attempted": True}
    owner.executor.close.side_effect = RuntimeError("executor close failed")
    owner.pins = Mock()
    owner.pins.close.return_value = ["pin close failed"]
    owner.guard = Mock()
    owner.previous_dpi = 4
    owner.user = Mock()
    owner.user.SetThreadDpiAwarenessContext.return_value = 0
    monkeypatch.setattr(owner, "_run", lambda: None)
    assert owner.run() == 1
    owner.pins.close.assert_called_once()
    owner.guard.close.assert_called_once()
    assert len(owner.result["cleanup_errors"]) == 3
    assert owner.result["passed"] is False
    assert owner.journal.stream.closed
    smoke.write_handshake.assert_called_once()


@pytest.mark.parametrize("raw", [b"[]", b'{"a":1,"a":2}', b" " * 131_073])
def test_packet_limits(tmp_path: Path, raw: bytes) -> None:
    """Immutable packet reads have strict size, shape and duplicate-key limits."""
    path = tmp_path / "packet.json"
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="witness"):
        witness.read_packet(path)


@pytest.mark.parametrize(
    "fault",
    [
        "success",
        "post_reference",
        "pins",
        "exit_status",
        "late_reference",
        "guard_logger",
        "late_predeath",
        "provenance",
        "close",
    ],
)
def test_full_surviving_lifecycle(  # noqa: C901, PLR0912, PLR0915 - full fixed lifecycle fault matrix
    fault: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: list[int]
) -> None:
    """Run the fixed lifecycle with fake ABI, preserving every failure through close."""
    args = argparse.Namespace(
        output=tmp_path,
        session=SESSION,
        preparation_start=95.0,
        wheel=tmp_path / "wheel.whl",
        states=tmp_path / "states",
        fixture_pid=31,
    )
    log, api, user, smoke, topology, pins = (
        journal(),
        kernel(),
        Mock(),
        Mock(),
        Mock(),
        Mock(),
    )
    user.SetThreadDpiAwarenessContext.return_value = 4
    monkeypatch.setattr(
        witness.c, "WinDLL", lambda *_args, **_kwargs: user, raising=False
    )
    smoke.Journal.return_value = log
    smoke.case_handoff.return_value = 115.0
    smoke.rect.side_effect = tuple
    smoke.crop.return_value = b"panel"
    raw = bytes(witness.ROOT_BYTES)
    (tmp_path / "before-root.rgb").write_bytes(raw)
    smoke.reference.return_value = (raw, b"panel")
    smoke.support.return_value.verify_wheel.return_value = {"verified": True}
    begin = begin_packet()
    begin["fixture"] = {"pid": 31}
    begin["expected"] = {"rect": [0, 0, 510, 430]}
    begin["before_rgb_sha256"] = hashlib.sha256(raw).hexdigest()
    smoke.fixture_key.return_value = begin["expected"]
    guard = smoke.ReferenceGuard.return_value
    guard.initial = begin["reference_initial"]
    executor = executor_record()
    peer = {**executor, "process_id": 22, "creation_100ns": 124}
    records = [executor, peer]
    baseline = {
        "console": "conhost",
        "roles": {"executor": 21},
        "request": {},
        "records": {"processes": [executor]},
    }
    temporary = {"roles": {"peer": 22}, "records": {"processes": [peer]}}
    begin["baseline_profile"], begin["temporary_profile"] = baseline, temporary
    smoke.profile_records.return_value = baseline["roles"]
    smoke.temporary_records.return_value = temporary["roles"]
    topology.expected_console_image.return_value = "conhost"
    topology.PinnedProcesses.return_value = pins
    smoke.topology_module.return_value = topology
    pins.collect.return_value = {"processes": records}
    pins.observe.return_value = {"processes": records, "cleanup_errors": []}
    pins.pins = [SimpleNamespace(handle=501), SimpleNamespace(handle=502)]
    pins.kernel = api
    pins.retired.return_value = {
        "retired": True,
        "cleanup_errors": [],
        "processes": [dict(r) for r in records],
    }
    pins.close.return_value = []

    def terminate(_handle: int, _code: int) -> int:
        api.WaitForSingleObject.return_value = 0
        return 1

    api.TerminateProcess.side_effect = terminate
    request = kill_packet()
    request["hold"]["harness_sha256"] = hashlib.sha256(
        Path(witness.__file__).with_name("installed_observe_smoke.py").read_bytes()
    ).hexdigest()
    packets = {
        "case-start.json": {},
        "death-begin.json": begin,
        "death-kill-request.json": request,
    }
    monkeypatch.setattr(
        witness, "wait_packet", lambda path, _journal: packets[path.name]
    )
    if fault == "post_reference":
        smoke.reference.return_value = (b"wrong", b"panel")
    elif fault == "pins":
        pins.observe.return_value = {"processes": [], "cleanup_errors": []}
    elif fault == "exit_status":
        api.GetExitCodeProcess.side_effect = lambda _handle, code: (
            setattr(code._obj, "value", 0),
            1,
        )[1]
    elif fault == "late_reference":

        def late(*_args: object) -> tuple[bytes, bytes]:
            clock[0] = NOW + 5_000_000_000
            return raw, b"panel"

        smoke.reference.side_effect = late
    elif fault == "guard_logger":

        def logged(name: str, operation: Any) -> object:  # noqa: ANN401 - fake journal boundary
            result = operation()
            if name == "death.reference.guard":
                raise RuntimeError("late guard logger")
            return result

        log.call.side_effect = logged
    elif fault == "late_predeath":

        def late_guard(name: str, operation: Any) -> object:  # noqa: ANN401 - fake journal boundary
            result = operation()
            if name == "death.reference.prekill":
                clock[0] = NOW + 1_500_000_000
            return result

        log.call.side_effect = late_guard
    elif fault == "provenance":
        smoke.support.return_value.verify_wheel.side_effect = RuntimeError(
            "wrong wheel"
        )
    elif fault == "close":
        pins.close.return_value = ["close failed"]
    owner = witness.Witness(args, smoke)
    assert owner.run() == (0 if fault == "success" else 1)
    if fault == "success":
        assert owner.result["passed"] is True
        assert owner.result["reference"]["exact_equal"] is True
        assert owner.result["retirement_deadline_ns"] == NOW + 5_000_000_000
        api.TerminateProcess.assert_called_once_with(500, 17)
        user.SetThreadDpiAwarenessContext.assert_any_call(4)
    else:
        assert owner.result["passed"] is False
    if fault == "late_predeath":
        api.TerminateProcess.assert_not_called()
    if fault != "provenance":
        guard.close.assert_called_once()
    if fault not in {"provenance", "guard_logger"}:
        pins.close.assert_called_once()
    assert log.stream.closed
    result = smoke.write_handshake.call_args.args[1]
    assert result["session"] == SESSION
    assert result["result"] == owner.result


def test_fixed_loader_and_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the fixed adjacent harness is loaded, and CLI exposes no action selector."""
    module = witness.fixed_smoke()
    assert module.__file__ is not None
    assert Path(module.__file__).name == "installed_observe_smoke.py"
    monkeypatch.setattr(witness, "fixed_smoke", lambda: module)
    fake = Mock()
    fake.run.return_value = 0
    monkeypatch.setattr(witness, "Witness", Mock(return_value=fake))
    monkeypatch.setattr(
        witness.sys,
        "argv",
        [
            "observe_death_witness.py",
            "--wheel",
            "wheel.whl",
            "--output",
            str(tmp_path),
            "--states",
            "states",
            "--session",
            SESSION,
            "--fixture-pid",
            "31",
            "--preparation-start",
            "95",
        ],
    )
    assert witness.main() == 0
    fake.run.assert_called_once()


def test_wait_packet_deadline_and_valid_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """File polling cannot reset the current original deadline."""
    log = journal()
    path = tmp_path / "begin.json"
    monkeypatch.setattr(
        witness.time, "sleep", lambda _seconds: path.write_text('{"schema":1}')
    )
    assert witness.wait_packet(path, log) == {"schema": 1}
    assert log.check.call_count == 3


def test_invalid_begin_digest(clock: list[int]) -> None:
    """Malformed frozen-byte evidence is rejected before process actions."""
    assert clock[0] == NOW
    begin = begin_packet()
    begin["before_rgb_sha256"] = "bad"
    with pytest.raises(ValueError, match="digest"):
        witness.validate_begin(
            begin, SESSION, begin["case_deadline_ns"], begin["absolute_deadline_ns"]
        )


def test_cleanup_logging_failure_still_closes_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A late final journal check remains failure after every native close."""
    owner = witness.Witness(
        argparse.Namespace(output=tmp_path, session=SESSION), Mock()
    )
    owner.journal = journal()
    owner.journal.check.side_effect = RuntimeError("late close")
    monkeypatch.setattr(owner, "_run", lambda: None)
    assert owner.run() == 1
    assert owner.journal.stream.closed
    assert "late close" in owner.result["cleanup_errors"][0]
