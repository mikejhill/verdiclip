"""Strict moving EDIT wire contracts, with isolated producer handoff evidence."""

from __future__ import annotations

import copy
import json
from typing import Any, cast

import pytest
from tests.capture.moving_wire_fixtures import (
    make_begin,
    make_move_ack,
    make_observation,
    make_proof,
    producer_state,
)

from verdiclip.capture.edit_observation import (
    EditObservation,
    baseline_identity_sha256,
    canonical_json,
    parse_json,
    validate_continuity,
    validate_native_state,
)
from verdiclip.platform import moving_edit_protocol as wire
from verdiclip.platform import sequence_protocol as old


def test_profile_constants_are_separate() -> None:
    """Larger bounds must never silently widen the qualified older profile."""
    assert (wire.VERSION, wire.PEER_SCHEMA, wire.RUNTIME_SCHEMA) == (4, 5, 6)
    assert (wire.MOVING_SCHEMA, wire.CREDIT_SCHEMA) == (1, 2)
    assert (wire.MAX_MESSAGE_BYTES, wire.MAX_OBSERVATION_BYTES) == (65536, 32768)
    assert (wire.MAX_FRAME_PROOF_BYTES, wire.MAX_MOVE_ACK_BYTES) == (49152, 20480)
    assert (wire.MAX_REQUESTS, wire.MAX_SEQUENCE_FRAMES) == (16, 7)
    assert old.VERSION == 3
    assert old.MAX_MESSAGE_BYTES == 16384


def test_canonical_framing_round_trip_and_failure_latch() -> None:
    """Fragmented canonical messages survive; noncanonical/malformed input latches."""
    message = wire.Message("request", 1, {"operation": "moving_begin"})
    encoded = wire.encode(message)
    decoder = wire.Decoder()
    assert decoder.feed(encoded[:3]) == []
    assert decoder.feed(encoded[3:]) == [message]
    with pytest.raises(wire.ProtocolError):
        decoder.feed(b"\0\1\0\1")
    assert decoder.failed
    assert not decoder.buffer
    with pytest.raises(wire.ProtocolError):
        decoder.feed(encoded)


@pytest.mark.parametrize(
    "payload",
    [
        b'{"a":1,"a":2}',
        b'{ "a":1}',
        b'{"a":NaN}',
        b'{"a":1e999}',
        b'{"a":-Infinity}',
        b'"x"',
    ],
)
def test_parser_rejects_untrusted_json(payload: bytes) -> None:
    """Reject duplicate/noncanonical/nonfinite/non-object records."""
    with pytest.raises(wire.ProtocolError):
        parse_json(payload, 65536)


def test_predecode_depth_and_node_limits() -> None:
    """Nested input is rejected before the JSON decoder builds a container tree."""
    with pytest.raises(ValueError, match="depth"):
        parse_json(b'{"x":' + b"[" * 32 + b"0" + b"]" * 32 + b"}", 65536)
    with pytest.raises(ValueError, match="node"):
        parse_json(b'{"x":[' + b"0," * 4096 + b"0]}", 65536)


def test_wire_cannot_admit_eighth_frame_or_nonnumeric_id() -> None:
    """The moving profile fixed ID plan does not inherit old 32/8 maxima."""
    for request_id in (True, 17, -1):
        with pytest.raises(wire.ProtocolError):
            wire.encode(wire.Message("request", request_id, {}))
    for kind, request_id in (("request", 0), ("ready", 1), ("ping", 1)):
        with pytest.raises(wire.ProtocolError):
            wire.encode(wire.Message(kind, request_id, {}))


def test_launch_strict_schema_and_canonicality() -> None:
    """Moving launch has its own namespace, fixed schemas, and nonzero IDs."""
    launch = wire.MovingEditLaunch(
        "VerdiClip-moving-edit-" + "a" * 32,
        "b" * 32,
        7,
        1_000_000_000,
        31_000_000_000,
        "c" * 32,
        8,
        123,
    )
    assert wire.deserialize_launch(launch.serialize()) == launch
    value = json.loads(launch.serialize())
    assert set(value) == {
        "pipe_name",
        "epoch",
        "parent_pid",
        "protocol_version",
        "peer_schema",
        "credit_schema",
        "moving_schema",
        "runtime_schema",
        "sequence_started_ns",
        "sequence_deadline_ns",
        "evidence_schema",
        "evidence_token",
        "evidence_reader_pid",
        "evidence_reader_created_100ns",
    }
    for key in ("runtime_schema", "moving_schema", "peer_schema", "credit_schema"):
        bad = dict(value, **{key: True})
        with pytest.raises(wire.ProtocolError):
            wire.deserialize_launch(canonical_json(bad).decode())


