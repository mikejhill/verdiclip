"""Portable admission and watchdog tests, never native capture qualification."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from scripts import installed_observe_smoke as smoke

from verdiclip.capture.observe_protocol import Observation
from verdiclip.platform import owned_helper

ROOT = (0, 0, 900, 640)
SELECTED = (24, 24, 534, 454)
DIGEST = "a" * 64
Json = dict[str, Any]


@pytest.fixture
def authored() -> Json:
    """Independent fixture facts include borders, sibling state, and off-R marks."""
    return {
        "mode": "observe-only",
        "alive": True,
        "pid": 321,
        "process_created_100ns": 1234,
        "root_hwnd": 456,
        "rect": list(SELECTED),
        "content_rect": [26, 26, 515, 452],
        "authored_layout_rect": list(ROOT),
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


def marked_pixels(authored: Json, *, dx: int = 0) -> bytes:
    """Paint only authored positions; dx creates a wrong-origin reference."""
    width, height = ROOT[2], ROOT[3]
    rgb = bytearray(width * height * 3)
    for marker in authored["markers"]:
        left, top, right, bottom = marker["rect"]
        for y in range(top, bottom):
            begin = (y * width + left + dx) * 3
            end = (y * width + right + dx) * 3
            rgb[begin:end] = bytes(marker["rgb"]) * (right - left)
    return bytes(rgb)


@pytest.mark.parametrize("value", [[-1, 2, 509, 432], [-1.0, 2.0, 509.0, 432.0]])
def test_rect_preserves_exact_physical_coordinates(value: list[int | float]) -> None:
    """Integral floats are accepted without translating a negative screen origin."""
    assert smoke.rect(value) == (-1, 2, 509, 432)


@pytest.mark.parametrize(
    "bad",
    [
        None,
        (0, 0, 1, 1),
        [0, 0, 1],
        [0, 0, 1, 1, 2],
        [False, 0, 1, 1],
        ["0", 0, 1, 1],
        [0.5, 0, 1, 1],
        [float("nan"), 0, 1, 1],
        [0, float("inf"), 1, 1],
        [0, -float("inf"), 1, 1],
        [-(2**31) - 1, 0, 1, 1],
        [0, 0, 2**31, 1],
        [0, 0, 0, 1],
        [0, 1, 1, 1],
        [2, 0, 1, 1],
    ],
)
def test_rect_rejects_fitted_nonfinite_or_empty_geometry(bad: object) -> None:
    """Rounding, bool coordinates, empty bounds, and overflow cannot enter a crop."""
    with pytest.raises(ValueError, match="rectangle"):
        smoke.rect(bad)


def test_fixture_key_ignores_freshness_but_retains_independent_state(
    authored: Json,
) -> None:
    """Fresh sample sequence may advance while every fixed reference fact agrees."""
    expected = smoke.fixture_key(authored)
    authored.update(sequence=12, milliseconds=1300)
    assert smoke.fixture_key(authored) == expected
    assert set(expected) == {
        "pid",
        "process_created_100ns",
        "root_hwnd",
        "rect",
        "content_rect",
        "state",
        "dpi_x",
        "dpi_y",
        "markers",
        "authored_layout_rect",
    }
    assert expected["rect"] == list(SELECTED)
    assert expected["content_rect"] != expected["rect"]
    assert expected["state"]["sibling"] == {"horizontal": 27, "vertical": 93}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mode", "ordinary"),
        ("mode", "borderless"),
        ("alive", False),
        ("alive", 1),
        ("dpi_x", 120),
        ("dpi_y", 144),
        ("dpi_x", float("nan")),
        ("rect", [24, 24, 533, 454]),
        ("rect", [24, 24, 534, 453]),
        ("rect", [24.5, 24, 534.5, 454]),
    ],
)
def test_fixture_key_rejects_wrong_mode_dpi_or_full_dimensions(
    authored: Json, field: str, value: object
) -> None:
    """The owned proof cannot silently become a smaller interior or scaled panel."""
    authored[field] = value
    with pytest.raises(ValueError, match=r".+"):
        smoke.fixture_key(authored)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("horizontal", 47),
        ("horizontal", False),
        ("horizontal", float("nan")),
        ("horizontal", float("inf")),
        ("vertical", 118),
        ("vertical", "119"),
        ("vertical", float("nan")),
        ("vertical", float("inf")),
    ],
)
def test_fixture_key_rejects_changed_or_ambiguous_offsets(
    authored: Json, field: str, value: object
) -> None:
    """Only the authored finite numeric pose is eligible for this success case."""
    authored["state"]["target"][field] = value
    with pytest.raises(ValueError, match="pose"):
        smoke.fixture_key(authored)


def test_crop_preserves_every_full_r_edge_and_native_origin() -> None:
    """Known root rows retain borders rather than a fitted interior-only match."""
    rgb = bytes(range(90))
    root = (-9, 10, -3, 15)
    selected = (-8, 11, -4, 14)
    expected = b"".join(rgb[(row * 6 + 1) * 3 : (row * 6 + 5) * 3] for row in (1, 2, 3))
    assert smoke.crop(rgb, root, selected) == expected
    assert len(expected) == 4 * 3 * 3
    assert smoke.crop(rgb, root, root) == rgb


@pytest.mark.parametrize(
    "selected",
    [(-1, 0, 2, 2), (0, -1, 2, 2), (0, 0, 3, 2), (0, 0, 2, 3), (0, 0, 0, 2)],
)
def test_crop_never_clips_or_translates_outside_selection(
    selected: tuple[int, int, int, int],
) -> None:
    """Any edge outside the native root fails instead of shrinking the oracle."""
    with pytest.raises(ValueError, match="outside"):
        smoke.crop(bytes(12), (0, 0, 2, 2), selected)


@pytest.mark.parametrize("count", [0, 11, 13])
def test_crop_rejects_reference_byte_shape(count: int) -> None:
    """Short or padded root buffers do not yield apparently correct small crops."""
    with pytest.raises(ValueError, match="shape"):
        smoke.crop(bytes(count), (0, 0, 2, 2), (0, 0, 1, 1))


def test_crop_rejects_reference_pixel_budget() -> None:
    """Oversize references fail before any root-sized buffer is needed."""
    with pytest.raises(ValueError, match="bound"):
        smoke.crop(b"", (0, 0, smoke.MAX_PIXELS + 1, 1), (0, 0, 1, 1))


def test_markers_require_exact_authored_coordinates(authored: Json) -> None:
    """Finding the same colors one pixel away must never fit the oracle origin."""
    smoke.markers(marked_pixels(authored), ROOT, authored)
    for dx in (-1, 1):
        with pytest.raises(ValueError, match="pixels differ"):
            smoke.markers(marked_pixels(authored, dx=dx), ROOT, authored)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "tr"),
        ("rect", [8, 8, 11, 12]),
        ("rect", [8, 8, 12, 13]),
        ("rect", [24, 24, 28, 28]),
        ("rect", [-1, 8, 3, 12]),
        ("rgb", [1, 2]),
        ("rgb", [True, 2, 3]),
        ("rgb", [1.0, 2, 3]),
        ("rgb", [-1, 2, 3]),
        ("rgb", [256, 2, 3]),
    ],
)
def test_markers_reject_wrong_identity_size_overlap_or_color(
    authored: Json, field: str, value: object
) -> None:
    """Only bounded off-panel four-pixel squares qualify at predicted positions."""
    rgb = marked_pixels(authored)
    authored["markers"][0][field] = value
    with pytest.raises(ValueError, match=r".+"):
        smoke.markers(rgb, ROOT, authored)


@pytest.mark.parametrize("value", [None, [], [{"id": "one"}]])
def test_markers_require_four_authored_witnesses(authored: Json, value: object) -> None:
    """Missing or partial marker sets cannot stand in for a full-origin check."""
    authored["markers"] = value
    with pytest.raises(ValueError, match="Four"):
        smoke.markers(b"", ROOT, authored)


def event(kind: str, timestamp: float, name: str = "read") -> Json:
    """Build one externally flushed call event without any native API execution."""
    return {"event": kind, "name": name, "monotonic": timestamp}


def log(path: Path, records: list[Json], suffix: bytes = b"") -> None:
    """Publish test records, optionally with an incomplete trailing write."""
    path.write_bytes(
        b"".join(json.dumps(value).encode() + b"\n" for value in records) + suffix
    )


@pytest.mark.parametrize("now", [2.999, 3.0, 3.001])
def test_missing_call_log_has_exact_startup_budget(tmp_path: Path, now: float) -> None:
    """A missing log is tolerated strictly before, never at, the 3-second edge."""
    path = tmp_path / "calls.jsonl"
    if now < smoke.CALL_SECONDS:
        smoke.validate_calls(path, 0.0, lambda: now, finished=False)
    else:
        with pytest.raises(RuntimeError, match="startup missing"):
            smoke.validate_calls(path, 0.0, lambda: now, finished=False)
    with pytest.raises(RuntimeError, match="startup missing"):
        smoke.validate_calls(path, 0.0, lambda: 0.1, finished=True)


@pytest.mark.parametrize("duration", [2.999, 3.0, 3.001])
@pytest.mark.parametrize("completed", [True, False])
def test_native_calls_have_strict_external_budget(
    tmp_path: Path, duration: float, *, completed: bool
) -> None:
    """Late completion and still-running calls fail at the same original limit."""
    path = tmp_path / "calls.jsonl"
    records = [event("process", 0.0), event("call_start", 1.0)]
    if completed:
        records.append(event("call_end", 1.0 + duration))
    log(path, records)
    if duration < smoke.CALL_SECONDS:
        smoke.validate_calls(path, 0.0, lambda: 1.0 + duration, finished=completed)
    else:
        with pytest.raises(RuntimeError, match=r"late|blocked"):
            smoke.validate_calls(path, 0.0, lambda: 1.0 + duration, finished=completed)


@pytest.mark.parametrize("now", [14.999, 15.0, 15.001])
def test_external_case_deadline_never_restarts(tmp_path: Path, now: float) -> None:
    """Even a complete early call history cannot qualify at the case deadline."""
    path = tmp_path / "calls.jsonl"
    log(path, [event("process", 0.0), event("call_start", 1.0), event("call_end", 1.1)])
    if now < smoke.CASE_SECONDS:
        smoke.validate_calls(path, 0.0, lambda: now, finished=True)
    else:
        with pytest.raises(RuntimeError, match="case deadline"):
            smoke.validate_calls(path, 0.0, lambda: now, finished=True)


@pytest.mark.parametrize(
    "records",
    [
        [],
        [event("process", 3.0)],
        [event("call_start", 0.0)],
        [event("process", 0.0), event("call_end", 0.1)],
        [event("process", 0.0), event("call_start", 0.1), event("call_start", 0.2)],
        [event("process", 0.0), event("call_start", 0.1), event("call_error", 0.2)],
        [
            event("process", 0.0),
            event("call_start", 0.1),
            event("call_end", 0.2, "other"),
        ],
        [event("process", 0.0), event("process", 0.1)],
        [event("process", 0.0), event("call_start", -0.1)],
        [event("process", float("nan"))],
        [event("process", float("inf"))],
        [event("process", -1.0)],
        [event("process", 4.1)],
    ],
)
def test_invalid_external_call_logs_fail_closed(
    tmp_path: Path, records: list[Json]
) -> None:
    """Errors, overlaps, wrong pairs, time reversal, and nonfinite logs all fail."""
    path = tmp_path / "calls.jsonl"
    log(path, records)
    with pytest.raises(RuntimeError, match=r".+"):
        smoke.validate_calls(path, 0.0, lambda: 4.0, finished=True)


def test_pending_and_partial_final_events_cannot_finish(tmp_path: Path) -> None:
    """A live partial write is tolerated but incomplete terminal evidence is not."""
    path = tmp_path / "calls.jsonl"
    log(path, [event("process", 0.0), event("call_start", 0.1)])
    with pytest.raises(RuntimeError, match="incomplete"):
        smoke.validate_calls(path, 0.0, lambda: 0.2, finished=True)
    log(path, [event("process", 0.0)], b'{"event":')
    smoke.validate_calls(path, 0.0, lambda: 0.2, finished=False)
    with pytest.raises(RuntimeError, match="Partial final"):
        smoke.validate_calls(path, 0.0, lambda: 0.2, finished=True)


def test_call_log_byte_and_record_bounds(tmp_path: Path) -> None:
    """External watchdog readers bound both bytes and event allocation."""
    path = tmp_path / "calls.jsonl"
    path.write_bytes(b"x" * (smoke.MAX_JSON + 1))
    with pytest.raises(RuntimeError, match="oversized"):
        smoke.validate_calls(path, 0.0, lambda: 1.0, finished=True)
    log(path, [event("process", 0.0)] * (smoke.MAX_CALLS + 1))
    with pytest.raises(RuntimeError, match="oversized"):
        smoke.validate_calls(path, 0.0, lambda: 1.0, finished=True)


@pytest.mark.parametrize("duration", [2.999, 3.0, 3.001])
def test_journal_rejects_late_completed_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, duration: float
) -> None:
    """The inner logger also rejects exact-bound completions without resetting time."""
    clock = [0.0]
    monkeypatch.setattr(smoke.time, "monotonic", lambda: clock[0])
    journal = smoke.Journal(tmp_path / "calls.jsonl", 15.0)

    def operation() -> str:
        clock[0] = duration
        return "pixels"

    try:
        if duration < smoke.CALL_SECONDS:
            assert journal.call("read", operation) == "pixels"
        else:
            with pytest.raises(RuntimeError, match="exceeded"):
                journal.call("read", operation)
    finally:
        journal.stream.close()
    records = [json.loads(line) for line in journal.path.read_text().splitlines()]
    assert [record["event"] for record in records] == [
        "process",
        "call_start",
        "call_end",
    ]


def test_journal_logs_operation_error_and_keeps_original_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A read failure is externally visible and cannot become a successful call."""
    monkeypatch.setattr(smoke.time, "monotonic", lambda: 0.0)
    journal = smoke.Journal(tmp_path / "calls.jsonl", 15.0)
    error = OSError("read failed")
    try:
        with pytest.raises(OSError, match="read failed") as caught:
            journal.call("read", Mock(side_effect=error))
        assert caught.value is error
    finally:
        journal.stream.close()
    with pytest.raises(RuntimeError, match="failure"):
        smoke.validate_calls(journal.path, 0.0, lambda: 0.1, finished=True)


