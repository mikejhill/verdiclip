"""Two installed read-only frames or frame2 held cancellation; no scrolling.

The independently supervised reference and exact owner profile are reused from
fixed adjacent fixture helpers. Old single-frame schemas and scripts are never
patched. Preparation10s, case15s, absolute25s and nested limits are unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import sys
import time
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

Json = dict[str, Any]
PREPARATION_SECONDS = 10.0
OUTER_SECONDS = 25.0
CASE_SECONDS = 15.0
CALL_SECONDS = 3.0
CLEANUP_SECONDS = 5.0
PEER_SCHEMA = 4
PROTOCOL_VERSION = 3
CREDIT_SCHEMA = 2
MAX_TEMPORARY_PIDS = 2
WITNESS_X = 960
WITNESS_Y = 704


def _adjacent(filename: str, name: str) -> ModuleType:
    """Load a fixed authored support file, never a wire-selected entrypoint."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).with_name(filename)
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Missing fixed sequence fixture support")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


base = _adjacent("installed_observe_smoke.py", "_sequence_reference_support")
Owner = base.Owner
Journal = base.Journal
BaselinePins = base.BaselinePins
ReferenceGuard = base.ReferenceGuard
OWNERSHIP_PHASES = base.OWNERSHIP_PHASES
write = base.write
read = base.read
write_handshake = base.write_handshake
support = base.support
rect = base.rect
fixture_key = base.fixture_key
latest = base.latest
preparation_receipt = base.preparation_receipt
case_handoff = base.case_handoff
qualify_ownership = base.qualify_ownership
topology_pids = base.topology_pids
ownership_packet = base.ownership_packet
ownership_interval = base.ownership_interval
temporary_records = base.temporary_records
validate_temporary_retirement = base.validate_temporary_retirement
track_temporary_member = base.track_temporary_member
retain_membership_failure = base.retain_membership_failure
validate_calls = base.validate_calls
release_owner = base.release_owner
pin_preparation = base.pin_preparation
prepare_owned_case = base.prepare_owned_case
ownership_checkpoint = base.ownership_checkpoint
reference = base.reference
acquire_temporary = base.acquire_temporary


def contract() -> ModuleType:
    """The independent bounded validator is a fixed fixture dependency."""
    return _adjacent("sequence_observe_contract.py", "_sequence_observe_contract")


def provenance(wheel: Path) -> Json:
    """Require independent wheel and loaded parent dependency provenance."""
    verifier = _adjacent("sequence_installed_runtime.py", "_sequence_installed_runtime")
    return verifier.verify_wheel(wheel)


def source_hashes() -> dict[str, str]:
    """Bind the exact fixed executor, verifier and independent validator files."""
    return {
        name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
        for name in (
            "sequence_observe_executor.py",
            "sequence_observe_contract.py",
            "sequence_installed_runtime.py",
        )
    }


def same_wire(actual: object, expected: object) -> bool:
    """Compare received JSON with trusted local JSON representation, types intact."""

    def encoded(value: object) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)

    return encoded(actual) == encoded(expected)


def case_index(case: str) -> int | None:
    """Only the two reviewed fixed cases are executable."""
    if case == "success":
        return None
    if case == "cancel-frame2":
        return 0
    raise ValueError("Unsupported fixed sequence case")


def _ready_profile(peer: Json) -> None:
    """Do not adapt sequence identity into an old-profile ready packet."""
    if set(peer) != {
        "pid",
        "executable",
        "epoch",
        "protocol_version",
        "peer_schema",
        "credit_schema",
        "sequence_schema",
        "runtime_schema",
        "max_frames",
        "max_requests",
    }:
        raise ValueError("Sequence ready keys differ")
    for name, expected in (
        ("protocol_version", 3),
        ("peer_schema", 4),
        ("credit_schema", 2),
        ("sequence_schema", 1),
        ("runtime_schema", 5),
        ("max_frames", 8),
        ("max_requests", 32),
    ):
        if type(peer[name]) is not int or peer[name] != expected:
            raise ValueError("Sequence ready profile differs")