def test_move_and_frame_stamps_share_original_session_reserve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No renewed frame or move clock can consume the final eight-second reserve."""
    now = [21_000_000_000]
    monkeypatch.setattr(wire.time, "monotonic_ns", lambda: now[0])
    deadline = wire.SequenceDeadline(30_000_000_000)
    deadline.move(now[0], 22_000_000_000, step_id=6)
    now[0] = 22_000_000_000
    with pytest.raises(wire.ProtocolError):
        deadline.check()
    now[0] = 21_000_000_000
    with pytest.raises(wire.ProtocolError):
        deadline.check()
    restored = wire.SequenceDeadline(30_000_000_000)
    now[0] = 28_000_000_000
    restored.frame(now[0], 30_000_000_000, step_id=7)
    now[0] = 29_999_999_999
    assert restored.check() == now[0]
    now[0] += 1
    with pytest.raises(wire.ProtocolError):
        restored.check()


@pytest.mark.parametrize("frame_id", range(1, 8))
def test_actual_producer_to_wire_to_consumer(frame_id: int) -> None:
    """The extracted snapshot, strict observation, full proof and wire all agree."""
    proof = make_proof(frame_id)
    encoded = wire.encode(wire.Message("response", 2 * frame_id + 1, proof.value))
    decoded = wire.Decoder().feed(encoded)
    consumed = wire.MovingFrameProof(wire.canonical_json(decoded[0].payload))
    assert consumed == proof
    assert consumed.observation.frame_identity().frame_id == frame_id
    assert consumed.observation.value["native_state"]["native"]["vertical_bar"] == [
        553,
        60,
        570,
        473,
    ]
    assert (
        consumed.observation.value["native_state"]["font"]["attestation"]["epoch"]
        != consumed.value["epoch"]
    )


def test_entire_allowed_delta_mask_and_restoration() -> None:
    """All seven produced states preserve the complete invariant guard."""
    observations = [make_observation(i) for i in range(1, 8)]
    for observation in observations:
        validate_continuity(observations[0], observation)
    for before, after in ((3, 4), (1, 5), (0, 6)):
        validate_continuity(observations[before], observations[after])
        assert (
            observations[before].value["native_state"]
            == observations[after].value["native_state"]
        )
    assert len({item.value["baseline_identity_sha256"] for item in observations}) == 1


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("native", "vertical_bar"), None),
        (("native", "dpi"), True),
        (("native", "style"), 0x503008C4),
        (("native", "window"), [60, 60, 800, 473]),
        (("scroll", "bar_state"), [0x8000, 0, 0, 0, 0, 0]),
        (("scroll", "thumb"), [True, 0, 1]),
        (("scroll", "track"), 14),
        (("scroll_pattern", 0), -0.0),
        (("scroll_pattern", 1), 23),
        (("pattern_fixed", 0), 0),
        (("text", "lines"), 39),
        (("text", "selection"), [0, True]),
        (("text_layout", "pitch"), 15),
        (("text_layout", "points", "3", "index"), 134),
        (("text_layout", "points", "13", "first"), [0, 159]),
        (("font", "wm_getfont_opaque"), 889),
        (("font", "attestation", "owner_measured_metrics", "maximum_width"), 2**31),
        (("monitors", 0, 5), 1),
        (("input_tick",), -1),
    ],
)
def test_strict_native_field_mutations(path: tuple[object, ...], value: object) -> None:
    """No WPF blanket scroll exception or integer/float coercion survives."""
    state, _ = producer_state(3)
    current: Any = state
    for key in path[:-1]:
        current = current[key]
    current[path[-1]] = value
    with pytest.raises(wire.ProtocolError):
        validate_native_state(state, 3)


def test_original_rail_predicates_do_not_invent_endpoint_equations() -> None:
    """SDK-typed nonzero rail child states and signed thumb values remain evidence."""
    state, _ = producer_state(3)
    state["scroll"]["bar_state"] = [0, 91, 82, 73, 64, 55]
    state["scroll"]["thumb"] = [-2, 1000, -99]
    assert validate_native_state(state, 3)["scroll"]["thumb"] == [-2, 1000, -99]


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("input_tick",), 16),
        (("monitors", 0, 0), 2),
        (("native", "vertical_bar"), [553, 60, 571, 473]),
        (("font", "attestation", "owner_measured_metrics", "maximum_width"), 11),
    ],
)
def test_invariant_identity_changes_never_survive_baseline(
    path: tuple[object, ...], value: object
) -> None:
    """Even valid native values cannot change during one accepted sequence."""
    before = make_observation()
    data = before.value
    current: Any = data["native_state"]
    for key in path[:-1]:
        current = current[key]
    current[path[-1]] = value
    if path == ("native", "vertical_bar"):
        data["native_state"]["native"]["window"][2] = 571
    data["baseline_identity_sha256"] = baseline_identity_sha256(
        data["native_state"], data["identity_evidence"]
    )
    after = EditObservation.from_value(data)
    with pytest.raises(ValueError, match="continuity"):
        validate_continuity(before, after)


def test_runtime_id_bound_is_32_and_identity_is_not_hwnd_only() -> None:
    """Retained RuntimeIds and full structures must be present and unchanged."""
    before = make_observation()
    data = before.value
    data["identity_evidence"]["panel_runtime_id"] = list(range(33))
    with pytest.raises(wire.ProtocolError):
        EditObservation.from_value(data)
    for changed in ([True], [2**31], []):
        data["identity_evidence"]["panel_runtime_id"] = changed
        with pytest.raises(wire.ProtocolError):
            EditObservation.from_value(data)
    data["identity_evidence"]["panel_runtime_id"] = list(range(32))
    data["baseline_identity_sha256"] = baseline_identity_sha256(
        data["native_state"], data["identity_evidence"]
    )
    after = EditObservation.from_value(data)
    with pytest.raises(wire.ProtocolError):
        validate_continuity(before, after)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("request_id", 2),
        ("frame_id", 8),
        ("step_id", True),
        ("schema", 2),
        ("epoch", "0" * 32),
        ("move_sha256", "A" * 64),
        ("profile", "owned-wpf"),
    ],
)
def test_observation_correlation_rejects_other_profiles(
    field: str, value: object
) -> None:
    """No relabeling frame1 or borrowing a prior operation is allowed."""
    data = make_observation().value
    data[field] = value
    with pytest.raises(wire.ProtocolError):
        EditObservation.from_value(data)


def test_move_ack_actual_completion_and_prequeue_limit() -> None:
    """Readback acknowledgments bind timestamps, state, and the fixed move ID."""
    ack = make_move_ack()
    assert (
        wire.validate_move_ack(
            ack, session_deadline_ns=30_000_000_000, now_ns=1_000_000_010
        )
        == ack
    )
    for key, bad in (
        ("completed_ns", ack["deadline_ns"]),
        ("readback_ns", ack["write_returned_ns"] - 1),
        ("request_id", 3),
        ("expected_line", 0),
        ("restoring", True),
        ("native_state_sha256", "f" * 64),
    ):
        mutated = copy.deepcopy(ack)
        mutated[key] = bad
        with pytest.raises(wire.ProtocolError):
            wire.validate_move_ack(mutated)
    with pytest.raises(wire.ProtocolError):
        wire.validate_move_ack(ack, now_ns=ack["deadline_ns"])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sequence_started_ns", True),
        ("sequence_deadline_ns", True),
        ("sequence_started_ns", 31_000_000_000),
        ("sequence_deadline_ns", 30_000_000_001),
        ("sequence_deadline_ns", 0),
    ],
)
def test_original_begin_deadline_rejects_reset_or_invalid_stamp(
    field: str, value: object
) -> None:
    """The peer receives and preserves the worker-entry ceiling without renewal."""
    value_dict = make_begin()
    assert wire.validate_begin_payload(value_dict, now_ns=1_000_000_000) == value_dict
    value_dict[field] = value
    with pytest.raises(wire.ProtocolError):
        wire.validate_begin_payload(value_dict, now_ns=1_000_000_000)


def test_delayed_begin_and_independent_fixture_epoch() -> None:
    """A delayed begin has only the original allowance left; fixture epoch survives."""
    begin: dict[str, Any] = dict(
        make_begin(),
        command_started_ns=21_000_000_000,
        command_deadline_ns=22_000_000_000,
    )
    assert wire.validate_begin_payload(begin, now_ns=21_999_999_999) == begin
    with pytest.raises(wire.ProtocolError):
        wire.validate_begin_payload(begin, now_ns=22_000_000_000)
    with pytest.raises(wire.ProtocolError):
        wire.validate_begin_payload(dict(begin, sequence_started_ns=1), now_ns=0)
    assert begin["profile_evidence"]["font_attestation"]["epoch"] == "1" * 32


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("request_id", 2),
        ("image_sha256", "b" * 64),
        ("credit_chunks", 9),
        ("accepted_bytes", 610826),
        ("verified_ns", 3_000_000_000),
        ("baseline_identity_sha256", "c" * 64),
        ("move_sha256", "d" * 64),
    ],
)
def test_complete_proof_rejects_credit_image_time_and_move_mutations(
    field: str, value: object
) -> None:
    """Headers and all twenty credit events are part of the moving proof."""
    proof = make_proof().value
    proof[field] = value
    with pytest.raises(wire.ProtocolError):
        wire.MovingFrameProof(canonical_json(proof))


def test_exact_selected_target_survives_wire_and_tampering_fails() -> None:
    """The original point is explicit, never synthesized from image origin."""
    observation = make_observation()
    assert observation.target.point == (80, 80)
    value = observation.value
    del value["target"]
    with pytest.raises(wire.ProtocolError):
        EditObservation.from_value(value)
    for key, changed in (
        ("hwnd", 101),
        ("pid", 98),
        ("process_created_100ns", 12346),
        ("point", [59, 60]),
        ("point", [True, 80]),
    ):
        value = observation.value
        value["target"][key] = changed
        with pytest.raises(wire.ProtocolError):
            EditObservation.from_value(value)
    value = observation.value
    value["target"]["point"] = [81, 80]
    other = EditObservation.from_value(value)
    assert other.target != observation.target
    with pytest.raises(wire.ProtocolError, match="continuity"):
        validate_continuity(observation, other)


def test_frame_accept_contains_full_proof_and_checks_chain_and_retained_evidence() -> (
    None
):
    """Peer does not authorize pixels from a summary-only acceptance hash."""
    proof = make_proof()
    header = wire.FrameHeader(
        proof.observation.frame_identity(), bytes.fromhex(proof.value["image_sha256"])
    )
    accepted: dict[str, Any] = {
        "epoch": proof.value["epoch"],
        "frame_id": 1,
        "step_id": 1,
        "proof_sha256": proof.digest.hex(),
        "sequence_sha256": wire.hashlib.sha256(bytes(32) + proof.digest).hexdigest(),
        "proof": proof.value,
    }
    assert (
        wire.validate_frame_accept(
            accepted,
            previous_chain=bytes(32),
            expected_observation=proof.observation,
            expected_header=header,
            now_ns=1_500_000_000,
        )
        == accepted
    )
    summary_only = dict(accepted)
    del summary_only["proof"]
    with pytest.raises(wire.ProtocolError):
        wire.validate_frame_accept(summary_only)
    for key, changed in (
        ("proof_sha256", "c" * 64),
        ("sequence_sha256", "c" * 64),
        ("step_id", 2),
        ("frame_id", True),
    ):
        with pytest.raises(wire.ProtocolError):
            wire.validate_frame_accept(
                dict(accepted, **{key: changed}), previous_chain=bytes(32)
            )
    changed = copy.deepcopy(accepted)
    changed["proof"]["credit_events"][0][3] = 1
    with pytest.raises(wire.ProtocolError):
        wire.validate_frame_accept(changed, previous_chain=bytes(32))
    with pytest.raises(wire.ProtocolError):
        wire.validate_frame_accept(accepted, expected_observation=make_observation(2))
    with pytest.raises(wire.ProtocolError):
        wire.validate_frame_accept(accepted, now_ns=3_000_000_000)
    changed["proof"]["overflow"] = "x" * 65536
    with pytest.raises(wire.ProtocolError):
        wire.validate_frame_accept(changed)


def test_control_byte_cap_and_credit_record_cap() -> None:
    """Read buffers and complete credit records use different hard bounds."""
    prefix = wire.encode(wire.Message("response", 1, {"text": ""}))
    amount = 65540 - len(prefix)
    exact = wire.encode(wire.Message("response", 1, {"text": "x" * amount}))
    assert len(exact) == 65540
    assert wire.Decoder().feed(exact)[0].payload == {"text": "x" * amount}
    with pytest.raises(wire.ProtocolError):
        wire.encode(wire.Message("response", 1, {"text": "x" * (amount + 1)}))
    with pytest.raises(wire.ProtocolError):
        wire.Decoder().feed(exact + b"x")
    with pytest.raises(wire.ProtocolError):
        wire.encode(wire.Message("frame_wait", 3, {"text": "x" * 512}))
    with pytest.raises(wire.ProtocolError):
        wire.encode(cast("Any", {}))
    decoder = wire.Decoder()
    assert decoder.feed(exact[:8]) == []
    assert decoder.feed(exact[8:])[0].request_id == 1


def test_record_budgets_count_both_directions_and_never_reset() -> None:
    """A failed global or per-frame reservation permanently latches."""
    budget = wire.SequenceBudget()
    for _ in range(64):
        budget.record(1)
    with pytest.raises(wire.ProtocolError):
        budget.record(1)
    with pytest.raises(wire.ProtocolError):
        budget.record(2)
    with pytest.raises(wire.ProtocolError):
        budget.attribute_frame(2)
    budget = wire.SequenceBudget()
    for _ in range(512):
        budget.record()
    with pytest.raises(wire.ProtocolError):
        budget.record()
    assert budget.records == 512
    budget = wire.SequenceBudget()
    budget.record()
    budget.attribute_frame(7)
    assert budget.frames[6] == 1
    assert budget.records == 1
    with pytest.raises(wire.ProtocolError):
        budget.attribute_frame(8)
    budget = wire.SequenceBudget()
    with pytest.raises(wire.ProtocolError):
        budget.record(frame_id=True)


def test_ready_and_derived_pipe_namespace() -> None:
    """Only seven fixed frame paths and the distinct exact ready profile pass."""
    name = "VerdiClip-moving-edit-" + "a" * 32
    assert wire.frame_pipe_name(name, 7) == name + "-frame-7"
    for bad in (
        "VerdiClip-sequence-" + "a" * 32,
        "VerdiClip-moving-edit-" + "0" * 32,
        "arbitrary-path",
    ):
        with pytest.raises(wire.ProtocolError):
            wire.MovingEditLaunch(
                bad, "b" * 32, 1, 1_000_000_000, 31_000_000_000, "c" * 32, 8, 123
            )
    with pytest.raises(wire.ProtocolError):
        wire.frame_pipe_name(name, 8)
    with pytest.raises(wire.ProtocolError):
        wire.deserialize_launch(cast("Any", b"invalid"))
    ready = {
        "pid": 1,
        "executable": r"C:\Python\python.exe",
        "epoch": "a" * 32,
        "protocol_version": 4,
        "peer_schema": 5,
        "credit_schema": 2,
        "moving_schema": 1,
        "runtime_schema": 6,
        "max_frames": 7,
        "max_requests": 16,
    }
    assert wire.validate_ready(ready) == ready
    for name, changed in (
        ("executable", ""),
        ("executable", "x" * 2049),
        ("peer_schema", 4),
        ("max_frames", 8),
        ("max_requests", 32),
        ("pid", True),
        ("epoch", "F" * 32),
    ):
        with pytest.raises(wire.ProtocolError):
            wire.validate_ready(dict(ready, **{name: changed}))


def test_begin_reply_preserves_originals_and_receive_deadline() -> None:
    """A still-bounded but widened acknowledgment must not replace original stamps."""
    reply = {
        "operation": "moving_begun",
        "epoch": "a" * 32,
        "sequence_started_ns": 0,
        "sequence_deadline_ns": 29_000_000_000,
        "baseline_identity_sha256": "b" * 64,
    }
    assert (
        wire.validate_begin_reply(
            reply,
            sequence_started_ns=0,
            sequence_deadline_ns=29_000_000_000,
            now_ns=28_000_000_000,
        )
        == reply
    )
    with pytest.raises(wire.ProtocolError):
        wire.validate_begin_reply(
            dict(reply, sequence_deadline_ns=30_000_000_000),
            sequence_deadline_ns=29_000_000_000,
        )
    with pytest.raises(wire.ProtocolError):
        wire.validate_begin_reply(reply, sequence_started_ns=1)
    with pytest.raises(wire.ProtocolError):
        wire.validate_begin_reply(reply, now_ns=29_000_000_000)
    with pytest.raises(wire.ProtocolError):
        wire.validate_begin_reply(dict(reply, sequence_deadline_ns=30_000_000_001))


def test_move_and_frame_requests_require_exact_prequeue_phase_deadline() -> None:
    """Same-session reserves apply to both requests, never only the native call."""
    move = {
        "operation": "move_next",
        "step_id": 6,
        "requested_ns": 21_000_000_000,
        "deadline_ns": 22_000_000_000,
    }
    assert (
        wire.validate_move_request(
            move, session_deadline_ns=30_000_000_000, now_ns=21_000_000_000
        )
        == move
    )
    frame = {
        "operation": "observe",
        "frame_id": 6,
        "step_id": 6,
        "move_sha256": "a" * 64,
        "requested_ns": 21_000_000_000,
        "deadline_ns": 22_000_000_000,
    }
    assert (
        wire.validate_frame_request(
            frame, session_deadline_ns=30_000_000_000, now_ns=21_500_000_000
        )
        == frame
    )
    for changed in (
        dict(move, deadline_ns=24_000_000_000),
        dict(move, deadline_ns=21_000_000_000),
        dict(move, requested_ns=True),
        dict(move, step_id=8),
    ):
        with pytest.raises(wire.ProtocolError):
            wire.validate_move_request(changed, session_deadline_ns=30_000_000_000)
    with pytest.raises(wire.ProtocolError):
        wire.validate_frame_request(dict(frame, frame_id=5))
    with pytest.raises(wire.ProtocolError):
        wire.validate_frame_request(frame, now_ns=22_000_000_000)
    assert (
        wire.move_digest(make_move_ack())
        == wire.hashlib.sha256(canonical_json(make_move_ack())).hexdigest()
    )


def test_deadline_bad_phase_clock_and_regression_permanently_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Caller validation failures, not just slow clock reads, poison the owner."""
    now = [1]
    monkeypatch.setattr(wire.time, "monotonic_ns", lambda: now[0])
    deadline = wire.SequenceDeadline(30_000_000_000)
    assert deadline.phase_limit(1) == 22_000_000_000
    assert deadline.phase_limit(7) == 30_000_000_000
    with pytest.raises(wire.ProtocolError):
        deadline.frame(0, 3_000_000_000)
    with pytest.raises(wire.ProtocolError):
        deadline.check()
    deadline = wire.SequenceDeadline(30_000_000_000)
    assert deadline.check() == 1
    now[0] = 0
    with pytest.raises(wire.ProtocolError):
        deadline.check()