@pytest.mark.parametrize("forced", [True, False])
@pytest.mark.parametrize("exit_confirmed", [True, False])
def test_cleanup_distinguishes_natural_and_forced_retirement(
    monkeypatch: pytest.MonkeyPatch, *, forced: bool, exit_confirmed: bool
) -> None:
    """A forced exit may be confirmed, but can never satisfy natural success."""
    clock = [0.0]
    owner = Mock()
    owner.job = 77
    initial = (
        {"exit_code": None, "members": [22]}
        if forced
        else {"exit_code": 0, "members": []}
    )
    final = (
        {"exit_code": 0, "members": []}
        if exit_confirmed
        else {"exit_code": None, "members": [22]}
    )
    owner.snapshot.side_effect = [initial, *([final] * 1000)]
    owner.close.return_value = []
    monkeypatch.setattr(smoke.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        smoke.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay)
    )
    result = smoke.release_owner(owner, 15.0)
    assert result["confirmed"] is exit_confirmed
    assert result["forced"] is forced
    assert result["errors"] == []
    assert (result["confirmed"] and not result["forced"]) is (
        exit_confirmed and not forced
    )
    assert owner.api.terminate.call_count == int(forced)
    if forced:
        owner.api.terminate.assert_called_once_with(77)
    owner.close.assert_called_once_with()
    assert clock[0] < 5.02