def diagnose_startup_failure(*_args: object) -> Json:
    """Never reinterpret the old optional diagnostic bootstrap as schema5."""
    return {
        "available": False,
        "reason": "Optional legacy startup diagnostic is not a sequence-profile proof",
    }


def serve_temporary(  # noqa: PLR0913, PLR0917 - fixed handshake
    output: Path,
    session: str,
    gui: Owner,
    baseline: Json,
    seen: set[int],
    deadline: float,
) -> Json | None:
    """Select bounded targets only; this exchange never authorizes observation."""
    path = output / "temporary-request.json"
    if not path.exists():
        return None
    request = ownership_packet(read(path), session, "request")
    peer = ownership_packet(read(output / "peer-identity.json"), session, "peer")
    _ready_profile(peer)
    if (
        set(request) != {"peer", "requested", "deadline"}
        or request["peer"] != peer
        or request["deadline"] != deadline
        or type(peer.get("pid")) is not int
        or not 0 < peer["pid"] < 2**32
        or peer.get("executable") != baseline["request"]["expected_base"]
        or type(peer.get("protocol_version")) is not int
        or type(peer.get("peer_schema")) is not int
        or type(peer.get("credit_schema")) is not int
        or peer.get("protocol_version") != PROTOCOL_VERSION
        or peer.get("peer_schema") != PEER_SCHEMA
        or peer.get("credit_schema") != CREDIT_SCHEMA
        or not isinstance(peer.get("epoch"), str)
        or re.fullmatch(r"[0-9a-f]{32}", peer["epoch"]) is None
    ):
        raise ValueError("Temporary request lacks exact channel identity")
    ownership_interval(
        {"started": request["requested"], "finished": request["requested"]},
        deadline - CASE_SECONDS,
        deadline,
    )
    target_path = output / "temporary-targets.json"
    if not target_path.exists():
        begin = time.monotonic()
        if begin >= deadline:
            raise RuntimeError("Late temporary target snapshot")
        status = gui.snapshot()
        end = time.monotonic()
        base = set(baseline["request"]["pids"])
        extra = set(topology_pids(status["members"])) - base
        if (
            end >= deadline
            or status["exit_code"] is not None
            or not base <= set(topology_pids(status["members"]))
            or not 1 <= len(extra) <= MAX_TEMPORARY_PIDS
            or peer["pid"] not in extra
            or seen - extra
        ):
            raise RuntimeError("Temporary target membership/history changed")
        write_handshake(
            target_path,
            {
                "schema": 1,
                "session": session,
                "targets": {
                    "peer": peer,
                    "pids": sorted(extra),
                    "deadline": deadline,
                    "started": begin,
                    "finished": end,
                    "snapshot": status,
                },
            },
        )
    pinned = output / "temporary-pinned.json"
    if not pinned.exists():
        return None
    value = ownership_packet(read(pinned), session, "profile")
    roles = temporary_records(value, peer, baseline)
    targets = ownership_packet(read(target_path), session, "targets")
    if (
        value.get("roles") != roles
        or set(roles.values()) != set(targets["pids"])
        or seen - set(roles.values())
    ):
        raise RuntimeError("Frozen temporary roles or history changed")
    if time.monotonic() >= deadline:
        raise RuntimeError("Late temporary role admission")
    return value


