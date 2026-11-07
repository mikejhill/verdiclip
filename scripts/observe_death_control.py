"""Outer held-credit death coordinator: bounded files and owned job polls only.

Native/image/termination/reference work belongs to the separate witness. This
owner never terminates anything to obtain a passing verdict and never closes a
job before admission. Existing outer cleanup retains responsibility for owners.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

Json = dict[str, Any]
MAX_JSON = 131_072
RETIRE_SECONDS = 5.0
DEATH_EXIT = 17
DIGEST_CHARS = 64
MAX_ENUMERATION_ENTRIES = 4096
CALL_SECONDS = 3.0

# This fixture-only suffix runs only after the exact installed runtime attests.
# The sole script is selected by this module, never by received metadata.
_WITNESS_SUFFIX = r"""
import runpy
witness = Path(sys.argv[2])
if witness.name != "observe_death_witness.py" or not witness.is_absolute():
    raise RuntimeError("Unexpected fixed death witness script")
sys.argv = sys.argv[2:]
runpy.run_path(str(witness), run_name="__main__")
"""


def _unique(pairs: list[tuple[str, object]]) -> Json:
    result: Json = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate death evidence field")
        result[key] = value
    return result


def read_object(path: Path) -> Json:
    """Bound every evidence read and reject duplicate keys at all depths."""
    with path.open("rb") as stream:
        raw = stream.read(MAX_JSON + 1)
    if len(raw) > MAX_JSON:
        raise ValueError("Death evidence exceeds bound")
    value = json.loads(raw, object_pairs_hook=_unique)
    if type(value) is not dict:
        raise ValueError("Death evidence is not an object")
    return cast("Json", value)


def read_packet(path: Path, session: str, field: str) -> Json:
    """Read one bounded immutable packet, rejecting duplicate/extra fields."""
    value = read_object(path)
    if (
        set(value) != {"schema", "session", field}
        or type(value["schema"]) is not int
        or value["schema"] != 1
        or value["session"] != session
        or type(value[field]) is not dict
    ):
        raise ValueError("Death evidence envelope differs")
    return cast("Json", value[field])


def canonical_provenance(value: Json) -> Json:
    """Snapshot trusted verifier output using its explicit JSON wire shape.

    InstalledRuntime/asdict retains tuples, while the witness packet decodes
    them as lists. Normalize only this local expectation, never received data
    or the strict evidence comparator.
    """
    raw = json.dumps(value, allow_nan=False).encode("utf-8")
    if len(raw) > MAX_JSON:
        raise ValueError("Death provenance exceeds bound")
    decoded = json.loads(raw, object_pairs_hook=_unique)
    if type(decoded) is not dict:
        raise ValueError("Death provenance is not an object")
    return cast("Json", decoded)


def _time(value: object) -> float:
    if (
        not isinstance(value, (float, int))
        or type(value) is bool
        or not math.isfinite(value)
    ):
        raise ValueError("Death evidence time differs")
    return float(value)


def _same(actual: object, expected: object) -> None:
    """JSON equality also requires exact scalar types; booleans are not integers."""
    if type(actual) is not type(expected):
        raise ValueError("Death evidence type differs")
    if isinstance(expected, dict):
        if not isinstance(actual, dict) or set(actual) != set(expected):
            raise ValueError("Death evidence fields differ")
        for key, item in expected.items():
            _same(actual[key], item)
    elif isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            raise ValueError("Death evidence list differs")
        for left, right in zip(actual, expected, strict=True):
            _same(left, right)
    elif actual != expected:
        raise ValueError("Death evidence value differs")


def validate_hold(
    hold: Json, session: str, identity: Json, source: str, now_ns: int
) -> None:
    """Only the fixed source's first ungranted public wait admits one death action."""
    called, received = hold.get("observe_called_ns"), hold.get("wait_received_ns")
    if (
        type(called) is not int
        or type(received) is not int
        or type(now_ns) is not int
        or not 0 < called <= received <= now_ns < called + 2_000_000_000
    ):
        raise ValueError("Late or invalid held-credit timing")
    _same(
        hold,
        {
            "session": session,
            "generation": 1,
            "observe_called_ns": called,
            "token": {
                "credit_schema": 1,
                "epoch": identity["epoch"],
                "frame_id": 1,
                "chunk_index": 0,
                "offset": 0,
                "count": 65536,
            },
            "wait_received_ns": received,
            "no_credit_issued": True,
            "no_cancel_issued": True,
            "harness_sha256": source,
        },
    )