@pytest.mark.parametrize("failure", ["snapshot", "terminate", "close", "deadline"])
def test_cleanup_failures_cannot_be_confirmed(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Failed inspection, termination, handle closure, or late cleanup rejects."""
    owner = Mock()
    owner.snapshot.return_value = {"exit_code": None, "members": [22]}
    owner.close.return_value = []
    if failure == "snapshot":
        owner.snapshot.side_effect = OSError("snapshot failed")
    elif failure == "terminate":
        owner.api.terminate.side_effect = OSError("terminate failed")
    elif failure == "close":
        owner.snapshot.return_value = {"exit_code": 0, "members": []}
        owner.close.return_value = ["close failed"]
    monkeypatch.setattr(
        smoke.time, "monotonic", lambda: 15.0 if failure == "deadline" else 0.0
    )
    result = smoke.release_owner(owner, 15.0)
    assert result["confirmed"] is False
    assert bool(result["errors"]) is (failure != "deadline")
    owner.close.assert_called_once_with()


@pytest.fixture
def terminal_case(authored: Json) -> tuple[Json, Json, Json, Json]:
    """Decode a real full-panel Observation, as in the protocol schema tests."""
    target = {
        "pid": 321,
        "process_created_100ns": 1234,
        "hwnd": 456,
        "point": [279, 239],
    }
    value = {
        "observation_schema": 1,
        "epoch": "a" * 32,
        "request_id": 1,
        "frame_id": 1,
        "target": {**target, "runtime_id": [42, -1], "framework": "WPF"},
        "guard": {
            "foreground_hwnd": 456,
            "foreground_pid": 321,
            "input_tick": 123,
            "root_client_rect": list(ROOT),
            "target_rect": list(SELECTED),
            "capture_rect": list(SELECTED),
            "dpi": [96, 96],
            "monitors": [[1, -100, -100, 1000, 800, True]],
            "visibility_evidence": {
                "scope": "owned-wpf-fixture-external-check-v1",
                "root_visible": True,
                "root_minimized": False,
                "root_cloaked": False,
                "external_intersection_count": 0,
                "point_root_checks": 6,
                "internal_overlay_assessment": "fixture-assumption-only",
            },
        },
        "scroll": {
            "horizontal": -1,
            "vertical": 12,
            "horizontal_view": 100,
            "vertical_view": 25,
            "horizontal_scrollable": False,
            "vertical_scrollable": True,
        },
        "settled": {"samples": 3, "quiet_ns": 200_000_000},
    }
    actual = Observation.parse(json.dumps(value).encode())
    assert actual.frame_identity().width == 510
    assert actual.frame_identity().height == 430
    receipt = {
        "passed": True,
        "failure": None,
        "cleanup_failure": None,
        "not_launched": False,
        "helper": {
            "pid": 99,
            "producer_verified": True,
            "termination_requested": False,
            "closed": True,
            "remaining_pids": [],
            "cleanup_confirmed": True,
            "cleanup_errors": [],
            "failure": None,
            "exit_code": 0,
        },
        "observe": {
            "image_sha256": DIGEST,
            "bytes": 510 * 430 * 3,
            "observation": json.loads(actual.canonical),
            "first_body_ns": 100,
            "validated_ns": 200,
            "finished_ns": 300,
            "deadline_ns": 400,
        },
    }
    expected = smoke.fixture_key(authored)
    expected["reference_input_tick"] = 123
    return receipt, expected, target, {"pid": 99}


def test_terminal_requires_real_metadata_full_r_hash_and_natural_cleanup(
    terminal_case: tuple[Json, Json, Json, Json],
) -> None:
    """Success consumes the public terminal without inventing EOF receipt fields."""
    receipt, expected, target, identity = terminal_case
    assert smoke.terminal_matches(receipt, expected, DIGEST, target, identity)
    assert not smoke.terminal_matches(receipt, expected, "b" * 64, target, identity)
    assert not smoke.terminal_matches(receipt, expected, DIGEST, target, {})
    assert not smoke.terminal_matches(receipt, expected, DIGEST, target, {"pid": 100})
    expected["rect"][0] += 1
    assert not smoke.terminal_matches(receipt, expected, DIGEST, target, identity)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("passed",), False),
        (("passed",), 1),
        (("failure",), "timeout"),
        (("cleanup_failure",), "close"),
        (("not_launched",), True),
        (("helper", "cleanup_confirmed"), False),
        (("helper", "producer_verified"), False),
        (("helper", "closed"), False),
        (("helper", "termination_requested"), True),
        (("helper", "termination_requested"), 0),
        (("helper", "termination_requested"), None),
        (("helper", "remaining_pids"), [99]),
        (("helper", "cleanup_errors"), ["failed"]),
        (("helper", "failure"), "cleanup"),
        (("helper", "exit_code"), 1),
        (("helper", "pid"), 100),
        (("observe", "bytes"), 510 * 430 * 3 - 1),
        (("observe", "observation"), None),
        (("observe", "first_body_ns"), 201),
        (("observe", "validated_ns"), 301),
        (("observe", "finished_ns"), 400),
        (("observe", "finished_ns"), 401),
        (("observe", "finished_ns"), 300.0),
        (("observe", "first_body_ns"), True),
        (("observe", "first_body_ns"), 0),
        (("observe", "first_body_ns"), -1),
    ],
)
def test_terminal_mismatch_or_exact_deadline_cannot_pass(
    terminal_case: tuple[Json, Json, Json, Json], path: tuple[str, ...], value: object
) -> None:
    """Any wrong guarantee, byte count, identity, or strict timing rejects success."""
    receipt, expected, target, identity = terminal_case
    current = receipt
    for key in path[:-1]:
        current = current[key]
    current[path[-1]] = value
    assert not smoke.terminal_matches(receipt, expected, DIGEST, target, identity)


def test_terminal_cannot_substitute_a_different_pinned_target(
    terminal_case: tuple[Json, Json, Json, Json],
) -> None:
    """A valid observation from another process lifetime still fails correlation."""
    receipt, expected, target, identity = terminal_case
    target["process_created_100ns"] += 1
    assert not smoke.terminal_matches(receipt, expected, DIGEST, target, identity)


@pytest.mark.parametrize(
    "field", ["producer_verified", "closed", "remaining_pids", "termination_requested"]
)
def test_terminal_requires_explicit_complete_helper_retirement(
    terminal_case: tuple[Json, Json, Json, Json], field: str
) -> None:
    """A missing lifecycle guarantee is not implicitly successful cleanup."""
    receipt, expected, target, identity = terminal_case
    del receipt["helper"][field]
    assert not smoke.terminal_matches(receipt, expected, DIGEST, target, identity)


def test_terminal_rejects_input_epoch_drift(
    terminal_case: tuple[Json, Json, Json, Json],
) -> None:
    """Terminal input must match the independent reference's frozen native epoch."""
    receipt, expected, target, identity = terminal_case
    expected["reference_input_tick"] += 1
    assert not smoke.terminal_matches(receipt, expected, DIGEST, target, identity)
    del expected["reference_input_tick"]
    assert not smoke.terminal_matches(receipt, expected, DIGEST, target, identity)


@pytest.mark.parametrize(
    ("confirmed", "forced"), [(True, False), (True, True), (False, False)]
)
@pytest.mark.parametrize("case", ["success", "cancel-first", "cancel-interior"])
def test_outer_success_cannot_hide_forced_or_unconfirmed_cleanup(  # noqa: PLR0913 - fixed cleanup/case matrix
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    authored: Json,
    case: str,
    *,
    confirmed: bool,
    forced: bool,
) -> None:
    """Actual outer admission rejects bad cleanup even after a successful child."""
    executable = tmp_path / "fixture.exe"
    executable.write_bytes(b"owned fixture stub, never executed")
    args = argparse.Namespace(
        fixture=executable,
        wheel=tmp_path / "app.whl",
        output=tmp_path / "out",
        case=case,
    )
    fixture, gui, module = Mock(), Mock(), Mock()
    fixture.process.pid = 321
    fixture.snapshot.side_effect = [
        {"exit_code": None, "members": [321]},
        {"exit_code": 0, "members": []},
    ]
    gui.process.pid = 654
    gui.snapshot.side_effect = [
        {"exit_code": None, "members": [654]},
        {"exit_code": None, "members": [654]},
        {"exit_code": None, "members": [654]},
        {"exit_code": 0, "members": []},
    ]
    module.verify_wheel.return_value = {"verified": True, "executable": "base"}
    module.ExternalOwner.side_effect = [fixture, gui]

    profile = {
        "request": {"pids": [654], "expected_base": "base"},
        "records": {"processes": [{"requested_pid": 654}]},
    }
    temporary = {"roles": {"peer": 777}, "peer": {"pid": 777}}
    monkeypatch.setattr(smoke, "serve_temporary", Mock(return_value=temporary))
    monkeypatch.setattr(smoke, "qualify_ownership", Mock(return_value=profile))
    monkeypatch.setattr(
        smoke,
        "serve_checkpoint",
        Mock(side_effect=[{"phase": p} for p in smoke.OWNERSHIP_PHASES]),
    )

    def start(_command: object) -> None:
        output = args.output / "executor"
        smoke.write(
            output / "result.json",
            {
                "passed": True,
                "credit_case": case,
                "cancel_validator_sha256": (
                    hashlib.sha256(
                        Path(smoke.__file__)
                        .with_name("observe_credit_cancel.py")
                        .read_bytes()
                    ).hexdigest()
                    if case != "success"
                    else None
                ),
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
        smoke.write(
            output / "preparation-ready.json", prepared_value(authored, ready=1.0)
        )
        log(output / "calls.jsonl", [event("process", 1.0)])
        smoke.write(
            output / "peer-identity.json",
            {"schema": 1, "session": "a" * 32, "peer": {"pid": 777}},
        )

    gui.start.side_effect = start
    monkeypatch.setattr(owned_helper, "HelperCommand", Mock(return_value=None))
    monkeypatch.setattr(smoke, "support", lambda: module)
    monkeypatch.setattr(smoke.time, "monotonic", lambda: 1.0)
    monkeypatch.setattr(smoke.uuid, "uuid4", lambda: SimpleNamespace(hex="a" * 32))
    cleanup = Mock(
        return_value={"confirmed": confirmed, "forced": forced, "errors": []}
    )
    monkeypatch.setattr(smoke, "release_owner", cleanup)
    passed = confirmed and not forced
    assert smoke.outer(args) == int(not passed)
    result = smoke.read(args.output / "receipt.json")
    assert result["passed"] is passed
    assert result["child"]["passed"] is True
    assert (args.output / "states" / "stop").read_bytes() == b""
    assert [call.args[0] for call in cleanup.call_args_list] == [gui, fixture]


class NativeBoundary:
    """Fake DLL functions exercise the real ctypes marshalling and guard code."""

    def __init__(self) -> None:
        self.foreground, self.pid, self.dpi = 456, 321, 96
        self.bounds = list(ROOT)
        self.input_tick, self.created = 123, 1234
        self.active, self.pmv2 = True, True
        self.fail: set[str] = set()
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.user = SimpleNamespace(
            **{
                name: Mock(side_effect=partial(self.invoke, name))
                for name in (
                    "GetForegroundWindow",
                    "GetWindowThreadProcessId",
                    "GetWindowRect",
                    "GetLastInputInfo",
                    "GetDpiForWindow",
                    "GetThreadDpiAwarenessContext",
                    "AreDpiAwarenessContextsEqual",
                )
            }
        )
        self.kernel = SimpleNamespace(
            **{
                name: Mock(side_effect=partial(self.invoke, name))
                for name in (
                    "OpenProcess",
                    "GetProcessTimes",
                    "WaitForSingleObject",
                    "CloseHandle",
                )
            }
        )

    def invoke(self, name: str, *args: Any) -> int:  # noqa: ANN401, C901, PLR0911 - heterogeneous fixed native ABI cases
        """Write real ctypes output pointers instead of substituting guard results."""
        self.calls.append((name, args))
        if name in self.fail:
            return 0
        if name == "OpenProcess":
            assert args == (0x00101000, 0, 321)
            return 77
        if name == "GetProcessTimes":
            assert args[0] == 77
            for pointer, value in zip(args[1:], (self.created, 0, 0, 0), strict=True):
                ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint64))[0] = value
            return 1
        if name == "WaitForSingleObject":
            assert args == (77, 0)
            return smoke.WAIT_TIMEOUT if self.active else 0
        if name == "CloseHandle":
            assert args == (77,)
            return 1
        if name == "GetForegroundWindow":
            return self.foreground
        if name == "GetWindowThreadProcessId":
            assert args[0] == 456
            ctypes.cast(args[1], ctypes.POINTER(ctypes.c_uint32))[0] = self.pid
            return 17
        if name == "GetWindowRect":
            assert args[0] in (456, 999)
            ctypes.cast(args[1], ctypes.POINTER(ctypes.c_int32 * 4)).contents[:] = (
                self.bounds
            )
            return 1
        if name == "GetLastInputInfo":
            info = ctypes.cast(args[0], ctypes.POINTER(smoke.InputInfo)).contents
            assert info.cbSize == ctypes.sizeof(smoke.InputInfo)
            info.dwTime = self.input_tick
            return 1
        if name == "GetDpiForWindow":
            assert args == (456,)
            return self.dpi
        if name == "GetThreadDpiAwarenessContext":
            return 888
        assert name == "AreDpiAwarenessContextsEqual"
        assert args[0] == 888
        assert args[1].value == ctypes.c_void_p(-4).value
        return int(self.pmv2)

    def dll(self, name: str, *, use_last_error: bool) -> SimpleNamespace:
        """Expose only the intended two DLL boundaries, never the host Win32 APIs."""
        assert use_last_error
        assert name in ("user32", "kernel32")
        return self.user if name == "user32" else self.kernel


@pytest.fixture
def native(monkeypatch: pytest.MonkeyPatch) -> NativeBoundary:
    """Install safe ABI fakes while retaining actual ReferenceGuard operations."""
    boundary = NativeBoundary()
    monkeypatch.setattr(smoke.c, "WinDLL", boundary.dll, raising=False)
    monkeypatch.setattr(smoke.c, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(
        smoke.c, "WinError", lambda code: OSError(code, "native failure"), raising=False
    )
    return boundary


def test_reference_guard_reads_actual_ctypes_outputs_and_pins_handle(
    native: NativeBoundary, authored: Json
) -> None:
    """Independent PID, outer rect, input, and FILETIME are read through pointers."""
    guard = smoke.ReferenceGuard(authored, 321)
    assert guard.created == 1234
    assert guard.initial == {
        "root_hwnd": 456,
        "pid": 321,
        "rect": list(ROOT),
        "input_tick": 123,
        "creation_100ns": 1234,
    }
    assert guard.check() == guard.initial
    assert native.user.GetWindowRect.argtypes == [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int32 * 4),
    ]
    assert native.kernel.OpenProcess.restype is ctypes.c_void_p
    assert native.kernel.GetProcessTimes.argtypes == [
        ctypes.c_void_p,
        *([ctypes.POINTER(ctypes.c_uint64)] * 4),
    ]
    guard.close()
    guard.close()
    native.kernel.CloseHandle.assert_called_once_with(77)
    native.kernel.OpenProcess.assert_called_once()


@pytest.mark.parametrize(
    "api",
    [
        "OpenProcess",
        "GetProcessTimes",
        "GetWindowThreadProcessId",
        "GetWindowRect",
        "GetLastInputInfo",
    ],
)
def test_reference_guard_native_init_failure_closes_only_acquired_handle(
    native: NativeBoundary, authored: Json, api: str
) -> None:
    """No leaked pin remains after a failed identity or native state acquisition."""
    native.fail.add(api)
    with pytest.raises((RuntimeError, OSError), match=r"guard failed|native failure"):
        smoke.ReferenceGuard(authored, 321)
    assert native.kernel.CloseHandle.call_count == int(api != "OpenProcess")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("foreground", 999),
        ("pid", 322),
        ("dpi", 120),
        ("active", False),
        ("pmv2", False),
    ],
)
def test_reference_guard_rejects_wrong_owned_root_or_execution_context(
    native: NativeBoundary, authored: Json, field: str, value: object
) -> None:
    """Wrong foreground/PID/DPI, exited pinned process, and non-PMv2 fail closed."""
    setattr(native, field, value)
    with pytest.raises(RuntimeError, match=r"guard failed|no longer alive"):
        smoke.ReferenceGuard(authored, 321)
    native.kernel.CloseHandle.assert_called_once_with(77)


