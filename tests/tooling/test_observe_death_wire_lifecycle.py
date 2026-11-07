"""Real producer-to-consumer death wire regressions over a fake native ABI.

No producer return dictionaries are substituted. Actual collect/observe/retired,
Witness.run, file encoders, control readiness/ACK and result admission execute.
"""

from __future__ import annotations

import argparse
import ctypes as c
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
from typing import Any, override
from unittest.mock import Mock

import pytest
from scripts import installed_observe_smoke as smoke
from scripts import observe_death_control as control
from scripts import observe_death_witness as witness
from scripts import observe_process_diagnostics as diagnostic
from tests.tooling.test_observe_process_diagnostics import Kernel

from verdiclip.platform import helper_runtime, owned_helper

Json = dict[str, Any]
SESSION = "a" * 32
IMAGE = r"C:\Python\python.exe"
CONSOLE = r"c:\windows\system32\conhost.exe"


class WireKernel(Kernel):
    """Actual ctypes output writes, separate executor termination handle, no OS I/O."""

    def __init__(self, *, console: bool = False) -> None:
        super().__init__()
        self.now = 100.0
        self.entries = [(21, 1, "executor"), (22, 21, "peer")]
        if console:
            self.entries.append((23, 22, "console"))
        self.paths = {21: IMAGE, 22: IMAGE, 23: CONSOLE}
        self.created = {21: 123, 22: 124, 23: 125}
        self.actions: list[tuple[int, int]] = []
        self.raw_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.post_exit_image_calls: list[int] = []
        self.functions["TerminateProcess"] = Mock(side_effect=self.terminate)
        self.functions["GetExitCodeProcess"] = Mock(side_effect=self.exit_code)

    @override
    def invoke(self, name: str, *args: Any) -> int:
        """Permit only exact executor terminate rights and retained bounded waits."""
        self.raw_calls.append((name, args))
        if name == "QueryFullProcessImageNameW" and self.actions:
            self.post_exit_image_calls.append(args[0])
            self.last_error = 6
            return 0
        if name == "OpenProcess" and args[0] == witness.PROCESS_RIGHTS:
            assert args[1:] == (0, 21)
            return 10_021
        if args and args[0] == 10_021:
            args = (1021, *args[1:])
        if name == "WaitForSingleObject":
            assert args[1] in (0, 100)
            args = (args[0], 0)
        return super().invoke(name, *args)

    def terminate(self, handle: int, code: int) -> int:
        """One executor-only action also simulates natural peer containment exit."""
        assert (handle, code) == (10_021, 17)
        assert not self.actions
        self.actions.append((handle, code))
        self.exited.update(pid for pid, _parent, _name in self.entries)
        return 1

    def exit_code(self, handle: int, output: Any) -> int:  # noqa: ANN401 - ctypes output pointer
        """Write statuses using the original retained handles, never new opens."""
        pid = handle - (10_000 if handle == 10_021 else 1000)
        assert pid in self.exited
        c.cast(output, c.POINTER(c.c_uint32))[0] = 17 if pid == 21 else 0
        return 1


def real_collect(
    api: WireKernel, pids: list[int], monkeypatch: pytest.MonkeyPatch
) -> Json:
    """Generate the real producer schema, including enumeration, over fake ctypes."""
    with monkeypatch.context() as patch:
        patch.setattr(diagnostic.c, "WinDLL", lambda *_a, **_k: api, raising=False)
        patch.setattr(
            diagnostic.c, "get_last_error", lambda: api.last_error, raising=False
        )
        log = Mock()
        log.call.side_effect = lambda _name, operation: operation()
        pins = diagnostic.PinnedProcesses(pids, 115.1, log)
        try:
            return pins.collect()
        finally:
            assert pins.close() == []


def fixture_data() -> Json:
    """Fixed owned target geometry and authored state, not process producer output."""
    return {
        "mode": "observe-only",
        "alive": True,
        "pid": 31,
        "process_created_100ns": 999,
        "root_hwnd": 456,
        "rect": [24, 24, 534, 454],
        "content_rect": [26, 26, 515, 452],
        "authored_layout_rect": [0, 0, 900, 640],
        "dpi_x": 96,
        "dpi_y": 96,
        "state": {
            "target": {"horizontal": 0, "vertical": 119},
            "sibling": {"horizontal": 27, "vertical": 93},
            "outer": None,
        },
        "markers": [
            {"id": "tl", "rect": [8, 8, 12, 12], "rgb": [11, 241, 227]},
            {"id": "tr", "rect": [812, 8, 816, 12], "rgb": [247, 181, 13]},
            {"id": "bl", "rect": [8, 544, 12, 548], "rgb": [149, 17, 239]},
            {"id": "br", "rect": [812, 544, 816, 548], "rgb": [19, 239, 73]},
        ],
        "sequence": 0,
        "milliseconds": 100,
    }


