"""Independent success-only credit harness proof, without native transport."""

from __future__ import annotations

import copy
import json
import struct
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from scripts import installed_observe_smoke as smoke

from verdiclip.capture.frame_credit import CreditGate, CreditToken, ParentCredit
from verdiclip.platform.helper_channel import _credit_snapshot
from verdiclip.platform.helper_protocol import Message, encode

EPOCH = "ab" * 16


@pytest.fixture
def journal(tmp_path: Path) -> Iterator[smoke.Journal]:
    """Use the actual bounded call log with a fixed existing case deadline."""
    value = smoke.Journal(tmp_path / "calls.jsonl", time.monotonic() + 15)
    yield value
    value.stream.close()


def drive(
    journal: smoke.Journal, size: int = 657900
) -> tuple[smoke.CreditTranscript, dict[str, Any]]:
    """Feed real production token encoding and worker accounting to the oracle."""
    transfer = ParentCredit(EPOCH, 1, size, gate=CreditGate(threading.Event()))
    transcript = smoke.CreditTranscript(1, EPOCH, size)
    facade = Mock()
    for index in range(transcript.count):
        token = CreditToken.for_chunk(EPOCH, 1, size, index)
        transfer.receive_wait(token)

        def granted(
            generation: int,
            request_id: int,
            chunk_index: int,
            *,
            expected_index: int = index,
            expected_token: CreditToken = token,
        ) -> None:
            assert (generation, request_id, chunk_index) == (1, 1, expected_index)
            transfer.reserve_grant(expected_token)
            transfer.accept_body(expected_token.count)

        facade.grant_frame_credit.side_effect = granted
        transcript.grant(
            1, encode(Message("frame_wait", 1, token.payload())), facade, journal
        )
    transfer.finish()
    finished = time.monotonic_ns()
    return transcript, {
        "passed": True,
        "observe": {
            "observation": {"epoch": EPOCH, "request_id": 1, "frame_id": 1},
            "credit": _credit_snapshot(transfer.snapshot()),
            "credit_cancellation": None,
            "finished_ns": finished,
            "deadline_ns": finished + 1_000_000,
            "transfer_messages": 4 + 2 * transcript.count,
        },
    }


@pytest.mark.parametrize("size", [3, 65536, 65537, 657900, 1572864])
def test_real_token_and_counter_transcript_passes_exact_sequence(
    journal: smoke.Journal, size: int
) -> None:
    """First, full, tail and maximum frames all preserve exact one-credit order."""
    transcript, receipt = drive(journal, size)
    proof = transcript.verify(receipt)
    assert proof["bytes"] == size
    assert proof["chunks"] == (size + 65535) // 65536
    assert proof["exact_sequence"] is True
    assert len(proof["public_events"]) <= 24


@pytest.mark.parametrize(
    "fault",
    [
        "version",
        "epoch",
        "count",
        "index",
        "bool",
        "extra",
        "prefix",
        "oversized",
        "duplicate-key",
    ],
)
def test_invalid_public_event_never_grants_and_latches(
    journal: smoke.Journal, fault: str
) -> None:
    """Independent oracle cannot turn malformed events into public grant calls."""
    transcript = smoke.CreditTranscript(1, EPOCH, 36)
    token = CreditToken.for_chunk(EPOCH, 1, 36, 0)
    message = json.loads(encode(Message("frame_wait", 1, token.payload()))[4:])
    if fault == "version":
        message["version"] = 1
    elif fault == "epoch":
        message["payload"]["epoch"] = "cd" * 16
    elif fault == "bool":
        message["request_id"] = True
    elif fault == "extra":
        message["extra"] = 1
    elif fault in ("count", "index"):
        message["payload"]["count" if fault == "count" else "chunk_index"] += 1
    body = json.dumps(message).encode()
    if fault == "oversized":
        body += b" " * 512
    elif fault == "duplicate-key":
        body = body.replace(b'"version": 2', b'"version": 2,"version": 2')
    raw = struct.pack(">I", len(body) + (fault == "prefix")) + body
    facade = Mock()
    with pytest.raises(ValueError, match=r".+"):
        transcript.grant(1, raw, facade, journal)
    facade.grant_frame_credit.assert_not_called()
    assert transcript.failed
    with pytest.raises(ValueError, match=r".+"):
        transcript.grant(
            1, encode(Message("frame_wait", 1, token.payload())), facade, journal
        )


@pytest.mark.parametrize(
    "fault",
    [
        "accepted",
        "granted",
        "pending",
        "failed",
        "cancel",
        "events",
        "sequence",
        "time",
        "deadline",
        "surplus-count",
        "public-return",
        "boolean-counter",
    ],
)
def test_terminal_transcript_rejects_forged_worker_or_public_evidence(  # noqa: C901 - independent negative cases
    journal: smoke.Journal, fault: str
) -> None:
    """Success requires both independently observed grants and exact worker totals."""
    transcript, receipt = drive(journal)
    value = copy.deepcopy(receipt)
    observe = value["observe"]
    state = observe["credit"]
    if fault in ("accepted", "granted"):
        state[f"{fault}_bytes"] -= 1
    elif fault == "pending":
        state["pending"] = {}
    elif fault == "failed":
        state["failed"] = True
    elif fault == "cancel":
        observe["credit_cancellation"] = {}
    elif fault == "events":
        state["events"].pop()
    elif fault == "sequence":
        state["events"][1][0] = 1
    elif fault == "time":
        state["events"][1][1] = 0
    elif fault == "deadline":
        observe["deadline_ns"] = observe["finished_ns"]
    elif fault == "surplus-count":
        observe["transfer_messages"] += 1
    elif fault == "boolean-counter":
        state["cancel_sequence"] = False
    else:
        transcript.events[0]["grant_returned_ns"] = None
    with pytest.raises(ValueError, match=r".+"):
        transcript.verify(value)


def test_public_grant_exception_keeps_partial_event_and_never_retries(
    journal: smoke.Journal,
) -> None:
    """The first uncertain public operation remains failure with bounded evidence."""
    transcript = smoke.CreditTranscript(1, EPOCH, 36)
    facade = Mock()
    facade.grant_frame_credit.side_effect = RuntimeError("grant failed")
    token = CreditToken.for_chunk(EPOCH, 1, 36, 0)
    with pytest.raises(RuntimeError, match="grant failed"):
        transcript.grant(
            1, encode(Message("frame_wait", 1, token.payload())), facade, journal
        )
    assert transcript.failed
    assert transcript.events[0]["grant_returned_ns"] is None
    facade.grant_frame_credit.assert_called_once_with(1, 1, 0)


@pytest.mark.parametrize(
    ("field", "value"), [("epoch", "cd" * 16), ("request_id", True), ("frame_id", 2)]
)
def test_terminal_credit_identity_cannot_change(
    journal: smoke.Journal, field: str, value: object
) -> None:
    """Worker event counts alone do not attest a different session or frame."""
    transcript, receipt = drive(journal)
    receipt["observe"]["observation"][field] = value
    with pytest.raises(ValueError, match="identity differs"):
        transcript.verify(receipt)