def test_finish_receipt_is_exact_provisional_metadata() -> None:
    """Only restored frame seven and the independent 0/8/13 assembly can finish."""
    value: dict[str, Any] = {
        "operation": "moving_finished",
        "epoch": "a" * 32,
        "accepted_frames": 7,
        "sequence_sha256": "b" * 64,
        "restoration": {
            "status": "verified",
            "step_id": 7,
            "initial_frame_id": 1,
            "restored_frame_id": 7,
            "state_equal": True,
            "root_equal": True,
            "client_equal": True,
        },
        "assembly": {
            "width": 493,
            "height": 621,
            "byte_count": 918459,
            "sha256": "c" * 64,
            "source_frame_ids": [2, 3, 4],
            "row_provenance_sha256": "d" * 64,
        },
    }
    assert wire.validate_finish_reply(value) == value
    for section, key, bad in (
        ("restoration", "status", "failed"),
        ("restoration", "client_equal", 1),
        ("restoration", "restored_frame_id", 6),
        ("assembly", "source_frame_ids", [1, 2, 3]),
        ("assembly", "byte_count", 918460),
        ("assembly", "sha256", "x" * 64),
    ):
        changed = copy.deepcopy(value)
        changed[section][key] = bad
        with pytest.raises(wire.ProtocolError):
            wire.validate_finish_reply(changed)