def serve_checkpoint(  # noqa: C901, PLR0913, PLR0917 - explicit fixed ownership scope
    output: Path,
    session: str,
    phase: str,
    gui: Owner,
    profile: Json,
    deadline: float,
    temporary: Json | None = None,
    *,
    cancelled: bool = False,
) -> Json | None:
    """Only existing channel-attested helper identity may extend the baseline set."""
    path = output / f"ownership-{phase}-request.json"
    if not path.exists():
        return None
    request = ownership_packet(read(path), session, "request")
    if (
        set(request) != {"phase", "deadline", "peer", "requested"}
        or request["phase"] != phase
        or request["deadline"] != deadline
    ):
        raise ValueError("Ownership checkpoint request differs")
    stamp = request["requested"]
    if (
        type(stamp) not in (int, float)
        or not math.isfinite(stamp)
        or not deadline - CASE_SECONDS <= stamp <= time.monotonic() < deadline
    ):
        raise ValueError("Ownership checkpoint request timing invalid")
    wanted = set(profile["request"]["pids"])
    if phase == "pre-observe":
        peer = ownership_packet(read(output / "peer-identity.json"), session, "peer")
        _ready_profile(peer)
        if (
            request["peer"] != peer
            or type(peer.get("pid")) is not int
            or not 0 < peer["pid"] < 2**32
            or peer["pid"] in wanted
            or peer.get("executable") != profile["request"]["expected_base"]
            or type(peer.get("protocol_version")) is not int
            or type(peer.get("peer_schema")) is not int
            or type(peer.get("credit_schema")) is not int
            or peer.get("protocol_version") != PROTOCOL_VERSION
            or peer.get("peer_schema") != PEER_SCHEMA
            or peer.get("credit_schema") != CREDIT_SCHEMA
            or not isinstance(peer.get("epoch"), str)
            or re.fullmatch(r"[0-9a-f]{32}", peer["epoch"]) is None
        ):
            raise ValueError("Temporary member is not the attested direct helper")
        wanted.update(
            temporary["roles"].values() if temporary is not None else [peer["pid"]]
        )
    elif request["peer"] is not None:
        raise ValueError("Unexpected temporary peer at baseline checkpoint")
    if phase == "pre-observe" and (
        temporary is None
        or temporary_records(temporary, request["peer"], profile) != temporary["roles"]
    ):
        raise RuntimeError("Missing pinned temporary profile")
    if phase == "post-terminal":
        if temporary is None:
            raise RuntimeError("Missing frozen temporary profile")
        value = ownership_packet(
            read(output / "temporary-retired.json"), session, "retired"
        )
        validate_temporary_retirement(value, temporary, cancelled=cancelled)
    begin = time.monotonic()
    if begin >= deadline:
        raise RuntimeError("Late ownership checkpoint")
    status = gui.snapshot()
    end = time.monotonic()
    if (
        end >= deadline
        or status["exit_code"] is not None
        or set(topology_pids(status["members"])) != wanted
    ):
        raise RuntimeError("Unexpected member at stable ownership checkpoint")
    ack = {
        "phase": phase,
        "deadline": deadline,
        "started": begin,
        "finished": end,
        "snapshot": status,
    }
    write_handshake(
        output / f"ownership-{phase}-ack.json",
        {"schema": 1, "session": session, "ack": ack},
    )
    return ack