def pixels(fixture: Json) -> bytes:
    """Paint fixed off-panel markers for the real bounded reference-file checks."""
    data = bytearray(witness.ROOT_BYTES)
    for marker in fixture["markers"]:
        left, top, right, bottom = marker["rect"]
        for row in range(top, bottom):
            data[(row * 900 + left) * 3 : (row * 900 + right) * 3] = bytes(
                marker["rgb"]
            ) * (right - left)
    return bytes(data)


def await_file(path: Path, finished: Event) -> None:
    """Bound synchronization by a real Event timeout, independent of fake clocks."""
    for _ in range(2000):
        if path.exists():
            return
        if finished.wait(0.002):
            pytest.fail(f"Witness exited before {path.name}")
    pytest.fail(f"Witness did not publish {path.name}")


def mutate_wire(path: Path, packet: Json, mutation: str) -> Json:  # noqa: C901, PLR0912 - explicit wire mutation matrix
    """Adversarial edits start from actual serialized producer evidence."""
    packet = deepcopy(packet)
    if path.name == "death-pinned.json":
        records = packet["pinned"]["records"]
        if mutation == "collect_extra":
            records["cleanup_errors"] = []
        elif mutation == "collect_missing":
            records.pop("enumeration")
        elif mutation == "collect_incomplete":
            records["enumeration"]["complete"] = False
        elif mutation == "collect_pid":
            records["processes"][1]["process_id"] = 999
        elif mutation == "collect_bool":
            records["enumeration"]["entries"] = True
        elif mutation == "collect_member":
            records["processes"].append(deepcopy(records["processes"][0]))
    if path.name == "death-result.json":
        result = packet["result"]
        if mutation == "retired_creation":
            result["retirement"]["processes"][0]["observed_creation_100ns"] += 1
        elif mutation == "retired_bool":
            result["retirement"]["processes"][0]["observed_process_id"] = True
        elif mutation == "retired_missing":
            result["retirement"]["processes"].pop()
        elif mutation == "result_cleanup":
            result["cleanup_errors"] = ["forced fallback"]
        elif mutation == "late_action":
            result["action"]["after_ns"] = (
                result["request"]["hold"]["observe_called_ns"] + 2_000_000_000
            )
        elif mutation == "reference_drift":
            result["reference"]["root_sha256"] = "0" * 64
        elif mutation == "request_changed":
            result["request"]["hold"]["no_credit_issued"] = False
        elif mutation == "executor_missing":
            result.pop("executor_retirement")
        elif mutation == "executor_extra":
            result["executor_retirement"]["fresh_image"] = IMAGE
        elif mutation == "executor_bool":
            result["executor_retirement"]["wait_result"] = False
        elif mutation == "executor_image":
            result["executor_retirement"]["pre_action_identity"]["image"] = (
                "C:\\wrong.exe"
            )
        elif mutation == "executor_created":
            result["executor_retirement"]["observed_creation_100ns"] += 1
        elif mutation == "executor_late":
            result["executor_retirement"]["observed_ns"] = result[
                "retirement_deadline_ns"
            ]
    return packet


