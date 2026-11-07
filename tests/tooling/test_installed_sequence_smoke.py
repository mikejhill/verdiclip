"""Sequence outer ownership admission tests, with no native execution."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from scripts import installed_sequence_smoke as smoke

from verdiclip.platform import owned_helper

Json = dict[str, Any]


def ready() -> Json:
    """Return the exact new public identity shape."""
    return {
        "pid": 777,
        "executable": "base",
        "epoch": "b" * 32,
        "protocol_version": 3,
        "peer_schema": 4,
        "credit_schema": 2,
        "sequence_schema": 1,
        "runtime_schema": 5,
        "max_frames": 8,
        "max_requests": 32,
    }


def test_ready_exact_profile_and_old_profile_unchanged() -> None:
    """New identity does not mutate any old profile constant."""
    smoke._ready_profile(ready())
    assert (
        smoke.base.PROTOCOL_VERSION,
        smoke.base.PEER_SCHEMA,
        smoke.base.CREDIT_SCHEMA,
    ) == (2, 3, 1)


@pytest.mark.parametrize(
    "field",
    [
        "protocol_version",
        "peer_schema",
        "credit_schema",
        "sequence_schema",
        "runtime_schema",
        "max_frames",
        "max_requests",
    ],
)
@pytest.mark.parametrize("bad", [True, "1", 0, None])
def test_ready_rejects_versions_and_types(field: str, bad: object) -> None:
    """A different version or a boolean is not a supported wire profile."""
    value = ready()
    value[field] = bad
    with pytest.raises(ValueError, match="Sequence ready"):
        smoke._ready_profile(value)


@pytest.mark.parametrize("extra", [True, False])
def test_ready_rejects_key_changes(*, extra: bool) -> None:
    """Missing and additional identity fields are rejected."""
    value = ready()
    if extra:
        value["unexpected"] = 1
    else:
        del value["runtime_schema"]
    with pytest.raises(ValueError, match="Sequence ready"):
        smoke._ready_profile(value)


@pytest.mark.parametrize(
    "case", ["cancel-first", "parent-death-first", "cancel-interior", "", None]
)
def test_fixed_cases_only(case: str) -> None:
    """No old cancellation or death mode is silently dispatched."""
    with pytest.raises(ValueError, match="Unsupported fixed"):
        smoke.case_index(case)


@pytest.mark.parametrize("case", ["success", "cancel-frame2"])
@pytest.mark.parametrize(
    "fault",
    [
        None,
        "forced",
        "unconfirmed",
        "child",
        "late-log",
        "late-receipt",
        "provenance",
        "source",
    ],
)
def test_actual_outer_admission_cleanup_and_final_log(  # noqa: PLR0915 - complete fixed outer lifecycle
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, case: str, fault: str | None
) -> None:
    """Full outer admission retains cleanup and post-read watchdog gates."""
    fixture_path = tmp_path / "fixture.exe"
    fixture_path.write_bytes(b"never executed")
    args = argparse.Namespace(
        fixture=fixture_path,
        wheel=tmp_path / "app.whl",
        output=tmp_path / "out",
        case=case,
    )
    monkeypatch.setattr(owned_helper, "HelperCommand", Mock(return_value=None))
    fixture, gui, module = Mock(), Mock(), Mock()
    fixture.process.pid, gui.process.pid = 321, 654
    fixture.snapshot.side_effect = [
        {"exit_code": None, "members": [321]},
        {"exit_code": 0, "members": []},
    ]
    gui.snapshot.side_effect = [
        *({"exit_code": None, "members": [654]} for _ in range(3)),
        {"exit_code": 0, "members": []},
    ]
    module.ExternalOwner.side_effect = [fixture, gui]
    monkeypatch.setattr(smoke, "support", lambda: module)
    monkeypatch.setattr(smoke, "provenance", lambda _wheel: {"executable": "base"})
    monkeypatch.setattr(smoke, "source_hashes", lambda: {"executor": "a" * 64})
    now = [1.0]
    monkeypatch.setattr(smoke.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(smoke.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(smoke.uuid, "uuid4", lambda: SimpleNamespace(hex="a" * 32))
    profile = {
        "request": {"pids": [654], "expected_base": "base"},
        "records": {"processes": [{"requested_pid": 654}]},
    }
    temporary = {"roles": {"peer": 777}, "peer": ready()}
    monkeypatch.setattr(smoke, "qualify_ownership", Mock(return_value=profile))
    monkeypatch.setattr(smoke, "serve_temporary", Mock(return_value=temporary))
    monkeypatch.setattr(
        smoke,
        "serve_checkpoint",
        Mock(side_effect=[{"phase": p} for p in smoke.OWNERSHIP_PHASES]),
    )
    monkeypatch.setattr(
        smoke, "preparation_receipt", lambda *_args: {"ready": 1.0, "executor_pid": 654}
    )
    monkeypatch.setattr(smoke, "case_handoff", Mock())
    checks = []

    def validate(_path: Path, _start: float, _clock: object, **kwargs: object) -> None:
        checks.append(kwargs)
        if kwargs["finished"] and len(checks) == 5:
            if fault == "late-log":
                raise RuntimeError("Completed native call exceeded3s")
            if fault == "late-receipt":
                now[0] = 16.0

    monkeypatch.setattr(smoke, "validate_calls", validate)

    def start(_command: object) -> None:
        out = args.output / "executor"
        smoke.write(out / "preparation-ready.json", {})
        smoke.write(
            out / "peer-identity.json",
            {"schema": 1, "session": "a" * 32, "peer": ready()},
        )
        smoke.write(
            out / "result.json",
            {
                "provenance": {
                    "executable": "other" if fault == "provenance" else "base"
                },
                "source_sha256": {"executor": ("b" if fault == "source" else "a") * 64},
                "passed": fault != "child",
                "first_failure": "original child failure" if fault == "child" else None,
                "credit_case": case,
                "cancel_validator_sha256": hashlib.sha256(
                    Path(smoke.__file__)
                    .with_name("sequence_observe_contract.py")
                    .read_bytes()
                ).hexdigest()
                if case == "cancel-frame2"
                else None,
                "case_start": 1.0,
                "case_deadline": 16.0,
                "ownership_profile": profile,
                "temporary_profile": temporary,
                "temporary_pin_cleanup": [],
                "baseline_pin_cleanup": {
                    "processes": profile["records"]["processes"],
                    "cleanup_errors": [],
                },
            },
        )

    gui.start.side_effect = start
    cleanup = Mock(
        return_value={
            "confirmed": fault != "unconfirmed",
            "forced": fault == "forced",
            "errors": [],
        }
    )
    monkeypatch.setattr(smoke, "release_owner", cleanup)
    assert smoke.outer(args) == int(fault is not None)
    receipt = smoke.read(args.output / "receipt.json")
    assert receipt["passed"] is (fault is None)
    assert len(checks) == 5
    assert checks[-1]["finished"] is True
    assert [call.args[0] for call in cleanup.call_args_list] == [gui, fixture]
    assert smoke.qualify_ownership.call_args.args[-1] == "base"
    if fault == "child":
        assert "original child failure" in receipt["first_failure"]
    if fault in {"late-log", "late-receipt", "child", "provenance", "source"}:
        assert not (args.output / "states" / "stop").exists()


@pytest.mark.parametrize("value", [True, 1.0, "1", None, {"extra": 1}])
def test_provenance_wire_type_changes_reject(value: object) -> None:
    """Canonicalizing trusted tuples must not relax scalar type or key identity."""
    assert not smoke.same_wire({"value": value}, {"value": 1})
    assert smoke.same_wire({"paths": ["a", "b"]}, {"paths": ("a", "b")})


@pytest.mark.parametrize("mode", ["old-profile", "late-snapshot", "unknown-member"])
def test_pre_observe_checkpoint_never_acknowledges_invalid_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """Actual checkpoint parser rejects before writing any observe permission."""
    peer = ready()
    if mode == "old-profile":
        peer["protocol_version"] = 2
    session = "a" * 32
    smoke.write_handshake(
        tmp_path / "peer-identity.json", {"schema": 1, "session": session, "peer": peer}
    )
    smoke.write_handshake(
        tmp_path / "ownership-pre-observe-request.json",
        {
            "schema": 1,
            "session": session,
            "request": {
                "phase": "pre-observe",
                "deadline": 15.0,
                "peer": peer,
                "requested": 1.0,
            },
        },
    )
    baseline = {"request": {"pids": [654], "expected_base": "base"}}
    temporary = {"roles": {"peer": 777}}
    monkeypatch.setattr(smoke, "temporary_records", lambda *_args: {"peer": 777})
    now = [2.0]
    monkeypatch.setattr(smoke.time, "monotonic", lambda: now[0])

    def snapshot() -> Json:
        if mode == "late-snapshot":
            now[0] = 15.0
        return {
            "exit_code": None,
            "members": [654, 777, 999] if mode == "unknown-member" else [654, 777],
        }

    gui = Mock(snapshot=Mock(side_effect=snapshot))
    with pytest.raises((ValueError, RuntimeError), match=r"profile|stable ownership"):
        smoke.serve_checkpoint(
            tmp_path, session, "pre-observe", gui, baseline, 15.0, temporary
        )
    assert not (tmp_path / "ownership-pre-observe-ack.json").exists()
    assert gui.snapshot.call_count == (0 if mode == "old-profile" else 1)


def test_contract_isolated_copied_dependency_without_scripts_package(
    tmp_path: Path,
) -> None:
    """Actual isolated interpreter loads the copied fixed validator outside checkout."""
    import shutil  # noqa: PLC0415 - test-only isolated preparation
    import subprocess  # noqa: PLC0415 - test-only isolated interpreter
    import sys  # noqa: PLC0415 - test-only interpreter identity

    folder = Path(smoke.__file__).parent
    for name in ("sequence_observe_contract.py", "observe_credit_cancel.py"):
        shutil.copyfile(folder / name, tmp_path / name)
    source = str(tmp_path / "sequence_observe_contract.py")
    code = (
        "import importlib.util; "
        f"s=importlib.util.spec_from_file_location('proof',{source!r}); "
        "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
        "assert m.parse_json(b'{\"value\":1}') == {'value':1}"
    )
    outcome = subprocess.run(  # noqa: S603 - fixed stdlib-only test interpreter
        [sys.executable, "-I", "-S", "-c", code],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert outcome.returncode == 0, outcome.stderr