def outer(args: argparse.Namespace) -> int:  # noqa: C901, PLR0912, PLR0915 - fixed supervised two-case proof
    """No MSS/UIA/reference read here: only owned process polls and bounded files."""
    module = support()
    runtime_provenance = provenance(args.wheel)
    from verdiclip.platform.owned_helper import (  # noqa: PLC0415 - installed runtime
        HelperCommand,
    )

    held_index = case_index(getattr(args, "case", "success"))
    output = args.output
    output.mkdir(parents=True, exist_ok=False)
    states = output / "states"
    states.mkdir()
    executor = output / "executor"
    executor.mkdir()
    result: Json = {
        "passed": False,
        "first_failure": None,
        "provenance": runtime_provenance,
        "fixture_sha256": hashlib.sha256(args.fixture.read_bytes()).hexdigest(),
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "candidate_head": os.environ.get("QUALIFICATION_HEAD"),
        "credit_case": getattr(args, "case", "success"),
        "cancel_validator_sha256": hashlib.sha256(
            Path(__file__).with_name("sequence_observe_contract.py").read_bytes()
        ).hexdigest()
        if held_index is not None
        else None,
    }
    result["source_sha256"] = source_hashes()
    owners = {}
    started = time.monotonic()
    preparation_deadline = started + PREPARATION_SECONDS
    absolute_deadline = started + OUTER_SECONDS
    deadline = preparation_deadline
    session = uuid.uuid4().hex
    case_start = None
    profile: Json = {}
    checkpoints: list[Json] = []
    peer_pid = None
    provisional_members: set[int] = set()
    temporary_profile: Json | None = None
    expected_launch = str(Path(sys.executable).resolve())
    result.update(
        contract="already-open-panel-explicit-ownership-v2",
        preparation_start=started,
        preparation_deadline=preparation_deadline,
        absolute_deadline=absolute_deadline,
        cold_startup_in_capture_latency=False,
        session=session,
    )
    try:
        fixture = module.ExternalOwner()
        owners["fixture"] = fixture
        fixture.start(
            HelperCommand(str(args.fixture), (str(states), "observe-only"), str(output))
        )
        if fixture.process is None:
            raise RuntimeError("Fixture process not retained")  # noqa: TRY301 - preserve first failure through owned cleanup
        gui = module.ExternalOwner()
        owners["gui"] = gui
        gui.start(
            HelperCommand(
                expected_launch,
                (
                    "-I",
                    str(Path(__file__).resolve()),
                    "--child",
                    "--case",
                    getattr(args, "case", "success"),
                    "--wheel",
                    str(args.wheel),
                    "--output",
                    str(executor),
                    "--states",
                    str(states),
                    "--fixture-pid",
                    str(fixture.process.pid),
                    "--preparation-start",
                    str(started),
                    "--session",
                    session,
                ),
                str(output),
            )
        )
        while time.monotonic() < deadline:
            snapshot_started = time.monotonic()
            status = gui.snapshot()
            snapshot_finished = time.monotonic()
            validate_calls(
                executor / "calls.jsonl",
                started,
                time.monotonic,
                finished=status["exit_code"] is not None,
                deadline=deadline,
            )
            ready_path = executor / "preparation-ready.json"
            if (
                case_start is None
                and ready_path.exists()
                and (status["exit_code"] is None)
            ):
                prepared = preparation_receipt(
                    read(ready_path),
                    session,
                    fixture.process.pid,
                    started,
                    time.monotonic(),
                )
                result["preparation_ownership"] = {
                    "session": session,
                    "ready": prepared["ready"],
                    "executor_pid": prepared["executor_pid"],
                    "gui_launch_pid": None if gui.process is None else gui.process.pid,
                    "gui_snapshot": status,
                    "gui_snapshot_started": snapshot_started,
                    "gui_snapshot_finished": snapshot_finished,
                }
                if gui.process is None:
                    raise RuntimeError("Missing retained executor launch")  # noqa: TRY301 - preserve first failure through owned cleanup
                profile = qualify_ownership(
                    executor,
                    session,
                    status,
                    snapshot_finished,
                    gui,
                    started,
                    deadline,
                    gui.process.pid,
                    prepared["executor_pid"],
                    expected_launch,
                    runtime_provenance["executable"],
                )
                result["ownership_profile"] = profile
                fixture_state = fixture.snapshot()
                result["preparation_ownership"].update(
                    fixture_launch_pid=fixture.process.pid,
                    fixture_snapshot=fixture_state,
                )
                if fixture_state["exit_code"] is not None or fixture_state[
                    "members"
                ] != [fixture.process.pid]:
                    raise RuntimeError("Prepared fixture no longer solely owned/alive")  # noqa: TRY301 - preserve first failure through owned cleanup
                case_start = time.monotonic()
                if case_start >= preparation_deadline:
                    raise RuntimeError("Late preparation admission")  # noqa: TRY301 - preserve first failure through owned cleanup
                deadline = min(case_start + CASE_SECONDS, absolute_deadline)
                handoff = {
                    "schema": 1,
                    "session": session,
                    "case_start": case_start,
                    "deadline": deadline,
                }
                case_handoff(handoff, session, prepared["ready"], started, case_start)
                write_handshake(executor / "case-start.json", handoff)
                if time.monotonic() >= preparation_deadline:
                    raise RuntimeError("Late preparation handoff publication")  # noqa: TRY301 - preserve first failure through owned cleanup
                result.update(
                    preparation_ready=prepared["ready"],
                    case_start=case_start,
                    case_deadline=deadline,
                    preparation=prepared,
                )
            if case_start is not None:
                if status["exit_code"] is None:
                    context = {
                        "schema": 1,
                        "session": session,
                        "snapshot": status,
                        "snapshot_started": snapshot_started,
                        "snapshot_finished": snapshot_finished,
                        "baseline": list(profile["request"]["pids"]),
                        "provisional_before": sorted(provisional_members),
                        "provisional_union": sorted(
                            provisional_members
                            | set(topology_pids(status["members"]))
                            - set(profile["request"]["pids"])
                        ),
                        "peer_pid": peer_pid,
                        "completed_phases": [item["phase"] for item in checkpoints],
                        "preparation_start": started,
                        "case_deadline": deadline,
                        "absolute_deadline": absolute_deadline,
                    }
                    try:
                        track_temporary_member(
                            set(topology_pids(status["members"])),
                            set(profile["request"]["pids"]),
                            provisional_members,
                            peer_pid,
                            retiring=len(checkpoints) == len(OWNERSHIP_PHASES),
                            admitted=set(temporary_profile["roles"].values())
                            if temporary_profile is not None
                            else None,
                        )
                    except Exception as error:
                        context["failure_time"] = time.monotonic()
                        retain_membership_failure(output, result, context, error)
                        phase_evidence = {}
                        for name in ("peer-identity", "ownership-pre-observe-request"):
                            try:
                                path = executor / f"{name}.json"
                                phase_evidence[name] = (
                                    read(path) if path.exists() else None
                                )
                            except Exception as read_error:  # noqa: BLE001 - retain primary failure
                                phase_evidence[name] = {
                                    "error": repr(read_error)[:2048]
                                }
                        result["failure_phase_evidence"] = phase_evidence
                        try:
                            write_handshake(
                                output / "failure-phase-evidence.json",
                                {
                                    "session": session,
                                    "observed": time.monotonic(),
                                    "evidence": phase_evidence,
                                },
                            )
                        except Exception as persistence_error:  # noqa: BLE001 - retain primary failure
                            result["phase_persistence_error"] = repr(persistence_error)[
                                :2048
                            ]
                        result["startup_diagnostics"] = diagnose_startup_failure(
                            output,
                            executor,
                            session,
                            context,
                            gui,
                            module,
                            owners,
                            runtime_provenance,
                            args.wheel,
                            deadline,
                            absolute_deadline,
                        )
                        raise
                if len(checkpoints) < len(OWNERSHIP_PHASES):
                    phase = OWNERSHIP_PHASES[len(checkpoints)]
                    if phase == "pre-observe" and temporary_profile is None:
                        temporary_profile = serve_temporary(
                            executor,
                            session,
                            gui,
                            profile,
                            provisional_members,
                            deadline,
                        )
                        if temporary_profile is not None:
                            peer_pid = temporary_profile["peer"]["pid"]
                            result["temporary_profile"] = temporary_profile
                    if (
                        phase == "pre-observe"
                        and (executor / "ownership-pre-observe-request.json").exists()
                        and (
                            temporary_profile is None
                            or provisional_members
                            - set(temporary_profile["roles"].values())
                        )
                    ):
                        raise RuntimeError(  # noqa: TRY301 - preserve first failure through owned cleanup
                            "A provisional member was not the attested helper"
                        )
                    ack = serve_checkpoint(
                        executor,
                        session,
                        phase,
                        gui,
                        profile,
                        deadline,
                        temporary_profile,
                        cancelled=case_index(getattr(args, "case", "success"))
                        is not None,
                    )
                    if ack is not None:
                        checkpoints.append(ack)
                        result["ownership_checkpoints"] = checkpoints
                        if phase == "pre-observe":
                            peer = ownership_packet(
                                read(executor / "peer-identity.json"), session, "peer"
                            )
                            peer_pid = peer["pid"]
                            result["attested_peer"] = peer
            if status["exit_code"] is not None and (not status["members"]):
                value = read(executor / "result.json")
                validate_calls(
                    executor / "calls.jsonl",
                    started,
                    time.monotonic,
                    finished=True,
                    deadline=deadline,
                )
                result.update(child=value, gui=status)
                if (
                    case_start is None
                    or len(checkpoints) != len(OWNERSHIP_PHASES)
                    or value.get("temporary_profile") != temporary_profile
                    or (value.get("temporary_pin_cleanup") != [])
                    or (value.get("ownership_profile") != profile)
                    or (not isinstance(value.get("baseline_pin_cleanup"), dict))
                    or (value["baseline_pin_cleanup"].get("cleanup_errors") != [])
                    or (
                        value["baseline_pin_cleanup"].get("processes")
                        != profile["records"]["processes"]
                    )
                    or (status["exit_code"] != 0)
                    or (value.get("passed") is not True)
                    or (value.get("credit_case") != getattr(args, "case", "success"))
                    or (
                        value.get("cancel_validator_sha256")
                        != result["cancel_validator_sha256"]
                    )
                    or not same_wire(value.get("provenance"), runtime_provenance)
                    or value.get("source_sha256") != result["source_sha256"]
                    or (value.get("case_start") != case_start)
                    or (value.get("case_deadline") != deadline)
                ):
                    raise RuntimeError(  # noqa: TRY301 - preserve first failure through owned cleanup
                        f"Observe executor failed: {value.get('first_failure')}"
                    )
                if time.monotonic() >= deadline:
                    raise RuntimeError("Late observe receipt")  # noqa: TRY301 - preserve first failure through owned cleanup
                (states / "stop").write_bytes(b"")
                while time.monotonic() < deadline:
                    fs = fixture.snapshot()
                    if fs["exit_code"] is not None and (not fs["members"]):
                        result["fixture_exit"] = fs
                        result["passed"] = (
                            fs["exit_code"] == 0 and time.monotonic() < deadline
                        )
                        break
                    time.sleep(0.01)
                break
            time.sleep(0.01)
        if not result["passed"]:
            raise RuntimeError("No timely natural successful observation")  # noqa: TRY301 - preserve first failure through owned cleanup
    except Exception as error:  # noqa: BLE001 - preserve first failure and cleanup
        result["first_failure"] = repr(error)
    finally:
        result["cleanup"] = {
            name: release_owner(owner, deadline)
            for name, owner in reversed(tuple(owners.items()))
        }
        if any(not x["confirmed"] or x["forced"] for x in result["cleanup"].values()):
            result["passed"] = False
        result["elapsed_seconds"] = time.monotonic() - started
        write(output / "receipt.json", result)
    return int(not result["passed"])