@pytest.mark.parametrize("field", ["bounds", "input_tick"])
def test_reference_guard_detects_post_pin_native_drift(
    native: NativeBoundary, authored: Json, field: str
) -> None:
    """Unchanged fixture telemetry cannot override independent root or input drift."""
    guard = smoke.ReferenceGuard(authored, 321)
    if field == "bounds":
        native.bounds[0] += 1
    else:
        native.input_tick += 1
    try:
        with pytest.raises(RuntimeError, match="native guard changed"):
            guard.check()
    finally:
        guard.close()
    native.kernel.CloseHandle.assert_called_once_with(77)


def test_reference_guard_preserves_primary_failure_and_close_error(
    native: NativeBoundary, authored: Json
) -> None:
    """A failed handle close is retained as a note without hiding the native fault."""
    native.fail.update({"GetWindowRect", "CloseHandle"})
    with pytest.raises(RuntimeError, match="guard failed") as caught:
        smoke.ReferenceGuard(authored, 321)
    assert any("native failure" in note for note in caught.value.__notes__)
    native.kernel.CloseHandle.assert_called_once_with(77)


def test_reference_guard_failed_close_is_reported_without_double_close(
    native: NativeBoundary, authored: Json
) -> None:
    """An unsuccessful close is never reported as clean or blindly retried."""
    guard = smoke.ReferenceGuard(authored, 321)
    native.fail.add("CloseHandle")
    with pytest.raises(OSError, match="native failure"):
        guard.close()
    guard.close()
    native.kernel.CloseHandle.assert_called_once_with(77)