@pytest.mark.parametrize("console", [False, True])
@pytest.mark.parametrize(
    "mutation",
    [
        "none",
        "collect_extra",
        "collect_missing",
        "collect_incomplete",
        "collect_pid",
        "collect_member",
        "collect_bool",
        "actual_close",
        "unsupported_event",
        "owner_nonzero",
        "owner_nonempty",
        "retired_creation",
        "retired_missing",
        "retired_bool",
        "result_cleanup",
        "late_action",
        "reference_drift",
        "request_changed",
        "executor_missing",
        "executor_extra",
        "executor_bool",
        "executor_image",
        "executor_created",
        "executor_late",
    ],
)
def test_actual_death_wire_lifecycle(  # noqa: C901, PLR0915 - complete fixed wire lifecycle regression
    *, console: bool, mutation: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real producers cross the entire serialized witness/control lifecycle."""
    api = WireKernel(console=console)
    monkeypatch.setattr(witness.time, "monotonic", lambda: api.now)
    monkeypatch.setattr(witness.time, "monotonic_ns", lambda: int(api.now * 1e9))
    monkeypatch.setattr(witness.os, "getpid", lambda: 90)
    user = Mock()
    user.SetThreadDpiAwarenessContext.return_value = 4
    monkeypatch.setattr(
        witness.c,
        "WinDLL",
        lambda name, **_kw: api if name == "kernel32" else user,
        raising=False,
    )
    monkeypatch.setattr(
        diagnostic.c, "get_last_error", lambda: api.last_error, raising=False
    )
    monkeypatch.setattr(smoke, "topology_module", lambda: diagnostic)
    baseline_records = real_collect(api, [21], monkeypatch)
    temp_pids = [22, 23] if console else [22]
    temporary_records = real_collect(api, temp_pids, monkeypatch)
    baseline = {
        "request": {
            "pids": [21],
            "launch_pid": 21,
            "executor_pid": 21,
            "expected_launch": IMAGE,
            "expected_base": IMAGE,
            "snapshot_time": 100.0,
            "deadline": 110.0,
        },
        "console": CONSOLE,
        "roles": {"launcher": 21, "executor": 21},
        "records": baseline_records,
    }
    identity = {"pid": 22, "epoch": "b" * 32}
    temporary = {
        "peer": identity,
        "console": CONSOLE,
        "records": temporary_records,
        "roles": {"peer": 22, **({"console": 23} if console else {})},
    }
    fixture = fixture_data()
    initial = {
        "root_hwnd": 456,
        "pid": 31,
        "rect": [0, 0, 900, 640],
        "input_tick": 7,
        "creation_100ns": 999,
    }
    raw = pixels(fixture)
    panel = smoke.crop(raw, smoke.rect(initial["rect"]), smoke.rect(fixture["rect"]))
    monkeypatch.setattr(smoke, "latest", lambda _states: deepcopy(fixture))
    guard = Mock()
    guard.initial = initial
    guard.check.return_value = initial
    monkeypatch.setattr(smoke, "ReferenceGuard", Mock(return_value=guard))
    monkeypatch.setattr(smoke, "reference", Mock(return_value=(raw, panel)))
    runtime = helper_runtime.RuntimeManifest(
        str(tmp_path / "base-python"),
        (str(tmp_path),),
        (str(tmp_path / "module.py"),) * 11,
        ("1", "1"),
        (3, 13, 5),
        64,
    )
    provenance = {
        "executable": str(tmp_path / "base-python"),
        "roots": (str(tmp_path),),
        "python_version": (3, 13, 5),
    }
    monkeypatch.setattr(helper_runtime, "inspect_runtime", lambda: runtime)
    monkeypatch.setattr(owned_helper, "HelperCommand", Mock())
    monkeypatch.setattr(
        smoke,
        "support",
        lambda: SimpleNamespace(verify_wheel=lambda _wheel: provenance),
    )
    root = tmp_path / "owned"
    root.mkdir()
    executor = root / "executor"
    executor.mkdir()
    finished = Event()
    returns: list[int] = []
    witness_owner, gui_owner = Mock(), Mock()
    witness_owner.process.pid = 90
    witness_owner.snapshot.side_effect = lambda: {
        "exit_code": (1 if mutation == "owner_nonzero" else returns[0])
        if finished.is_set()
        else None,
        "members": [] if finished.is_set() and mutation != "owner_nonempty" else [90],
    }
    gui_owner.snapshot.side_effect = lambda: {
        "exit_code": 17 if api.actions else None,
        "members": [] if api.actions else [21, *temp_pids],
    }
    owners = {
        "gui": gui_owner,
        "fixture": SimpleNamespace(process=SimpleNamespace(pid=31)),
    }
    coordinator = control.DeathControl(
        smoke,
        SimpleNamespace(ExternalOwner=lambda: witness_owner),
        owners,
        output=root,
        executor=executor,
        states=root / "states",
        wheel=root / "proof.whl",
        session=SESSION,
        started=100.0,
        provenance=provenance,
    )
    (coordinator.output / "before-root.rgb").write_bytes(raw)
    original_write = smoke.write_handshake

    def write(path: Path, packet: Json) -> None:
        if path.name == "death-result.json" and mutation == "unsupported_event":
            with (path.parent / "death-calls.jsonl").open("a") as stream:
                stream.write(
                    json.dumps(
                        {
                            "event": "process_exit",
                            "name": "death-witness",
                            "monotonic": api.now,
                        }
                    )
                    + "\n"
                )
        original_write(path, mutate_wire(path, packet, mutation))

    monkeypatch.setattr(smoke, "write_handshake", write)
    args = argparse.Namespace(
        output=coordinator.output,
        session=SESSION,
        preparation_start=100.0,
        wheel=root / "proof.whl",
        states=root / "states",
        fixture_pid=31,
    )
    survivor = witness.Witness(args, smoke)

    if mutation == "actual_close":
        original_terminate = api.functions["TerminateProcess"].side_effect

        def terminate_then_fail_close(handle: int, code: int) -> int:
            value = original_terminate(handle, code)
            api.fail.add(("CloseHandle", 22))
            return int(value)

        api.functions["TerminateProcess"].side_effect = terminate_then_fail_close

    def execute() -> None:
        try:
            returns.append(survivor.run())
        finally:
            finished.set()

    thread = Thread(target=execute, name="fake-native-real-witness")
    thread.start()
    try:
        await_file(coordinator.output / "death-ready.json", finished)
        assert coordinator.preparation_ready()
        api.now = 100.1
        smoke.write_handshake(
            executor / "case-start.json",
            {"schema": 1, "session": SESSION, "case_start": api.now, "deadline": 115.1},
        )
        smoke.write_handshake(
            executor / "death-reference.json",
            {
                "schema": 1,
                "session": SESSION,
                "fixture": fixture,
                "expected": smoke.fixture_key(fixture),
                "reference_initial": initial,
                "before_rgb_sha256": hashlib.sha256(raw).hexdigest(),
                "before_rgb_byte_count": len(raw),
            },
        )
        assert not coordinator.before_observe(baseline, temporary, identity, 115.1)
        await_file(coordinator.output / "death-pinned.json", finished)
        if mutation.startswith("collect_"):
            with pytest.raises(ValueError, match=r"Death|death|executor retirement"):
                coordinator.before_observe(baseline, temporary, identity, 115.1)
            assert not api.actions
            return
        assert coordinator.before_observe(baseline, temporary, identity, 115.1)
        pinned = control.read_packet(
            coordinator.output / "death-pinned.json", SESSION, "pinned"
        )
        assert set(pinned["records"]) == {"processes", "enumeration"}
        assert "cleanup_errors" not in pinned["records"]
        assert [r["process_id"] for r in pinned["records"]["processes"]] == [
            21,
            *temp_pids,
        ]
        hold = {
            "session": SESSION,
            "generation": 1,
            "observe_called_ns": int(api.now * 1e9),
            "token": {
                "credit_schema": 1,
                "epoch": identity["epoch"],
                "frame_id": 1,
                "chunk_index": 0,
                "offset": 0,
                "count": 65_536,
            },
            "wait_received_ns": int(api.now * 1e9),
            "no_credit_issued": True,
            "no_cancel_issued": True,
            "harness_sha256": coordinator.sources["installed_observe_smoke.py"],
        }
        smoke.write_handshake(
            executor / "death-held.json",
            {"schema": 1, "session": SESSION, "hold": hold},
        )
        if mutation == "none":
            result = coordinator.finish_if_held()
            assert result is not None
            assert result["passed"] is True
            assert result["result"]["passed"] is True
            assert result["result"]["executor_retirement"]["pre_action_identity"] == {
                "pid": 21,
                "created": 123,
                "image": IMAGE,
            }
            assert result["result"]["executor_retirement"]["exit_code"] == 17
            assert set(result["result"]["retirement"]) == {
                "retired",
                "processes",
                "cleanup_errors",
            }
            assert result["result"]["retirement"]["cleanup_errors"] == []
            assert (
                result["result"]["reference"]["root_sha256"]
                == hashlib.sha256(raw).hexdigest()
            )
            assert (
                result["result"]["reference"]["panel_sha256"]
                == hashlib.sha256(panel).hexdigest()
            )
            assert coordinator.request == control.read_object(
                coordinator.output / "death-kill-request.json"
            )
        elif mutation == "actual_close":
            with pytest.raises(
                RuntimeError,
                match=r"Witness exit missing clean proof|Reference native call failure",
            ):
                coordinator.finish_if_held()
            assert finished.wait(2)
            actual_result = control.read_packet(
                coordinator.output / "death-result.json", SESSION, "result"
            )
            assert actual_result["passed"] is False
            assert actual_result["cleanup_errors"]
            assert coordinator.failed
        elif mutation in {"unsupported_event", "owner_nonzero", "owner_nonempty"}:
            with pytest.raises(
                RuntimeError,
                match=r"Reference native call failure|Witness exit missing clean proof",
            ):
                coordinator.finish_if_held()
            assert coordinator.failed
        else:
            with pytest.raises(ValueError, match=r"Death|death|executor retirement"):
                coordinator.finish_if_held()
            assert coordinator.failed
        assert api.actions == [(10_021, 17)]
        assert api.post_exit_image_calls == []
        witness_owner.emergency.assert_not_called()
        gui_owner.emergency.assert_not_called()
        guard.close.assert_called_once()
        assert finished.wait(2)
        assert returns == ([1] if mutation == "actual_close" else [0])
    finally:
        if not finished.is_set():
            api.now = 126.0
        thread.join(3)
        assert not thread.is_alive()
        assert survivor.journal.stream.closed
        # Retained read-only resources and separate executor handle all close.
        if survivor.executor is not None:
            assert survivor.executor.handle is None
        if survivor.pins is not None:
            assert survivor.pins.closed