def child(args: argparse.Namespace) -> int:
    """Use only the fixed adjacent supervised sequence executor."""
    executor = _adjacent("sequence_observe_executor.py", "_sequence_observe_executor")
    result = executor.child(args)
    if type(result) is not int:
        raise RuntimeError("Executor returned malformed status")
    return result


def main() -> int:
    """Two fixed cases only; no arbitrary launch or provider overrides."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument(
        "--case", choices=("success", "cancel-frame2"), default="success"
    )
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--states", type=Path)
    parser.add_argument("--fixture-pid", type=int)
    parser.add_argument("--preparation-start", type=float)
    parser.add_argument("--session")
    args = parser.parse_args()
    if (
        sys.platform != "win32"
        or os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
        or re.fullmatch(r"[0-9a-f]{40}", os.environ.get("QUALIFICATION_HEAD", ""))
        is None
    ):
        parser.error("Sequence proof requires exact-head disposable Windows CI")
    args.wheel = args.wheel.resolve(strict=True)
    args.output = args.output.resolve()
    if args.child:
        try:
            return child(args)
        except Exception as error:  # noqa: BLE001 - retain pre-event-loop failure
            write(args.output / "fatal.json", {"first_failure": repr(error)})
            return 1
    if args.fixture is None:
        parser.error("--fixture required for outer owner")
    args.fixture = args.fixture.resolve(strict=True)
    return outer(args)


if __name__ == "__main__":
    raise SystemExit(main())