def test_reference_guard_rejects_unowned_fixture_before_opening_process(
    native: NativeBoundary, authored: Json
) -> None:
    """Authored PID mismatch fails before borrowing any native process handle."""
    with pytest.raises(RuntimeError, match="PID differs"):
        smoke.ReferenceGuard(authored, 322)
    assert native.calls == []


def test_reference_guard_rejects_reused_pid_before_reading_window_state(
    native: NativeBoundary, authored: Json
) -> None:
    """A newly opened PID cannot replace the fixture's frozen process lifetime."""
    native.created = 1235
    with pytest.raises(RuntimeError, match=r".+"):
        smoke.ReferenceGuard(authored, 321)
    native.kernel.GetProcessTimes.assert_called_once()
    native.kernel.CloseHandle.assert_called_once_with(77)
    native.user.GetForegroundWindow.assert_not_called()
    native.kernel.WaitForSingleObject.assert_not_called()


@pytest.mark.parametrize("bounds", [[960, 704, 992, 736], [899, 639, 931, 671]])
def test_witness_exclusion_uses_actual_native_bounds(
    native: NativeBoundary, authored: Json, bounds: list[int]
) -> None:
    """An actual overlap rejects even when Qt requested an off-root position."""
    guard = smoke.ReferenceGuard(authored, 321)
    native.bounds = bounds
    try:
        if bounds[0] >= ROOT[2]:
            assert guard.excluded_window(999) == bounds
        else:
            with pytest.raises(RuntimeError, match="overlaps"):
                guard.excluded_window(999)
    finally:
        guard.close()


@pytest.mark.parametrize("phase", ["initial", "poll", "close"])
@pytest.mark.parametrize("elapsed", [4.999, 5.0, 5.001])
def test_cleanup_budget_includes_every_inspection_and_close(
    monkeypatch: pytest.MonkeyPatch, phase: str, elapsed: float
) -> None:
    """Slow successful native calls cannot extend the entry-fixed cleanup budget."""
    clock = [2.0]
    owner = Mock()
    calls = [0]

    def snapshot() -> Json:
        calls[0] += 1
        if (phase == "initial" and calls[0] == 1) or (
            phase == "poll" and calls[0] == 2
        ):
            clock[0] += elapsed
        return {"exit_code": 0, "members": []}

    def close() -> list[str]:
        if phase == "close":
            clock[0] += elapsed
        return []

    owner.snapshot.side_effect = snapshot
    owner.close.side_effect = close
    monkeypatch.setattr(smoke.time, "monotonic", lambda: clock[0])
    result = smoke.release_owner(owner, 15.0)
    assert result["confirmed"] is (elapsed < 5.0)
    owner.close.assert_called_once_with()


def test_call_log_uses_post_read_time_for_live_records(tmp_path: Path) -> None:
    """A record newer than the pre-read clock is valid before post-read admission."""
    path = tmp_path / "calls.jsonl"
    log(path, [event("process", 0.0), event("call_start", 1.0), event("call_end", 1.1)])
    clock = iter([1.0, 1.2, 1.2])
    smoke.validate_calls(path, 0.0, lambda: next(clock), finished=True)


@pytest.mark.parametrize("times", [[14.9, 15.0], [14.9, 14.99, 15.0]])
def test_call_log_read_and_validation_cannot_cross_case_deadline(
    tmp_path: Path, times: list[float]
) -> None:
    """Both post-read and final admission enforce the original absolute limit."""
    path = tmp_path / "calls.jsonl"
    log(path, [event("process", 0.0)])
    clock = iter(times)
    with pytest.raises(RuntimeError, match="case deadline"):
        smoke.validate_calls(path, 0.0, lambda: next(clock), finished=True)