class DeathControl:
    """One witness owner and one death request, with no retries or fallback success."""

    def __init__(  # noqa: PLR0913 - explicit fixed owned inputs
        self,
        harness: Any,  # noqa: ANN401 - fixed dynamically loaded harness module
        support: Any,  # noqa: ANN401 - fixed dynamically loaded owner module
        owners: dict[str, Any],
        *,
        output: Path,
        executor: Path,
        states: Path,
        wheel: Path,
        session: str,
        started: float,
        provenance: Json,
    ) -> None:
        from verdiclip.platform import helper_bootstrap, helper_runtime  # noqa: PLC0415
        from verdiclip.platform.owned_helper import HelperCommand  # noqa: PLC0415

        self.harness, self.owners = harness, owners
        self.output, self.executor, self.session = (
            output / "death-witness",
            executor,
            session,
        )
        self.started = started
        self.preparation_deadline = started + 10.0
        self.absolute_deadline = started + 25.0
        self.deadline = self.preparation_deadline
        self.retirement_deadline: float | None = None
        self.provenance = canonical_provenance(provenance)
        self.sources = {
            name: hashlib.sha256(
                Path(__file__).with_name(name).read_bytes()
            ).hexdigest()
            for name in (
                "observe_death_witness.py",
                "observe_death_control.py",
                "installed_observe_smoke.py",
                "installed_channel_smoke.py",
                "installed_runtime_smoke.py",
                "observe_process_diagnostics.py",
            )
        }
        self.begin: Json | None = None
        self.pinned: Json | None = None
        self.request: Json | None = None
        self.ready: Json | None = None
        self.case_started = False
        self.failed = False
        self.last_membership: Json | None = None
        self.output.mkdir(exist_ok=False)
        self.owner = support.ExternalOwner()
        owners["death-witness"] = self.owner
        if time.monotonic() >= self.preparation_deadline:
            raise RuntimeError("Late death witness launch")
        runtime = helper_runtime.inspect_runtime()
        _same(runtime.executable, provenance["executable"])
        arguments = helper_bootstrap.proof_arguments(runtime.serialize())
        if time.monotonic() >= self.preparation_deadline:
            raise RuntimeError("Late death bootstrap construction")
        self.owner.start(
            HelperCommand(
                runtime.executable,
                (
                    *arguments[:3],
                    arguments[3] + _WITNESS_SUFFIX,
                    arguments[4],
                    str(Path(__file__).with_name("observe_death_witness.py").resolve()),
                    "--wheel",
                    str(wheel),
                    "--output",
                    str(self.output),
                    "--states",
                    str(states),
                    "--session",
                    session,
                    "--fixture-pid",
                    str(owners["fixture"].process.pid),
                    "--preparation-start",
                    str(started),
                ),
                str(output),
            )
        )
        if time.monotonic() >= self.preparation_deadline:
            raise RuntimeError("Late death witness launch completion")

    def failure_context(self) -> Json:
        """Preserve observed evidence before fallback without additional queries."""
        return deepcopy(
            {
                "ready": self.ready,
                "begin": self.begin,
                "pinned": self.pinned,
                "request": self.request,
                "last_membership": self.last_membership,
                "deadline": self.deadline,
                "retirement_deadline": self.retirement_deadline,
                "sources": self.sources,
                "provenance": self.provenance,
            }
        )

    def _poll(self, *, finished: bool = False) -> Json:
        if self.failed or time.monotonic() >= self.deadline:
            raise RuntimeError("Death control failed or deadline expired")
        status = self.owner.snapshot()
        self.harness.validate_calls(
            self.output / "death-calls.jsonl",
            self.started,
            time.monotonic,
            finished=finished or status["exit_code"] is not None,
            deadline=self.deadline,
        )
        if time.monotonic() >= self.deadline:
            raise RuntimeError("Late death witness snapshot")
        return cast("Json", status)

    def preparation_ready(self) -> bool:
        """Poll readiness without granting extra startup or preparation time."""
        if self.ready is not None:
            return True
        status = self._poll()
        if status["exit_code"] is not None:
            raise RuntimeError("Death witness exited before preparation")
        path = self.output / "death-ready.json"
        if not path.exists():
            return False
        ready = read_packet(path, self.session, "ready")
        if set(ready) != {"pid", "ready", "provenance", "sources"}:
            raise ValueError("Death readiness fields differ")
        _same(ready["pid"], self.owner.process.pid)
        _same(ready["provenance"], self.provenance)
        _same(ready["sources"], self.sources)
        if (
            not self.started
            <= _time(ready["ready"])
            <= time.monotonic()
            < self.preparation_deadline
        ):
            raise ValueError("Late death witness readiness")
        self.ready = ready
        return True

    def _live_gui(self) -> Json:
        if self.begin is None:
            raise RuntimeError("Missing death begin")
        expected = set(self.begin["baseline_profile"]["request"]["pids"])
        expected.update(self.begin["temporary_profile"]["roles"].values())
        start = time.monotonic()
        status = self.owners["gui"].snapshot()
        end = time.monotonic()
        self.last_membership = {
            "started": start,
            "finished": end,
            "snapshot": deepcopy(status),
        }
        if (
            end >= self.deadline
            or status["exit_code"] is not None
            or set(self.harness.topology_pids(status["members"])) != expected
        ):
            raise RuntimeError("Changed or late live GUI job membership")
        return {"started": start, "finished": end, "snapshot": status}

    def before_observe(
        self,
        profile: Json,
        temporary: Json,
        identity: Json,
        deadline: float,
    ) -> bool:
        """Freeze the original reference once; ACK only after pinned/live recheck."""
        if self.ready is None or deadline > self.absolute_deadline:
            raise RuntimeError("Unprepared death witness or extended deadline")
        if self.begin is None:
            self.deadline = deadline
            reference = read_object(self.executor / "death-reference.json")
            if (
                set(reference)
                != {
                    "schema",
                    "session",
                    "fixture",
                    "expected",
                    "reference_initial",
                    "before_rgb_sha256",
                    "before_rgb_byte_count",
                }
                or type(reference["schema"]) is not int
                or reference["schema"] != 1
                or reference["session"] != self.session
            ):
                raise ValueError("Death reference envelope differs")
            self.begin = {
                "schema": 1,
                "session": self.session,
                "case_deadline_ns": int(deadline * 1_000_000_000),
                "absolute_deadline_ns": int(self.absolute_deadline * 1_000_000_000),
                "identity": deepcopy(identity),
                "baseline_profile": deepcopy(profile),
                "temporary_profile": deepcopy(temporary),
                **reference,
            }
            self.harness.write_handshake(self.output / "death-begin.json", self.begin)
        elif (
            profile != self.begin["baseline_profile"]
            or temporary != self.begin["temporary_profile"]
            or identity != self.begin["identity"]
            or deadline != self.deadline
        ):
            raise ValueError("Frozen death begin changed")
        status = self._poll()
        if status["exit_code"] is not None:
            raise RuntimeError("Death witness exited before pins")
        path = self.output / "death-pinned.json"
        if not path.exists():
            return False
        pinned = read_packet(path, self.session, "pinned")
        roles = {"executor": profile["roles"]["executor"], **temporary["roles"]}
        records = [
            r
            for r in profile["records"]["processes"]
            if r["process_id"] == roles["executor"]
        ]
        records.extend(temporary["records"]["processes"])
        stamp = pinned.get("pinned_ns")
        if type(stamp) is not int or not int(
            self.started * 1e9
        ) <= stamp <= time.monotonic_ns() < int(deadline * 1e9):
            raise ValueError("Late or invalid death pins")
        enumeration = pinned.get("records", {}).get("enumeration", {})
        if (
            set(enumeration)
            != {
                "started_monotonic",
                "finished_monotonic",
                "entries",
                "complete",
                "error",
            }
            or enumeration["complete"] is not True
            or enumeration["error"] is not None
            or type(enumeration["entries"]) is not int
            or not 0 < enumeration["entries"] <= MAX_ENUMERATION_ENTRIES
        ):
            raise ValueError("Incomplete death pin enumeration")
        enum_start, enum_end = (
            _time(enumeration["started_monotonic"]),
            _time(enumeration["finished_monotonic"]),
        )
        if (
            not self.started <= enum_start <= enum_end <= stamp / 1e9
            or enum_end - enum_start >= CALL_SECONDS
        ):
            raise ValueError("Late death pin enumeration")
        _same(
            pinned,
            {
                "roles": roles,
                "records": {
                    "processes": records,
                    "cleanup_errors": [],
                    "enumeration": enumeration,
                },
                "reference_initial": self.begin["reference_initial"],
                "pinned_ns": stamp,
            },
        )
        self._live_gui()
        self.pinned = pinned
        return True

    def finish_if_held(self) -> Json | None:
        """Only a valid hold starts bounded retirement; failure never retries death."""
        path = self.executor / "death-held.json"
        if not path.exists():
            handoff = self.executor / "case-start.json"
            if self.begin is None and not self.case_started and handoff.exists():
                value = self.harness.read(handoff)
                self.deadline = self.harness.case_handoff(
                    value,
                    self.session,
                    self.ready["ready"] if self.ready is not None else 0,
                    self.started,
                    time.monotonic(),
                )
                self.case_started = True
            status = self._poll()
            if status["exit_code"] is not None:
                raise RuntimeError("Death witness exited without held proof")
            return None
        if (
            self.failed
            or self.request is not None
            or self.begin is None
            or self.pinned is None
        ):
            raise RuntimeError("Repeated or unprepared death request")
        try:
            return self._finish(read_packet(path, self.session, "hold"))
        except BaseException:
            self.failed = True
            raise

    def _finish(self, hold: Json) -> Json:
        if self.begin is None:
            raise RuntimeError("Missing frozen death begin")
        validate_hold(
            hold,
            self.session,
            self.begin["identity"],
            self.sources["installed_observe_smoke.py"],
            time.monotonic_ns(),
        )
        if self.pinned is None or hold["observe_called_ns"] < self.pinned["pinned_ns"]:
            raise ValueError("Observe preceded live witness pins")
        live = self._live_gui()
        validate_hold(
            hold,
            self.session,
            self.begin["identity"],
            self.sources["installed_observe_smoke.py"],
            time.monotonic_ns(),
        )
        requested = time.monotonic_ns()
        validate_hold(
            hold,
            self.session,
            self.begin["identity"],
            self.sources["installed_observe_smoke.py"],
            requested,
        )
        self.request = {
            "schema": 1,
            "session": self.session,
            "requested_ns": requested,
            "hold": deepcopy(hold),
        }
        retire_ns = min(
            requested + 5_000_000_000,
            self.begin["case_deadline_ns"],
            self.begin["absolute_deadline_ns"],
        )
        self.retirement_deadline = self.deadline = retire_ns / 1_000_000_000
        self.harness.write_handshake(
            self.output / "death-kill-request.json", self.request
        )
        while time.monotonic() < self.deadline:
            witness = self._poll()
            gui = self.owners["gui"].snapshot()
            if time.monotonic() >= self.deadline:
                raise RuntimeError("Late death retirement snapshot")
            path = self.output / "death-result.json"
            if witness["exit_code"] is not None:
                if witness["exit_code"] != 0 or witness["members"] or not path.exists():
                    raise RuntimeError("Witness exit missing clean proof")
                result = read_packet(path, self.session, "result")
                self._admit_result(result, retire_ns)
                if gui["exit_code"] is not None and not gui["members"]:
                    final = self._poll(finished=True)
                    _same(final, witness)
                    return {
                        "passed": True,
                        "gui": gui,
                        "witness": witness,
                        "live_gui": live,
                        "hold": hold,
                        "begin": self.begin,
                        "result": result,
                        "sources": self.sources,
                        "provenance": self.provenance,
                        "retirement_deadline": self.retirement_deadline,
                    }
            time.sleep(0.01)
        raise RuntimeError("Death retirement exceeded original deadline")

    def _admit_result(self, result: Json, retire_ns: int) -> None:  # noqa: C901, PLR0912 - independent proof gates
        if self.begin is None or self.pinned is None or self.request is None:
            raise RuntimeError("Missing frozen death evidence")
        if set(result) != {
            "passed",
            "failure",
            "cleanup_errors",
            "completed_ns",
            "provenance",
            "sources",
            "begin",
            "request",
            "retirement_deadline_ns",
            "action",
            "retirement",
            "reference",
        }:
            raise ValueError("Death result fields differ")
        for key, expected in {
            "passed": True,
            "failure": None,
            "cleanup_errors": [],
            "begin": self.begin,
            "request": self.request,
            "retirement_deadline_ns": retire_ns,
            "provenance": self.provenance,
            "sources": self.sources,
        }.items():
            _same(result.get(key), expected)
        action = result.get("action", {})
        before, after = action.get("before_ns"), action.get("after_ns")
        completed = result.get("completed_ns")
        limit = min(
            self.request["hold"]["observe_called_ns"] + 2_000_000_000,
            self.begin["case_deadline_ns"],
            self.begin["absolute_deadline_ns"],
        )
        if (
            type(before) is not int
            or type(after) is not int
            or type(completed) is not int
            or not self.request["requested_ns"] <= before <= after < limit
            or not after <= completed <= time.monotonic_ns() < retire_ns
        ):
            raise ValueError("Late or invalid death action/result")
        _same(
            action,
            {
                "attempted": True,
                "returned": True,
                "before_ns": before,
                "after_ns": after,
            },
        )
        retired = result.get("retirement", {})
        _same(retired.get("retired"), expected=True)
        _same(retired.get("cleanup_errors"), [])
        if set(retired) != {"retired", "cleanup_errors", "processes"}:
            raise ValueError("Death retirement fields differ")
        records = retired.get("processes", [])
        expected_records = self.pinned["records"]["processes"]
        if len(records) != len(expected_records):
            raise ValueError("Missing or extra retired death pins")
        for record, original in zip(records, expected_records, strict=True):
            for key in (
                "requested_pid",
                "process_id",
                "creation_100ns",
                "image_path",
                "parent",
            ):
                _same(record.get(key), original[key])
            for key, expected in {
                "observed_process_id": original["process_id"],
                "observed_creation_100ns": original["creation_100ns"],
                "identity_unchanged": True,
                "wait_result": 0,
                "signaled": True,
                "retired": True,
                "status": "retired",
                "errors": [],
            }.items():
                _same(record.get(key), expected)
            code = record.get("exit_code")
            if type(code) is not int or not 0 <= code < 2**32:
                raise ValueError("Missing native exit status")
            if original["process_id"] == self.pinned["roles"]["executor"]:
                _same(code, DEATH_EXIT)
        reference = result.get("reference", {})
        if set(reference) != {
            "root_sha256",
            "panel_sha256",
            "root_bytes",
            "panel_bytes",
            "exact_equal",
        }:
            raise ValueError("Death reference fields differ")
        rect = self.begin["expected"]["rect"]
        _same(reference.get("root_sha256"), self.begin["before_rgb_sha256"])
        _same(reference.get("root_bytes"), self.begin["before_rgb_byte_count"])
        _same(
            reference.get("panel_bytes"), (rect[2] - rect[0]) * (rect[3] - rect[1]) * 3
        )
        _same(reference.get("exact_equal"), expected=True)
        digest = reference.get("panel_sha256")
        if (
            type(digest) is not str
            or len(digest) != DIGEST_CHARS
            or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise ValueError("Invalid post-reference digest")
