"""Actual sequence producers through independent fixture validators; no native I/O."""

import hashlib
import json
import struct
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Any
from unittest.mock import Mock

import pytest
from scripts import sequence_observe_contract as contract
from tests.capture.test_sequence_retention import EPOCH, RGB, frame
from tests.platform import test_sequence_channel as helpers
from tests.platform.test_helper_channel import helper_status

from verdiclip.capture import sequence_credit
from verdiclip.capture.sequence_credit import CreditToken, ParentCredit
from verdiclip.capture.sequence_retention import SequenceCollector
from verdiclip.platform import sequence_channel as channel
from verdiclip.platform.sequence_protocol import Message, SequenceFrameProof, encode

state_worker = helpers.state_worker


def produce(monkeypatch: pytest.MonkeyPatch, index: int) -> tuple[Any, ...]:
    """Produce."""
    candidate, base = frame(index)
    now = 1_000_000_000 + (index - 1) * 500_000_000
    monkeypatch.setattr(sequence_credit, "monotonic_ns", lambda: now)
    credit = ParentCredit(EPOCH, index + 1, index, len(RGB))
    transcript = contract.CreditTranscript(1, EPOCH, len(RGB), index)
    token = CreditToken.for_chunk(EPOCH, index + 1, index, len(RGB), 0)
    now += 100_000_000
    credit.receive_wait(token)
    identity = transcript.record_wait(
        1, encode(Message("frame_wait", index + 1, token.payload())), now
    )
    credit.reserve_grant(token)
    transcript.record_grant(identity, now, now)
    credit.accept_body(len(RGB))
    credit.finish()
    value = base.value
    value.update(
        requested_ns=now - 100_000_000,
        verified_ns=now + 100_000_000,
        deadline_ns=now + 1_000_000_000,
        credit_events=channel._credit_snapshot(credit.snapshot())["events"],
    )
    return candidate, SequenceFrameProof(contract.canonical(value)), credit, transcript


@pytest.fixture
def produced(
    state_worker: channel._Worker, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, ...]:
    """Produced."""
    worker = state_worker
    collector = SequenceCollector(EPOCH, lambda: None)
    sources = [produce(monkeypatch, i) for i in (1, 2)]
    for candidate, proof, _, _ in sources:
        collector.reserve(candidate.header.identity)
        collector.accept(candidate, proof)
        collector.commit(proof.digest)
    worker.sequence_candidate = collector.finish()
    worker.proofs = [proof.digest.hex() for _, proof, _, _ in sources]
    worker.credits = sources[-1][2]
    worker.frame_id, worker.frame_bytes = 2, len(RGB)
    worker.finish_requested = worker.observe_consumed = True
    worker.eof = worker.frame_eof = worker.launch_returned = True
    worker.helper = Mock(status=helper_status(retired=True, exit_code=0))
    worker.phase, worker.deadline = "retiring", 31.0
    worker.vault.begin(EPOCH) if worker.vault.stamp() else None
    if worker.vault.stamp() is None:
        worker.vault.publish_stamp("ready", 31.0)
        worker.vault.begin(EPOCH)
    worker.vault.publish_stamp("retiring", 31.0)
    receipts = []
    worker.terminal.connect(lambda _, raw: receipts.append(raw))
    worker._terminal_impl()
    assert json.loads(receipts[0])["passed"] is True
    worker.vault.authorize(receipts[0])
    sequence = worker.vault.take(1, receipts[0])
    target = sources[0][1].observation.target.value()
    args = [
        sequence,
        RGB,
        receipts[0],
        (-2, 1, 2, 4),
        contract.canonical(target),
        123,
        tuple(x[3] for x in sources),
        tuple(worker.proofs),
    ]
    return args, worker, sources