def test_pending_call_can_expire_while_log_is_read(tmp_path: Path) -> None:
    """The final pending-call age uses fresh time, never the pre-read snapshot."""
    path = tmp_path / "calls.jsonl"
    log(path, [event("process", 0.0), event("call_start", 1.0)])
    clock = iter([3.9, 3.99, 4.0])
    with pytest.raises(RuntimeError, match="blocked"):
        smoke.validate_calls(path, 0.0, lambda: next(clock), finished=False)


def prepared_value(authored: Json, *, ready: float = 2.0) -> Json:
    """Construct the real fixed metadata-only preparation receipt."""
    return {
        "schema": 1,
        "session": "a" * 32,
        "fixture_pid": 321,
        "executor_pid": 654,
        "ready": ready,
        "fixture": authored,
        "native": {
            "root_hwnd": 456,
            "pid": 321,
            "rect": list(ROOT),
            "input_tick": 123,
            "creation_100ns": 1234,
        },
    }


@pytest.mark.parametrize("now", [9.999, 10.0, 10.001])
def test_preparation_has_fixed_ten_second_admission(authored: Json, now: float) -> None:
    """Even an early ready receipt cannot be admitted at or after the prep cap."""
    value = prepared_value(authored)
    if now < 10:
        assert smoke.preparation_receipt(value, "a" * 32, 321, 0.0, now) is value
    else:
        with pytest.raises(ValueError, match="timing"):
            smoke.preparation_receipt(value, "a" * 32, 321, 0.0, now)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema", True),
        ("schema", 2),
        ("session", "b" * 32),
        ("fixture_pid", 322),
        ("fixture_pid", 321.0),
        ("executor_pid", True),
        ("executor_pid", 654.0),
        ("executor_pid", 0),
        ("executor_pid", -1),
        ("executor_pid", 2**32),
        ("ready", float("nan")),
        ("ready", float("inf")),
        ("ready", True),
        ("ready", -1),
        ("ready", 3.1),
    ],
)
def test_preparation_rejects_wrong_identity_and_times(
    authored: Json, field: str, value: object
) -> None:
    """Cross-session, wrong-process and malformed time cannot start capture."""
    receipt = prepared_value(authored)
    receipt[field] = value
    with pytest.raises(ValueError, match=r".+"):
        smoke.preparation_receipt(receipt, "a" * 32, 321, 0.0, 3.0)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pid", 322),
        ("root_hwnd", 457),
        ("creation_100ns", 1235),
        ("input_tick", True),
        ("input_tick", -1),
        ("input_tick", 2**32),
        ("rect", [100, 100, 200, 200]),
    ],
)
def test_preparation_requires_consistent_pinned_native_facts(
    authored: Json, field: str, value: object
) -> None:
    """Authored identity must agree with retained native process/root evidence."""
    receipt = prepared_value(authored)
    receipt["native"][field] = value
    with pytest.raises(ValueError, match=r".+"):
        smoke.preparation_receipt(receipt, "a" * 32, 321, 0.0, 3.0)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sequence", -1),
        ("sequence", 256),
        ("sequence", True),
        ("root_hwnd", 0),
        ("root_hwnd", True),
        ("mode", "borderless"),
    ],
)
def test_preparation_requires_initialized_correct_fixture(
    authored: Json, field: str, value: object
) -> None:
    """Only bounded initialized snapshots from the exact fixture mode qualify."""
    authored[field] = value
    with pytest.raises(ValueError, match=r".+"):
        smoke.preparation_receipt(prepared_value(authored), "a" * 32, 321, 0.0, 3.0)


@pytest.mark.parametrize("now", [9.999, 10.0, 10.001])
def test_case_handoff_must_arrive_before_prep_deadline(now: float) -> None:
    """Ready earlier is insufficient if the child receives its start too late."""
    value = {"schema": 1, "session": "a" * 32, "case_start": 9.5, "deadline": 24.5}
    if now < 10:
        assert smoke.case_handoff(value, "a" * 32, 9.0, 0.0, now) == 24.5
    else:
        with pytest.raises(ValueError, match="timing"):
            smoke.case_handoff(value, "a" * 32, 9.0, 0.0, now)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema", True),
        ("session", "b" * 32),
        ("case_start", 0.5),
        ("case_start", 4.0),
        ("case_start", float("nan")),
        ("deadline", 18.0),
        ("deadline", 16.0),
        ("deadline", float("inf")),
    ],
)
def test_case_handoff_never_changes_authoritative_budget(
    field: str, value: object
) -> None:
    """A changed, stale, future or extended case clock fails before pixels."""
    handoff = {"schema": 1, "session": "a" * 32, "case_start": 2.0, "deadline": 17.0}
    handoff[field] = value
    with pytest.raises(ValueError, match=r".+"):
        smoke.case_handoff(handoff, "a" * 32, 1.0, 0.0, 3.0)


def test_duplicate_handshake_is_not_overwritten(tmp_path: Path) -> None:
    """An already-published start cannot be replaced to restart the clock."""
    path = tmp_path / "case-start.json"
    smoke.write_handshake(path, {"deadline": 17.0})
    with pytest.raises(RuntimeError, match="Duplicate"):
        smoke.write_handshake(path, {"deadline": 25.0})
    assert smoke.read(path) == {"deadline": 17.0}


@pytest.mark.parametrize("deadline", [10.0, 24.5, 25.0])
def test_external_call_parser_enforces_explicit_active_deadline(
    tmp_path: Path, deadline: float
) -> None:
    """Prep and capture use distinct fixed caps; validation never starts another."""
    path = tmp_path / "calls.jsonl"
    log(path, [event("process", 0.0)])
    smoke.validate_calls(
        path, 0.0, lambda: deadline - 0.001, finished=False, deadline=deadline
    )
    with pytest.raises(RuntimeError, match="case deadline"):
        smoke.validate_calls(
            path, 0.0, lambda: deadline, finished=False, deadline=deadline
        )