def test_proof_helper_roundtrip_and_event_order_failures() -> None:
    """Private proof helpers are strict, and every paired credit event is ordered."""
    proof = make_proof()
    assert wire.parse_frame_proof(proof.canonical) == proof.value
    assert wire.proof_digest(proof.canonical) == proof.digest
    assert wire.MovingFrameProof.parse(proof.canonical) == proof
    for index, value in (
        (0, [1, 1, "wait", 0]),
        (1, [2, 1_200_000_003, "wait", 0]),
        (19, [20, 3_000_000_000, "grant", 9]),
    ):
        changed = proof.value
        changed["credit_events"][index] = value
        with pytest.raises(wire.ProtocolError):
            wire.encode_frame_proof(changed)
    changed = proof.value
    changed["requested_ns"] = 1_100_000_000
    with pytest.raises(wire.ProtocolError):
        wire.encode_frame_proof(changed)


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"{" * 65537,
        b'{"x":"\\uD800"}',
        b'{"x":18446744073709551616}',
        b'{"x":true,"x":false}',
        b'{"x":"a\\\\b\\"c"} ',
    ],
    ids=[
        "empty",
        "oversized",
        "surrogate",
        "integer-overflow",
        "duplicate-key",
        "trailing-space",
    ],
)
def test_canonical_json_bounds_and_lexical_escapes(
    data: bytes, request: pytest.FixtureRequest
) -> None:
    """Malformed encodings cannot bypass scalar size/type gates."""
    assert len(request.node.nodeid) < 200
    with pytest.raises(wire.ProtocolError):
        parse_json(data, 65536)