def timing(*, cancelled: bool = False) -> dict[str, Any]:
    """Freeze the independent intervals used by the actual deterministic producers."""
    second = 1_350_000_000 if cancelled else 1_500_000_000
    return {
        "public_calls": contract.canonical(
            {
                "calls": [
                    {
                        "frame_id": 1,
                        "called_ns": 1_000_000_000,
                        "returned_ns": 1_000_000_000,
                    },
                    {"frame_id": 2, "called_ns": second, "returned_ns": second},
                ]
            }
        ),
        "case_deadline_ns": 4_000_000_000,
        "sequence_deadline_ns": 4_000_000_000,
    }


def compare(args: list[Any], bounds: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compare."""
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(
            contract.compare_retained_sequence,
            *args,
            pid=7,
            **(timing() if bounds is None else bounds),
        ).result()


def test_actual_collector_worker_terminal_success(produced: tuple[Any, ...]) -> None:
    """Test actual collector worker terminal success."""
    args, worker, _ = produced
    assert compare(args)["full_rgb_exact"] is True
    with pytest.raises(ValueError, match=r".+"):
        worker.vault.take(1, args[2])
    with pytest.raises(ValueError, match=r".+"):
        contract.compare_retained_sequence(*args, pid=7, **timing())


@pytest.mark.parametrize(
    "fault",
    [
        "rgb",
        "swap",
        "receipt",
        "proof_order",
        "target",
        "input",
        "cleanup",
        "bool",
        "deadline",
        "event_order",
    ],
)
def test_mutations_reject(produced: tuple[Any, ...], fault: str) -> None:
    """Test mutations reject."""
    args, _, _ = produced
    if fault == "rgb":
        args[1] = bytes(len(RGB))
    elif fault == "swap":
        args[0] = replace(args[0], frames=args[0].frames[::-1])
    elif fault == "receipt":
        args[2] += b" "
    elif fault == "proof_order":
        args[7] = args[7][::-1]
    elif fault == "target":
        target = json.loads(args[4])
        target["hwnd"] = 999
        args[4] = contract.canonical(target)
    elif fault == "input":
        args[5] = True
    elif fault == "cleanup":
        receipt = json.loads(args[2])
        receipt["helper"]["closed"] = False
        args[2] = contract.canonical(receipt)
        args[0] = replace(args[0], receipt_sha256=hashlib.sha256(args[2]).digest())
    else:
        proof = json.loads(args[0].proofs[0])
        if fault == "bool":
            proof["frame_id"] = True
        elif fault == "deadline":
            proof["verified_ns"] = proof["deadline_ns"]
        else:
            proof["credit_events"].reverse()
        args[0] = replace(
            args[0], proofs=(contract.canonical(proof), args[0].proofs[1])
        )
    with pytest.raises(ValueError, match=r".+"):
        compare(args)


@pytest.mark.parametrize(
    "fault", ["partial", "trailing", "stale", "bool", "oversize", "request"]
)
def test_complete_credit_wire_rejects(fault: str) -> None:
    """Test complete credit wire rejects."""
    token = CreditToken.for_chunk(EPOCH, 3, 2, len(RGB), 0)
    value = {
        "version": 3,
        "kind": "frame_wait",
        "request_id": 3,
        "payload": token.payload(),
    }
    if fault == "stale":
        value["payload"]["epoch"] = "b" * 32
    if fault == "bool":
        value["payload"]["frame_id"] = True
    if fault == "request":
        value["request_id"] = 2
    raw = contract.canonical(value)

    raw = struct.pack(">I", len(raw)) + raw
    if fault == "partial":
        raw = raw[:-1]
    if fault == "trailing":
        raw += b"x"
    if fault == "oversize":
        raw += b" " * 512
    with pytest.raises(ValueError, match=r".+"):
        contract.parse_credit_token(
            1,
            raw,
            expected_generation=1,
            epoch=EPOCH,
            size=len(RGB),
            frame_id=2,
            chunk_index=0,
        )


def cancellation(
    state_worker: channel._Worker, monkeypatch: pytest.MonkeyPatch
) -> list[Any]:
    """Cancellation."""
    _, proof, _, transcript = produce(monkeypatch, 1)
    worker = state_worker
    now = 1_400_000_000
    monkeypatch.setattr(sequence_credit, "monotonic_ns", lambda: now)
    credit = ParentCredit(EPOCH, 3, 2, len(RGB), gate=worker.credit_gate)
    token = CreditToken.for_chunk(EPOCH, 3, 2, len(RGB), 0)
    credit.receive_wait(token)
    held = contract.CreditTranscript(1, EPOCH, len(RGB), 2)
    held.record_wait(1, encode(Message("frame_wait", 3, token.payload())), now)
    hold = {
        "token": held.events[0]["token"],
        "wait_received_ns": now,
        "cancel_called_ns": now,
        "cancel_returned_ns": now,
    }
    worker.credit_gate.cancel()
    credit.observe_cancel()
    worker.credits = credit
    worker.proofs = [proof.digest.hex()]
    worker.frame_id, worker.frame_bytes, worker.observe_deadline_ns = (
        2,
        0,
        3_000_000_000,
    )
    worker.failure = "cancelled"
    worker.launch_returned = worker.observe_consumed = True
    worker.helper = Mock(
        status=replace(
            helper_status(retired=True, exit_code=1), termination_requested=True
        )
    )
    receipts = []
    worker.terminal.connect(lambda _, raw: receipts.append(raw))
    worker._terminal_impl()
    return [
        receipts[0],
        {"epoch": EPOCH, "pid": 7},
        transcript,
        proof.digest.hex(),
        1_300_000_000,
        hold,
    ]


def test_actual_cancel_worker_witness(
    state_worker: channel._Worker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test actual cancel worker witness."""
    args = cancellation(state_worker, monkeypatch)
    assert (
        contract.validate_cancel(*args, **timing(cancelled=True))["no_held_grant"]
        is True
    )
    with pytest.raises(ValueError, match=r".+"):
        state_worker.vault.take(1, args[0])


@pytest.mark.parametrize(
    "fault", ["grant", "chain", "history", "deadline", "exit", "token", "order"]
)
def test_cancel_mutations(
    state_worker: channel._Worker, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """Test cancel mutations."""
    args = cancellation(state_worker, monkeypatch)
    receipt = json.loads(args[0])
    if fault == "grant":
        receipt["sequence"]["credit"]["granted_bytes"] = 1
    elif fault == "chain":
        receipt["sequence"]["sequence_sha256"] = "a" * 64
    elif fault == "history":
        receipt["sequence"]["proof_sha256"] = []
    elif fault == "deadline":
        args[-1]["cancel_returned_ns"] = receipt["sequence"]["deadline_ns"]
    elif fault == "exit":
        receipt["helper"]["exit_code"] = 0
    elif fault == "token":
        args[-1]["token"]["frame_id"] = 1
    else:
        receipt["sequence"]["credit_cancellation"]["final"]["events"].reverse()
    args[0] = contract.canonical(receipt)
    with pytest.raises(ValueError, match=r".+"):
        contract.validate_cancel(*args, **timing(cancelled=True))


@pytest.mark.parametrize("runtime", [True, [], [True], [2**31], [1] * 33, "42"])
def test_independent_runtime_id_shape(runtime: object) -> None:
    """Reject malformed runtime IDs without a production parse validator."""
    _, proof = frame(1)
    actual = proof.value["observation"]["observation"]["target"]
    actual["runtime_id"] = runtime
    with pytest.raises(ValueError, match=r".+"):
        contract._validate_target(actual, proof.observation.target.value())


def test_full_panel_credit_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise every public chunk for the actual 510 by 430 fixture size."""
    size = 510 * 430 * 3
    transcript = contract.CreditTranscript(17, EPOCH, size, 2)
    credit = ParentCredit(EPOCH, 3, 2, size)
    now = 1000
    monkeypatch.setattr(sequence_credit, "monotonic_ns", lambda: now)
    for index in range((size + 65535) // 65536):
        token = CreditToken.for_chunk(EPOCH, 3, 2, size, index)
        credit.receive_wait(token)
        identity = transcript.record_wait(
            17, encode(Message("frame_wait", 3, token.payload())), now
        )
        credit.reserve_grant(token)
        transcript.record_grant(identity, now, now)
        credit.accept_body(token.count)
        now += 1
    credit.finish()
    _, original = frame(2)
    proof = original.value
    proof.update(
        requested_ns=999,
        deadline_ns=2000,
        verified_ns=now,
        bytes=size,
        granted_bytes=size,
        accepted_bytes=size,
        credit_chunks=len(transcript.events),
        credit_events=channel._credit_snapshot(credit.snapshot())["events"],
    )
    assert transcript.verify(proof)["bytes"] == size
    assert transcript.events[-1]["token"]["count"] == size % 65536
    with pytest.raises(ValueError, match=r".+"):
        transcript.record_wait(
            17, encode(Message("frame_wait", 3, token.payload())), now
        )


@pytest.mark.parametrize(
    "fault", ["shifted", "bool", "reordered", "case_cap", "sequence_cap"]
)
def test_public_success_deadline_binding(produced: tuple[Any, ...], fault: str) -> None:
    """Actual retained producer proof must fit the original public call and caps."""
    args, _, _ = produced
    bounds = timing()
    calls = json.loads(bounds["public_calls"])
    if fault == "shifted":
        calls["calls"][0]["called_ns"] += 1
        calls["calls"][0]["returned_ns"] += 1
    elif fault == "bool":
        calls["calls"][0]["called_ns"] = True
    elif fault == "reordered":
        calls["calls"].reverse()
    elif fault == "case_cap":
        bounds["case_deadline_ns"] = 2_500_000_000
    else:
        bounds["sequence_deadline_ns"] = 2_500_000_000
    bounds["public_calls"] = contract.canonical(calls)
    with pytest.raises(ValueError, match=r".+"):
        compare(args, bounds)


@pytest.mark.parametrize(
    "fault", ["inflated", "case_cap", "sequence_cap", "conservative_boundary"]
)
def test_cancel_original_public_window(
    state_worker: channel._Worker, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """Cancellation never extends its window to a terminal-supplied later deadline."""
    args = cancellation(state_worker, monkeypatch)
    bounds = timing(cancelled=True)
    receipt = json.loads(args[0])
    if fault == "inflated":
        receipt["sequence"]["deadline_ns"] = 3_350_000_001
    elif fault == "case_cap":
        bounds["case_deadline_ns"] = 2_999_999_999
    elif fault == "sequence_cap":
        bounds["sequence_deadline_ns"] = 2_999_999_999
    else:
        calls = json.loads(bounds["public_calls"])
        calls["calls"][1]["returned_ns"] += 10_000_000
        bounds["public_calls"] = contract.canonical(calls)
        receipt["sequence"]["deadline_ns"] = 3_360_000_000
        for snapshot in (
            receipt["sequence"]["credit"],
            receipt["sequence"]["credit_cancellation"]["latched"],
            receipt["sequence"]["credit_cancellation"]["final"],
        ):
            snapshot["cancel_time_ns"] = 3_350_000_000
            snapshot["events"][1][1] = 3_350_000_000
            if snapshot["observed_sequence"]:
                snapshot["observed_time_ns"] = 3_350_000_000
                snapshot["events"][2][1] = 3_350_000_000
        args[-1]["cancel_called_ns"] = args[-1]["cancel_returned_ns"] = 3_350_000_000
    args[0] = contract.canonical(receipt)
    with pytest.raises(ValueError, match=r".+"):
        contract.validate_cancel(*args, **bounds)