@pytest.mark.parametrize(
    "failure", [None, "input", "fixture", "late", "missing", "session"]
)
def test_real_preparation_handoff_retains_guards_and_fixed_deadlines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, authored: Json, failure: str | None
) -> None:
    """Actual metadata handshake keeps its original pin and admits no drift."""
    output, states = tmp_path / "executor", tmp_path / "states"
    output.mkdir()
    states.mkdir()
    smoke.write(states / "0000.json", authored)
    clock = [2.0]
    monkeypatch.setattr(smoke.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(smoke.time, "sleep", lambda _: clock.__setitem__(0, 10.0))
    guard = Mock()
    native = prepared_value(authored)["native"]
    guard.check.side_effect = [
        native,
        RuntimeError("Frozen reference native guard changed")
        if failure == "input"
        else native,
    ]
    original = smoke.write_handshake

    def publish(path: Path, value: Json) -> None:
        original(path, value)
        assert path.name == "preparation-ready.json"
        assert guard.check.call_count == 1
        if failure == "fixture":
            changed = json.loads(json.dumps(authored))
            changed["state"]["target"]["vertical"] = 120
            smoke.write(states / "0001.json", {**changed, "sequence": 1})
        if failure != "missing":
            original(
                output / "case-start.json",
                {
                    "schema": 1,
                    "session": "b" * 32 if failure == "session" else "a" * 32,
                    "case_start": 2.0,
                    "deadline": 17.0,
                },
            )
        if failure == "late":
            clock[0] = 10.0

    monkeypatch.setattr(smoke, "write_handshake", publish)
    journal = smoke.Journal(output / "calls.jsonl", 10.0)
    try:
        if failure is None:
            result = smoke.prepare_case(
                output,
                states,
                "a" * 32,
                0.0,
                321,
                authored,
                smoke.fixture_key(authored),
                guard,
                journal,
            )
            assert result["case_start"] == 2.0
            assert journal.deadline == 17.0
            assert guard.check.call_count == 2
        else:
            with pytest.raises((RuntimeError, ValueError), match=r".+"):
                smoke.prepare_case(
                    output,
                    states,
                    "a" * 32,
                    0.0,
                    321,
                    authored,
                    smoke.fixture_key(authored),
                    guard,
                    journal,
                )
            if failure in ("late", "missing", "session"):
                assert guard.check.call_count == 1
                assert journal.deadline == 10.0
        assert smoke.read(output / "preparation-ready.json")["native"] == native
        assert (
            smoke.read(output / "preparation-ready.json")["executor_pid"]
            == smoke.os.getpid()
        )
    finally:
        journal.stream.close()


@pytest.mark.parametrize(
    "failure",
    [
        "late-read",
        "late-snapshot",
        "wrong-job",
        "wrong-pid",
        "terminal",
        "gui-descendant",
    ],
)
def test_outer_preparation_failure_never_publishes_case_start(  # noqa: PLR0915 - full external failure path
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, authored: Json, failure: str
) -> None:
    """Actual external admission rejects late evidence and lost ownership."""
    executable = tmp_path / "fixture.exe"
    executable.write_bytes(b"owned fixture stub, never executed")
    args = argparse.Namespace(
        fixture=executable, wheel=tmp_path / "app.whl", output=tmp_path / "out"
    )
    fixture, gui, module = Mock(), Mock(), Mock()
    fixture.process.pid, gui.process.pid = 321, 654
    gui.snapshot.return_value = {
        "exit_code": 1 if failure == "terminal" else None,
        "members": []
        if failure == "terminal"
        else ([654, 765] if failure == "gui-descendant" else [654]),
    }
    clock = [1.0]

    def snapshot() -> Json:
        if failure == "late-snapshot":
            clock[0] = 11.0
        return {
            "exit_code": None,
            "members": [322] if failure == "wrong-job" else [321],
        }

    fixture.snapshot.side_effect = snapshot
    module.verify_wheel.return_value = {"verified": True, "executable": "base"}
    module.ExternalOwner.side_effect = [fixture, gui]

    def start(_command: object) -> None:
        output = args.output / "executor"
        receipt = prepared_value(authored, ready=1.0)
        if failure == "wrong-pid":
            receipt["fixture_pid"] = 322
        smoke.write(output / "preparation-ready.json", receipt)
        smoke.write(
            output / "result.json", {"passed": False, "first_failure": "fixture failed"}
        )
        log(output / "calls.jsonl", [event("process", 1.0)])

    original_read = smoke.read

    def read_receipt(path: Path) -> Json:
        value = original_read(path)
        if path.name == "preparation-ready.json" and failure == "late-read":
            clock[0] = 11.0
        return value

    gui.start.side_effect = start
    monkeypatch.setattr(smoke, "read", read_receipt)
    monkeypatch.setattr(owned_helper, "HelperCommand", Mock(return_value=None))
    monkeypatch.setattr(smoke, "support", lambda: module)
    monkeypatch.setattr(smoke.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(smoke.uuid, "uuid4", lambda: SimpleNamespace(hex="a" * 32))
    cleanup = Mock(return_value={"confirmed": True, "forced": False, "errors": []})
    monkeypatch.setattr(smoke, "release_owner", cleanup)
    monkeypatch.setattr(
        smoke,
        "qualify_ownership",
        Mock(side_effect=RuntimeError("Unqualified ownership profile"))
        if failure == "gui-descendant"
        else Mock(return_value={}),
    )
    assert smoke.outer(args) == 1
    result = original_read(args.output / "receipt.json")
    assert result["passed"] is False
    assert result["preparation_start"] == 1.0
    assert result["preparation_deadline"] == 11.0
    assert result["absolute_deadline"] == 26.0
    assert "case_start" not in result
    assert not (args.output / "executor" / "case-start.json").exists()
    assert all(call.args[1] == 11.0 for call in cleanup.call_args_list)
    if failure == "gui-descendant":
        evidence = result["preparation_ownership"]
        assert evidence == {
            "session": "a" * 32,
            "ready": 1.0,
            "executor_pid": 654,
            "gui_launch_pid": 654,
            "gui_snapshot": {"exit_code": None, "members": [654, 765]},
            "gui_snapshot_started": 1.0,
            "gui_snapshot_finished": 1.0,
        }
        fixture.snapshot.assert_not_called()
        assert (
            result["first_failure"] == "RuntimeError('Unqualified ownership profile')"
        )
        assert "topology_error" not in result
    if failure == "wrong-job":
        assert result["preparation_ownership"]["fixture_launch_pid"] == 321
        assert result["preparation_ownership"]["fixture_snapshot"] == {
            "exit_code": None,
            "members": [322],
        }


@pytest.mark.parametrize("finished", [9.999, 10.0, 10.001])
def test_telemetry_read_crossing_preparation_deadline_never_acquires_native_pin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, authored: Json, finished: float
) -> None:
    """The actual child acquisition path checks fresh time after reading telemetry."""
    clock = [9.9]
    monkeypatch.setattr(smoke.time, "monotonic", lambda: clock[0])

    def latest(_states: Path) -> Json:
        clock[0] = finished
        return authored

    monkeypatch.setattr(smoke, "latest", latest)
    constructor = Mock()
    monkeypatch.setattr(smoke, "ReferenceGuard", constructor)
    journal = smoke.Journal(tmp_path / "calls.jsonl", 10.0)
    try:
        if finished < 10.0:
            _, _, guard = smoke.pin_preparation(tmp_path, 321, journal)
            assert guard is constructor.return_value
            constructor.assert_called_once_with(authored, 321)
        else:
            with pytest.raises(RuntimeError, match="deadline"):
                smoke.pin_preparation(tmp_path, 321, journal)
            constructor.assert_not_called()
    finally:
        journal.stream.close()


def test_slow_successful_preparation_pin_is_released_before_rejection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, authored: Json
) -> None:
    """A returned owned handle survives post-call timing rejection only until close."""
    clock = [9.0]
    guard = Mock()
    monkeypatch.setattr(smoke.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(smoke, "latest", lambda _states: authored)

    def construct(_fixture: Json, _pid: int) -> Mock:
        clock[0] = 10.0
        return guard

    monkeypatch.setattr(smoke, "ReferenceGuard", construct)
    journal = smoke.Journal(tmp_path / "calls.jsonl", 10.0)
    try:
        with pytest.raises(RuntimeError, match="deadline"):
            smoke.pin_preparation(tmp_path, 321, journal)
        guard.close.assert_called_once_with()
    finally:
        journal.stream.close()


def test_preparation_executor_pid_is_required(authored: Json) -> None:
    """Missing executor PID is not inferred from the launch process or job list."""
    value = prepared_value(authored)
    del value["executor_pid"]
    with pytest.raises(ValueError, match="fields"):
        smoke.preparation_receipt(value, "a" * 32, 321, 0.0, 3.0)


@pytest.mark.parametrize(
    "pids", [[], list(range(1, 10)), [1, 1], [True], [1.0], [0], [-1], [2**32], "1"]
)
def test_topology_pid_scope_is_strictly_bounded(pids: object) -> None:
    """Only one to eight unique positive uint32 targets may be inspected."""
    with pytest.raises(ValueError, match="PID"):
        smoke.topology_pids(pids)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("schema", True),
        ("session", "other"),
        ("deadline", 11.0),
        ("original_snapshot_time", float("nan")),
        ("original_snapshot_time", -1.0),
        ("original_snapshot_time", 3.0),
    ],
)
def test_topology_request_never_changes_scope_or_budget(
    key: str, value: object
) -> None:
    """Diagnostics cannot add time or switch sessions after the original failure."""
    request = {
        "schema": 1,
        "session": "a" * 32,
        "pids": [1, 2],
        "original_snapshot_time": 1.0,
        "deadline": 10.0,
    }
    request[key] = value
    with pytest.raises(ValueError, match=r".+"):
        smoke.topology_request(request, "a" * 32, 10.0, 2.0)