def test_native_canonical_equivalent_states_are_immutable_and_tightly_bounded() -> None:
    """Returned dictionaries and unknown fields cannot alter immutable evidence."""
    observation = make_observation()
    detached = observation.value
    detached["native_state"]["input_tick"] = 16
    assert observation.value["native_state"]["input_tick"] == 15
    assert EditObservation.parse(observation.canonical) == observation
    changed = observation.value
    changed["native_state"]["extra"] = "x" * 16384
    with pytest.raises(wire.ProtocolError):
        EditObservation.from_value(changed)
    changed = observation.value
    changed["image"]["origin"] = [61, 60]
    with pytest.raises(wire.ProtocolError):
        EditObservation.from_value(changed)
    changed = observation.value
    changed["acquisition"]["finished_ns"] = changed["acquisition"]["started_ns"]
    with pytest.raises(wire.ProtocolError):
        EditObservation.from_value(changed)


def test_same_position_requires_exact_scrollbar_state_not_only_identity() -> None:
    """Allowed moving fields are still immutable when a position is repeated."""
    before, after = make_observation(4), make_observation(5)
    changed = after.value
    changed["native_state"]["scroll"]["thumb"][0] += 1
    mutated = EditObservation.from_value(changed)
    assert (
        mutated.value["baseline_identity_sha256"]
        == before.value["baseline_identity_sha256"]
    )
    with pytest.raises(wire.ProtocolError, match="repeated-state"):
        validate_continuity(before, mutated)


