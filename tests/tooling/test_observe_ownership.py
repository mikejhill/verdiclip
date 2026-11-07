"""Portable ownership profile transitions, not native capture qualification."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from scripts import installed_observe_smoke as smoke
from scripts import observe_process_diagnostics as native

Json = dict[str, Any]
SESSION = "a" * 32
LAUNCH = r"C:\proof\venv\Scripts\python.exe"
BASE = r"C:\runtime\python.exe"
CONSOLE = r"C:\Windows\System32\conhost.exe"


@pytest.fixture
def records() -> Json:
    """Three exact independent image roles and pinned process lifetime facts."""
    return {
        "processes": [
            {
                "requested_pid": pid,
                "process_id": pid,
                "creation_100ns": 1000 + pid,
                "image_path": image,
                "status": "available",
                "errors": [],
                "parent": {"status": "available", "pid": 99 if pid == 11 else 11},
            }
            for pid, image in ((11, LAUNCH), (12, BASE), (13, CONSOLE))
        ],
        "cleanup_errors": [],
    }


def request() -> Json:
    """Fixed outer expectations do not come from observed process image names."""
    return {
        "pids": [11, 12, 13],
        "launch_pid": 11,
        "executor_pid": 12,
        "expected_launch": LAUNCH,
        "expected_base": BASE,
        "snapshot_time": 1.0,
        "deadline": 10.0,
    }


def profile(records: Json) -> Json:
    """Make an independently validated exact-role profile."""
    return {
        "request": request(),
        "console": CONSOLE,
        "records": records,
        "roles": dict(
            native.validate_profile(records["processes"], 11, 12, LAUNCH, BASE, CONSOLE)
        ),
    }


def packet(field: str, value: Json) -> Json:
    """Encode one fixed session-scoped exchange."""
    return {"schema": 1, "session": SESSION, field: value}


def log(path: Path) -> None:
    """One early process event, without inventing native call completion."""
    path.write_text(
        json.dumps({"event": "process", "name": "executor", "monotonic": 0.0}) + "\n"
    )


@pytest.mark.parametrize(
    "failure",
    [
        None,
        "unknown-image",
        "current-extra",
        "current-missing",
        "changed-pin",
        "late-ack",
    ],
)
def test_acquire_holds_exact_baseline_pins_across_membership(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, records: Json, failure: str | None
) -> None:
    """Actual child acquisition keeps handles only after current containment proof."""
    clock = [2.0]
    monkeypatch.setattr(smoke.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(smoke.os, "getpid", lambda: 12)
    smoke.write(tmp_path / "ownership-request.json", packet("request", request()))
    pins = Mock()
    pins.collect.return_value = deepcopy(records)
    pins.observe.return_value = deepcopy(records)
    pins.close.return_value = []
    if failure == "unknown-image":
        pins.collect.return_value["processes"][2]["image_path"] = (
            r"C:\other\conhost.exe"
        )
    if failure == "changed-pin":
        pins.observe.return_value["processes"][2]["creation_100ns"] += 1
    module = SimpleNamespace(
        PinnedProcesses=Mock(return_value=pins),
        expected_console_image=Mock(return_value=CONSOLE),
        validate_profile=native.validate_profile,
    )
    monkeypatch.setattr(smoke, "topology_module", lambda: module)
    original = smoke.write_handshake

    def publish(path: Path, value: Json) -> None:
        original(path, value)
        if path.name == "ownership-pinned.json":
            pins.close.assert_not_called()
            members = [11, 12, 13]
            if failure == "current-extra":
                members.append(14)
            if failure == "current-missing":
                members.pop()
            original(
                tmp_path / "ownership-membership.json",
                packet(
                    "membership",
                    {
                        "started": 2.0,
                        "finished": 2.0,
                        "snapshot": {"exit_code": None, "members": members},
                    },
                ),
            )
            if failure == "late-ack":
                clock[0] = 10.0

    monkeypatch.setattr(smoke, "write_handshake", publish)
    journal = smoke.Journal(tmp_path / "calls.jsonl", 10.0)
    try:
        if failure is None:
            held, qualified = smoke.acquire_ownership(tmp_path, SESSION, 0.0, journal)
            assert held is pins
            assert qualified["roles"] == {"launcher": 11, "executor": 12, "console": 13}
            pins.close.assert_not_called()
            assert (tmp_path / "ownership-ready.json").exists()
        else:
            with pytest.raises((ValueError, RuntimeError), match=r".+"):
                smoke.acquire_ownership(tmp_path, SESSION, 0.0, journal)
            pins.close.assert_called_once_with()
            assert not (tmp_path / "ownership-ready.json").exists()
        assert not (tmp_path / "case-start.json").exists()
    finally:
        journal.stream.close()


@pytest.mark.parametrize(
    "failure",
    [None, "substitute-launch", "extra-member", "late-snapshot", "changed-ready"],
)
def test_outer_qualifies_only_fixed_images_and_current_exact_job(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, records: Json, failure: str | None
) -> None:
    """Outer admission compares independent expectations and retained-pin records."""
    clock = [2.0]
    monkeypatch.setattr(smoke.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(smoke, "topology_module", lambda: native)
    log(tmp_path / "calls.jsonl")
    gui = Mock()

    def snapshot() -> Json:
        if failure == "late-snapshot":
            clock[0] = 10.0
        return {
            "exit_code": None,
            "members": [11, 12, 13, 14] if failure == "extra-member" else [11, 12, 13],
        }

    gui.snapshot.side_effect = snapshot
    original = smoke.write_handshake
    candidate = profile(records)

    def publish(path: Path, value: Json) -> None:
        original(path, value)
        if path.name == "ownership-request.json":
            assert value["request"] == request()
            if failure == "substitute-launch":
                candidate["request"]["expected_launch"] = r"C:\other\python.exe"
            original(tmp_path / "ownership-pinned.json", packet("pinned", candidate))
        elif path.name == "ownership-membership.json":
            ready = deepcopy(candidate)
            if failure == "changed-ready":
                ready["roles"]["console"] = 14
            original(tmp_path / "ownership-ready.json", packet("ready", ready))

    monkeypatch.setattr(smoke, "write_handshake", publish)
    args = (
        tmp_path,
        SESSION,
        {"exit_code": None, "members": [11, 12, 13]},
        1.0,
        gui,
        0.0,
        10.0,
        11,
        12,
        LAUNCH,
        BASE,
    )
    if failure is None:
        assert smoke.qualify_ownership(*args) == candidate
        gui.snapshot.assert_called_once_with()
    else:
        with pytest.raises((ValueError, RuntimeError), match=r".+"):
            smoke.qualify_ownership(*args)
    assert not (tmp_path / "case-start.json").exists()


@pytest.mark.parametrize("phase", smoke.OWNERSHIP_PHASES)
@pytest.mark.parametrize("failure", [None, "extra", "missing", "wrong-peer", "late"])
def test_stable_checkpoint_requires_exact_phase_specific_members(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    records: Json,
    phase: str,
    failure: str | None,
) -> None:
    """Only the channel-attested direct helper can be a temporary fourth member."""
    clock = [2.0]
    monkeypatch.setattr(smoke.time, "monotonic", lambda: clock[0])
    peer = {
        "pid": 44,
        "epoch": "b" * 32,
        "executable": BASE,
        "peer_schema": 2,
        "protocol_version": 1,
    }
    smoke.write(tmp_path / "peer-identity.json", packet("peer", peer))
    sent_peer = deepcopy(peer) if phase == "pre-observe" else None
    if failure == "wrong-peer":
        sent_peer = {**peer, "pid": 45}
    smoke.write(
        tmp_path / f"ownership-{phase}-request.json",
        packet(
            "request",
            {"phase": phase, "deadline": 15.0, "peer": sent_peer, "requested": 2.0},
        ),
    )
    members = [11, 12, 13, 44] if phase == "pre-observe" else [11, 12, 13]
    if failure == "extra":
        members.append(55)
    if failure == "missing":
        members.remove(13)
    gui = Mock()

    def snapshot() -> Json:
        if failure == "late":
            clock[0] = 15.0
        return {"exit_code": None, "members": members}

    gui.snapshot.side_effect = snapshot
    if failure is None:
        ack = smoke.serve_checkpoint(
            tmp_path, SESSION, phase, gui, profile(records), 15.0
        )
        assert ack is not None
        assert ack["snapshot"]["members"] == members
    else:
        with pytest.raises((ValueError, RuntimeError), match=r".+"):
            smoke.serve_checkpoint(
                tmp_path, SESSION, phase, gui, profile(records), 15.0
            )
        assert not (tmp_path / f"ownership-{phase}-ack.json").exists()


@pytest.mark.parametrize("failure", [None, "pin-changed", "extra-member", "late"])
def test_child_stable_checkpoint_rechecks_held_pins_before_pixels(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, records: Json, failure: str | None
) -> None:
    """A correct snapshot does not excuse a later changed pinned identity."""
    clock = [2.0]
    monkeypatch.setattr(smoke.time, "monotonic", lambda: clock[0])
    pins = Mock()
    pins.observe.return_value = deepcopy(records)
    if failure == "pin-changed":
        pins.observe.return_value["processes"][0]["status"] = "stale"
    original = smoke.write_handshake

    def publish(path: Path, value: Json) -> None:
        original(path, value)
        ack = {
            "phase": "pre-reference",
            "deadline": 15.0,
            "started": 2.0,
            "finished": 2.0,
            "snapshot": {
                "exit_code": None,
                "members": [11, 12, 13, 55]
                if failure == "extra-member"
                else [11, 12, 13],
            },
        }
        original(tmp_path / "ownership-pre-reference-ack.json", packet("ack", ack))
        if failure == "late":
            clock[0] = 15.0

    monkeypatch.setattr(smoke, "write_handshake", publish)
    journal = smoke.Journal(tmp_path / "calls.jsonl", 15.0)
    try:
        if failure is None:
            smoke.ownership_checkpoint(
                tmp_path,
                SESSION,
                "pre-reference",
                None,
                pins,
                profile(records),
                journal,
            )
            pins.observe.assert_called_once_with()
        else:
            with pytest.raises(RuntimeError, match=r".+"):
                smoke.ownership_checkpoint(
                    tmp_path,
                    SESSION,
                    "pre-reference",
                    None,
                    pins,
                    profile(records),
                    journal,
                )
        pins.close.assert_not_called()
    finally:
        journal.stream.close()


@pytest.mark.parametrize(
    "failure", [None, "late-handoff", "missing-handoff", "target-changed"]
)
def test_preparation_to_case_transfers_or_closes_baseline_pins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, records: Json, failure: str | None
) -> None:
    """New retained-pin preparation never loses handles on failed case admission."""
    states = tmp_path / "states"
    states.mkdir()
    fixture = {
        "mode": "observe-only",
        "alive": True,
        "pid": 321,
        "process_created_100ns": 1234,
        "root_hwnd": 456,
        "rect": [24, 24, 534, 454],
        "content_rect": [24, 24, 517, 454],
        "authored_layout_rect": [0, 0, 900, 640],
        "dpi_x": 96,
        "dpi_y": 96,
        "state": {"target": {"horizontal": 0, "vertical": 119}},
        "markers": [],
        "sequence": 0,
    }
    smoke.write(states / "0000.json", fixture)
    clock = [2.0]
    monkeypatch.setattr(smoke.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(smoke.time, "sleep", lambda _: clock.__setitem__(0, 10.0))
    guard, pins = Mock(), Mock()
    pins.close.return_value = []
    guard.check.side_effect = [
        {
            "pid": 321,
            "root_hwnd": 456,
            "creation_100ns": 1234,
            "input_tick": 123,
            "rect": [0, 0, 900, 640],
        },
        RuntimeError("target changed") if failure == "target-changed" else {},
    ]

    def acquire(*_args: object) -> tuple[Mock, Json]:
        if failure != "missing-handoff":
            smoke.write(
                tmp_path / "case-start.json",
                {"schema": 1, "session": SESSION, "case_start": 2.0, "deadline": 17.0},
            )
        if failure == "late-handoff":
            clock[0] = 10.0
        return pins, profile(records)

    monkeypatch.setattr(smoke, "acquire_ownership", acquire)
    journal = smoke.Journal(tmp_path / "calls.jsonl", 10.0)
    args = (
        tmp_path,
        states,
        SESSION,
        0.0,
        321,
        fixture,
        smoke.fixture_key(fixture),
        guard,
        journal,
    )
    try:
        if failure is None:
            handoff, held, _ = smoke.prepare_owned_case(*args)
            assert handoff["case_start"] == 2.0
            assert held is pins
            assert pins.deadline == 17.0
            assert journal.deadline == 17.0
            pins.close.assert_not_called()
        else:
            with pytest.raises(RuntimeError, match=r".+"):
                smoke.prepare_owned_case(*args)
            pins.close.assert_called_once_with()
    finally:
        journal.stream.close()


@pytest.mark.parametrize(
    ("begin", "end"),
    [
        (True, 2.0),
        (1.0, False),
        (float("nan"), 2.0),
        (1.0, float("inf")),
        (0.5, 2.0),
        (2.5, 2.0),
        (1.0, 3.0),
    ],
)
def test_ownership_snapshot_times_are_finite_fresh_and_ordered(
    monkeypatch: pytest.MonkeyPatch, begin: object, end: object
) -> None:
    """A claimed membership interval cannot predate its request or the live clock."""
    monkeypatch.setattr(smoke.time, "monotonic", lambda: 2.0)
    with pytest.raises(ValueError, match="interval"):
        smoke.ownership_interval({"started": begin, "finished": end}, 1.0, 15.0)


def test_transient_unknown_startup_member_cannot_disappear_into_admission() -> None:
    """An extra observed before attestation stays accounted for even if it exits."""
    seen: set[int] = set()
    smoke.track_temporary_member(
        {11, 12, 13, 55}, {11, 12, 13}, seen, None, retiring=False
    )
    smoke.track_temporary_member({11, 12, 13}, {11, 12, 13}, seen, None, retiring=False)
    with pytest.raises(RuntimeError, match="Unexpected"):
        smoke.track_temporary_member(
            {11, 12, 13, 44}, {11, 12, 13}, seen, 44, retiring=False
        )


def test_only_attested_temporary_helper_can_arrive_and_retire() -> None:
    """The expected fourth process is provisional until its identity is attested."""
    seen: set[int] = set()
    smoke.track_temporary_member(
        {11, 12, 13, 44}, {11, 12, 13}, seen, None, retiring=False
    )
    smoke.track_temporary_member(
        {11, 12, 13, 44}, {11, 12, 13}, seen, 44, retiring=False
    )
    smoke.track_temporary_member({11, 12, 13}, {11, 12, 13}, seen, 44, retiring=False)
    smoke.track_temporary_member({11}, {11, 12, 13}, seen, 44, retiring=True)
    assert seen == {44}
    with pytest.raises(RuntimeError, match="disappeared"):
        smoke.track_temporary_member({11}, {11, 12, 13}, seen, 44, retiring=False)


@pytest.mark.parametrize("extra", [44, 55])
def test_retired_peer_or_unknown_member_cannot_reappear(extra: int) -> None:
    """A retired peer PID cannot authorize a new member after terminal cleanup."""
    seen = {44}
    with pytest.raises(RuntimeError, match="after helper retirement"):
        smoke.track_temporary_member(
            {11, 12, 13, extra}, {11, 12, 13}, seen, 44, retiring=True
        )
    assert seen == {44}


@pytest.mark.parametrize("members", [{11, 12, 13}, {11}, set()])
def test_retirement_allows_only_natural_baseline_shrink(members: set[int]) -> None:
    """Retirement permits baseline exits without forgetting prior peer history."""
    seen = {44}
    smoke.track_temporary_member(members, {11, 12, 13}, seen, 44, retiring=True)
    assert seen == {44}