@pytest.mark.parametrize(
    "failure", [None, "missing-ack", "late-ack", "wrong-session", "collect-error"]
)
def test_child_diagnostic_keeps_pins_until_membership_then_releases(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str | None
) -> None:
    """Actual child handshake always releases pins, without starting capture."""
    clock = [2.0]
    monkeypatch.setattr(smoke.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(smoke.time, "sleep", lambda _: clock.__setitem__(0, 10.0))
    request = {
        "schema": 1,
        "session": "a" * 32,
        "pids": [1, 2],
        "original_snapshot_time": 1.0,
        "deadline": 10.0,
    }
    smoke.write(tmp_path / "topology-request.json", request)
    pins = Mock()
    pins.close.return_value = []
    pins.collect.return_value = {
        "processes": [{"requested_pid": 1}, {"requested_pid": 2}]
    }
    pins.finish.return_value = {
        "processes": [{"requested_pid": 1}, {"requested_pid": 2}],
        "cleanup_errors": [],
    }
    if failure == "collect-error":
        pins.collect.side_effect = OSError("inspection failed")
    module = SimpleNamespace(PinnedProcesses=Mock(return_value=pins))
    monkeypatch.setattr(smoke, "topology_module", lambda: module)
    original = smoke.write_handshake

    def publish(path: Path, value: Json) -> None:
        original(path, value)
        if path.name != "topology-pinned.json":
            return
        pins.close.assert_not_called()
        pins.finish.assert_not_called()
        if failure == "missing-ack":
            return
        original(
            tmp_path / "topology-membership.json",
            {
                "schema": 1,
                "session": "other" if failure == "wrong-session" else "a" * 32,
                "pids": [1, 2],
                "membership": {
                    "started": 2.0,
                    "finished": 2.0,
                    "snapshot": {"exit_code": None, "members": [1, 2]},
                },
            },
        )
        if failure == "late-ack":
            clock[0] = 10.0

    monkeypatch.setattr(smoke, "write_handshake", publish)
    journal = smoke.Journal(tmp_path / "calls.jsonl", 10.0)
    try:
        if failure is None:
            smoke.collect_topology(tmp_path, "a" * 32, journal)
            pins.finish.assert_called_once_with()
            assert (tmp_path / "topology-result.json").exists()
        else:
            with pytest.raises((ValueError, RuntimeError, OSError), match=r".+"):
                smoke.collect_topology(tmp_path, "a" * 32, journal)
            pins.finish.assert_not_called()
            assert not (tmp_path / "topology-result.json").exists()
        pins.close.assert_called_once_with()
        assert not (tmp_path / "case-start.json").exists()
    finally:
        journal.stream.close()


@pytest.mark.parametrize(
    "failure", [None, "wrong-target", "late-snapshot", "late-final", "late-call"]
)
def test_outer_diagnostic_has_one_temporal_membership_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str | None
) -> None:
    """Pins are reported before a single later snapshot, never a case admission."""
    clock = [2.0]
    monkeypatch.setattr(smoke.time, "monotonic", lambda: clock[0])
    log(tmp_path / "calls.jsonl", [event("process", 0.0)])
    gui = Mock()

    def snapshot() -> Json:
        if failure == "late-snapshot":
            clock[0] = 10.0
        return {"exit_code": None, "members": [1, 2]}

    gui.snapshot.side_effect = snapshot
    original = smoke.write_handshake

    def publish(path: Path, value: Json) -> None:
        original(path, value)
        if path.name == "topology-request.json":
            original(
                tmp_path / "topology-pinned.json",
                {
                    "schema": 1,
                    "session": "a" * 32,
                    "pids": [1, 2],
                    "collected": {
                        "processes": [
                            {"requested_pid": 1},
                            {"requested_pid": 3 if failure == "wrong-target" else 2},
                        ]
                    },
                },
            )
        elif path.name == "topology-membership.json":
            original(
                tmp_path / "topology-result.json",
                {
                    "schema": 1,
                    "session": "a" * 32,
                    "pids": [1, 2],
                    "final": {
                        "processes": [{"requested_pid": 1}, {"requested_pid": 2}],
                        "cleanup_errors": [],
                    },
                },
            )
            if failure == "late-final":
                clock[0] = 10.0
            if failure == "late-call":
                clock[0] = 4.0
                log(
                    tmp_path / "calls.jsonl",
                    [
                        event("process", 0.0),
                        event("call_start", 1.0, "topology.CloseHandle.1"),
                        event("call_end", 4.0, "topology.CloseHandle.1"),
                    ],
                )

    monkeypatch.setattr(smoke, "write_handshake", publish)
    status = {"exit_code": None, "members": [1, 2]}
    if failure is None:
        result = smoke.diagnose_topology(
            tmp_path, "a" * 32, status, 1.0, gui, 0.0, 10.0
        )
        assert result["original_snapshot"] == status
        assert result["later_membership"]["snapshot"] == status
        assert result["final"]["cleanup_errors"] == []
    else:
        with pytest.raises((ValueError, RuntimeError), match=r".+"):
            smoke.diagnose_topology(tmp_path, "a" * 32, status, 1.0, gui, 0.0, 10.0)
    assert gui.snapshot.call_count == (0 if failure == "wrong-target" else 1)
    assert not (tmp_path / "case-start.json").exists()


def test_diagnostic_call_errors_never_change_normal_watchdog_admission(
    tmp_path: Path,
) -> None:
    """Only paired topology errors are evidence during an already-failed case."""
    path = tmp_path / "calls.jsonl"
    log(
        path,
        [
            event("process", 0),
            event("call_start", 1, "topology.open"),
            event("call_error", 1.1, "topology.open"),
        ],
    )
    smoke.validate_calls(
        path, 0.0, lambda: 2.0, finished=False, diagnostic=True, deadline=10.0
    )
    with pytest.raises(RuntimeError, match="failure"):
        smoke.validate_calls(path, 0.0, lambda: 2.0, finished=False, deadline=10.0)
    log(
        path,
        [
            event("process", 0),
            event("call_start", 1, "reference.pin"),
            event("call_error", 1.1, "reference.pin"),
        ],
    )
    with pytest.raises(RuntimeError, match="failure"):
        smoke.validate_calls(
            path, 0.0, lambda: 2.0, finished=False, diagnostic=True, deadline=10.0
        )


@pytest.mark.parametrize(
    "records",
    [
        [],
        [{"requested_pid": True}],
        [{"requested_pid": 1}, {"requested_pid": 1}],
        [{"requested_pid": 3}],
        [None],
    ],
)
def test_topology_records_cannot_add_or_alias_requested_pids(records: object) -> None:
    """Boolean aliases, duplicates and unrelated records do not enter diagnostics."""
    with pytest.raises(ValueError, match=r".+"):
        smoke.topology_targets({"processes": records}, (1, 2))