def _invariant_leaf_paths() -> list[tuple[object, ...]]:
    """Enumerate every native/retained invariant leaf, not selected sample fields."""
    observation = make_observation().value
    paths: list[tuple[object, ...]] = []

    def collect(value: object, path: tuple[object, ...]) -> None:
        if type(value) is dict:
            for key, child in value.items():
                collect(child, (*path, key))
        elif type(value) is list:
            for index, child in enumerate(value):
                collect(child, (*path, index))
        else:
            paths.append(path)

    for name in ("native", "font", "text", "input_tick", "monitors", "pattern_fixed"):
        collect(observation["native_state"][name], ("native_state", name))
    for name in ("minimum", "maximum", "page"):
        collect(
            observation["native_state"]["scroll"][name],
            ("native_state", "scroll", name),
        )
    for name in ("pitch", "character_advance"):
        collect(
            observation["native_state"]["text_layout"][name],
            ("native_state", "text_layout", name),
        )
    collect(observation["identity_evidence"], ("identity_evidence",))
    collect(observation["target"], ("target",))
    return paths


@pytest.mark.parametrize("path", _invariant_leaf_paths())
def test_every_nonmoving_leaf_is_frozen(path: tuple[object, ...]) -> None:
    """Every nonmoving field must fail schema or continuity when changed."""
    initial = make_observation()
    value = initial.value
    cursor: Any = value
    for part in path[:-1]:
        cursor = cursor[part]
    prior = cursor[path[-1]]
    if type(prior) is bool:
        changed: object = not prior
    elif type(prior) in (int, float):
        changed = prior + 1
    else:
        changed = prior + "x"
    cursor[path[-1]] = changed
    with pytest.raises(wire.ProtocolError):
        validate_continuity(initial, EditObservation.from_value(value))


