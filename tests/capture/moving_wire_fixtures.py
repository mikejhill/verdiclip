"""Moving wire test factories fed by the extracted actual snapshot producer."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest
from tests.capture.test_moving_edit_native_extraction import Harness

from verdiclip.capture.edit_observation import EditObservation, baseline_identity_sha256
from verdiclip.capture.edit_scroll_contract import FIXTURE_CONTRACT, FRAME_LINES
from verdiclip.capture.frame_envelope import FrameHeader
from verdiclip.platform.moving_edit_protocol import (
    MovingFrameProof,
    canonical_json,
    encode_frame_proof,
)

WIRE_EPOCH = "a" * 32


def producer_state(first: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the actual native producer with SDK-buffer fakes, never edited snapshots."""
    with pytest.MonkeyPatch.context() as monkeypatch:
        producer = Harness(monkeypatch)
        producer.first = first
        state = producer.native.snapshot()
        identity = {
            "root_runtime_id": list(producer.native.root_id),
            "panel_runtime_id": list(producer.native.panel_id),
            "root_structure": producer.native.root_baseline,
            "panel_structure": producer.native.panel_baseline,
        }
    return state, identity


def make_observation(
    frame_id: int = 1, image_sha256: str = "a" * 64, move_sha256: str = "b" * 64
) -> EditObservation:
    """Freeze real produced state in the new wire schema with independent epochs."""
    state, identity = producer_state(FRAME_LINES[frame_id - 1])
    requested = frame_id * 1_000_000_000
    return EditObservation.from_value(
        {
            "schema": 1,
            "target": {
                "pid": 99,
                "process_created_100ns": 12345,
                "hwnd": 100,
                "point": [80, 80],
            },
            "profile": "owned-edit-moving-v1",
            "epoch": WIRE_EPOCH,
            "request_id": frame_id * 2 + 1,
            "frame_id": frame_id,
            "step_id": frame_id,
            "move_sha256": move_sha256,
            "baseline_identity_sha256": baseline_identity_sha256(state, identity),
            "native_state": state,
            "identity_evidence": identity,
            "acquisition": {
                "started_ns": requested + 1,
                "finished_ns": requested + 200_000_001,
                "samples": 3,
                "quiet_ns": 200_000_000,
            },
            "image": {
                "origin": [60, 60],
                "width": 493,
                "height": 413,
                "stride": 1479,
                "byte_count": 610827,
                "sha256": image_sha256,
            },
        }
    )


def make_proof(
    frame_id: int = 1, image_sha256: str = "a" * 64, move_sha256: str = "b" * 64
) -> MovingFrameProof:
    """Construct the same complete proof a parent must validate before acceptance."""
    observation = make_observation(frame_id, image_sha256, move_sha256)
    value = observation.value
    requested = frame_id * 1_000_000_000
    header = FrameHeader(observation.frame_identity(), bytes.fromhex(image_sha256))
    proof = {
        "proof_schema": 1,
        "epoch": WIRE_EPOCH,
        "request_id": frame_id * 2 + 1,
        "frame_id": frame_id,
        "step_id": frame_id,
        "move_sha256": move_sha256,
        "baseline_identity_sha256": value["baseline_identity_sha256"],
        "requested_ns": requested,
        "deadline_ns": requested + 2_000_000_000,
        "verified_ns": requested + 300_000_000,
        "header_sha256": hashlib.sha256(header.encode()).hexdigest(),
        "observation_sha256": observation.digest.hex(),
        "image_sha256": image_sha256,
        "bytes": 610827,
        "credit_chunks": 10,
        "granted_bytes": 610827,
        "accepted_bytes": 610827,
        "credit_events": [
            [
                i + 1,
                requested + 200_000_002 + i,
                "wait" if i % 2 == 0 else "grant",
                i // 2,
            ]
            for i in range(20)
        ],
        "observation": value,
    }
    return MovingFrameProof(encode_frame_proof(proof))


def make_begin() -> dict[str, Any]:
    """Bind the actual producer attestation and exact focused-owner setup receipt."""
    state, _ = producer_state(0)
    font = state["font"]["attestation"]
    setup = {
        "schema": "owned-edit-scroll-receipt-v1",
        "fixture_contract": FIXTURE_CONTRACT,
        "session_id": font["epoch"],
        "sequence": 1,
        "action": "switch-to-target",
        "status": "completed",
        "fixture_pid": 99,
        "creation": 12345,
        "root": 100,
        "child": 101,
        "phase": "target",
        "foreground": 100,
        "focus": 100,
        "before_input_tick": 15,
        "after_input_tick": 15,
        "completed_monotonic": 0.5,
        "target_setup": {
            "schema": "owned-edit-focused-hidden-caret-v1",
            "prior_focus": 100,
            "focus": 101,
            "caret": 101,
            "active": 100,
            "flags": 0,
            "capture": 0,
            "menu_owner": 0,
            "move_size": 0,
            "hide_caret": True,
            "before_input_tick": 15,
            "after_input_tick": 15,
        },
    }
    return {
        "operation": "moving_begin",
        "target": {
            "pid": 99,
            "process_created_100ns": 12345,
            "hwnd": 100,
            "point": [80, 80],
        },
        "profile_evidence": {
            "fixture_contract": FIXTURE_CONTRACT,
            "font_attestation": font,
            "target_setup": setup,
        },
        "sequence_started_ns": 0,
        "sequence_deadline_ns": 30_000_000_000,
        "command_started_ns": 0,
        "command_deadline_ns": 2_000_000_000,
    }


def make_move_ack(step_id: int = 1) -> dict[str, Any]:
    """Use actual snapshot output with complete, ordered write/readback timestamps."""
    state, identity = producer_state(FRAME_LINES[step_id - 1])
    requested = step_id * 1_000_000_000
    return {
        "operation": "move_completed",
        "epoch": WIRE_EPOCH,
        "request_id": 2 * step_id,
        "step_id": step_id,
        "expected_line": FRAME_LINES[step_id - 1],
        "requested_ns": requested,
        "deadline_ns": requested + 3_000_000_000,
        "write_started_ns": requested + 1,
        "write_returned_ns": requested + 2,
        "readback_ns": requested + 3,
        "completed_ns": requested + 4,
        "restoring": step_id == 7,
        "native_state": state,
        "native_state_sha256": hashlib.sha256(canonical_json(state)).hexdigest(),
        "baseline_identity_sha256": baseline_identity_sha256(state, identity),
    }