@pytest.mark.parametrize(
    "value",
    [
        {1: "invalid-key"},
        {"x": object()},
        {"x": "x" * 65537},
        {"x": float("nan")},
        {"x": 2**65},
    ],
)
def test_serializer_bounds_before_output_allocation(value: object) -> None:
    """Local producer metadata also has exact types and bounded scalar sizes."""
    with pytest.raises(wire.ProtocolError):
        canonical_json(value)


def test_serializer_bounded_depth_and_nodes_before_encoding() -> None:
    """Local nested objects cannot overflow the encoder recursion or output limit."""
    value: Any = 0
    for _ in range(33):
        value = [value]
    with pytest.raises(wire.ProtocolError, match="depth"):
        canonical_json({"x": value})
    with pytest.raises(wire.ProtocolError, match="node"):
        canonical_json({"x": [0] * 4096})


def test_full_profile_evidence_rejects_missing_setup_binding_and_bad_point() -> None:
    """Begin cannot silently strip the owner's caret/font evidence."""
    for field, changed in (("font_attestation", []), ("target_setup", {})):
        begin = make_begin()
        begin["profile_evidence"][field] = changed
        with pytest.raises(wire.ProtocolError):
            wire.validate_begin_payload(begin)
    begin = make_begin()
    begin["profile_evidence"]["font_attestation"]["pid"] = 98
    with pytest.raises(wire.ProtocolError):
        wire.validate_begin_payload(begin)
    begin = make_begin()
    begin["target"]["point"] = [1000, 1000]
    with pytest.raises(wire.ProtocolError):
        wire.validate_begin_payload(begin)


def test_remaining_snapshot_shape_guards() -> None:
    """Missing source shapes, invalid monitors and native evidence fail closed."""
    for path, changed in [
        (("native", "client_local"), [0, 0, 0, 0]),
        (("native", "root"), 101),
        (("font", "attestation"), None),
        (("monitors",), [[1, 0, 0, 10, 10, True]]),
        (("text_layout",), []),
        (("text_layout", "first_visible_line"), 2),
        (("scroll_pattern", 0), 0),
    ]:
        state, _ = producer_state(3)
        cursor: Any = state
        for part in path[:-1]:
            cursor = cursor[part]
        cursor[path[-1]] = changed
        with pytest.raises(wire.ProtocolError):
            validate_native_state(state, 3)
    for key, rect in (
        ("root_structure", [0, 0, float("nan"), 1]),
        ("panel_structure", [1, 1, 1, 1]),
    ):
        value = make_observation().value
        value["identity_evidence"][key]["rect"] = rect
        with pytest.raises(wire.ProtocolError):
            EditObservation.from_value(value)


def test_accept_rejects_wrong_retained_header_and_bad_chain_type() -> None:
    """Summary hashes must match this peer's retained stream, not another frame."""
    proof = make_proof()
    accepted = {
        "epoch": proof.value["epoch"],
        "frame_id": 1,
        "step_id": 1,
        "proof_sha256": proof.digest.hex(),
        "sequence_sha256": wire.hashlib.sha256(bytes(32) + proof.digest).hexdigest(),
        "proof": proof.value,
    }
    with pytest.raises(wire.ProtocolError):
        wire.validate_frame_accept(accepted, previous_chain=b"short")
    other = make_proof(2)
    other_header = wire.FrameHeader(
        other.observation.frame_identity(), bytes.fromhex(other.value["image_sha256"])
    )
    with pytest.raises(wire.ProtocolError):
        wire.validate_frame_accept(accepted, expected_header=other_header)
