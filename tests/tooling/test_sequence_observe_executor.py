"""Fixed executor callbacks and consumer disposal; no native launch or sockets."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from pytestqt.qtbot import QtBot
from scripts import sequence_observe_executor as executor
from tests.capture.test_sequence_retention import EPOCH, RGB, frame
from tests.tooling import test_sequence_observe_contract as contracts

from verdiclip.capture.retained_frame import DeadlineStamp
from verdiclip.capture.sequence_credit import CreditToken
from verdiclip.platform.sequence_channel import SequenceChannel
from verdiclip.platform.sequence_peer import SequenceLaunch, SequencePeerProtocol
from verdiclip.platform.sequence_protocol import Message, encode

state_worker = contracts.state_worker
produced = contracts.produced


@pytest.fixture
def journal(tmp_path: Path) -> Generator[executor.Journal]:
    """Use the actual bounded flush-before-call journal."""
    value = executor.Journal(tmp_path / "calls.jsonl", time.monotonic() + 15)
    try:
        yield value
    finally:
        value.stream.close()


def ready_controller(
    _qtbot: QtBot, journal: executor.Journal, case: str = "success"
) -> tuple[SequenceChannel, executor.SequenceController, list[Any], list[Any]]:
    """Use real facade request, credit, reply and cancellation signal transitions."""
    facade = SequenceChannel()
    facade._state = "ready"
    facade._generation = 1
    facade._credit_epoch = EPOCH
    facade._vault = Mock()
    facade._vault.stamp.return_value = DeadlineStamp("ready", 1, time.monotonic() + 15)
    controller = executor.SequenceController(facade, journal, 1, EPOCH, len(RGB), case)
    facade.reply.connect(controller.reply)
    facade.credit_required.connect(controller.credit)
    requests = []
    grants = []
    facade._request.connect(
        lambda caller, raw: requests.append((caller, json.loads(raw)))
    )
    facade._credit.connect(lambda generation, raw: grants.append((generation, raw)))
    _, proof = frame(1)
    controller.begin(proof.observation.target)
    peer = SequencePeerProtocol(
        SequenceLaunch("VerdiClip-sequence-" + "b" * 32, EPOCH, 7)
    )
    response = peer.begin(
        Message(
            "request",
            1,
            {
                **requests[-1][1],
                "sequence_schema": 1,
                "epoch": EPOCH,
                "max_frames": 8,
                "deadline_ns": time.monotonic_ns() + 10_000_000_000,
            },
        )
    )
    facade._reply(1, 1, executor.contract.canonical(response.payload))
    assert requests[-1][0] == 2
    return facade, controller, requests, grants


def accept(
    facade: SequenceChannel, controller: executor.SequenceController, index: int
) -> None:
    """Deliver actual proof digests in the worker's exact acceptance wire schema."""
    _, proof = frame(index)
    chain = hashlib.sha256(controller.chain + proof.digest).hexdigest()
    facade._reply(
        1,
        index + 1,
        executor.contract.canonical(
            {
                "operation": "frame_accepted",
                "frame_id": index,
                "proof_sha256": proof.digest.hex(),
                "sequence_sha256": chain,
            }
        ),
    )


def wait(facade: SequenceChannel, index: int) -> None:
    """Emit actual VERSION3 credit2 token through the public facade gate."""
    token = CreditToken.for_chunk(EPOCH, index + 1, index, len(RGB), 0)
    facade._credit_required(
        1, encode(Message("frame_wait", index + 1, token.payload()))
    )


def test_actual_facade_two_frames(qtbot: QtBot, journal: executor.Journal) -> None:
    """No second request before frame1 acceptance; finish only after frame2."""
    facade, controller, requests, grants = ready_controller(qtbot, journal)
    assert controller.phase == "frame"
    wait(facade, 1)
    assert len(grants) == 1
    assert len(requests) == 2
    accept(facade, controller, 1)
    assert requests[-1][0] == 3
    assert requests[-1][1]["frame_id"] == 2
    wait(facade, 2)
    accept(facade, controller, 2)
    assert len(grants) == 2
    assert facade._state == "closing"
    controller.terminal()
    assert controller.phase == "terminal"
    facade.deleteLater()


def test_actual_facade_immediate_frame2_cancel(
    qtbot: QtBot, journal: executor.Journal
) -> None:
    """Frame2 wait invokes public cancel synchronously and never emits credit."""
    facade, controller, _, grants = ready_controller(qtbot, journal, "cancel-frame2")
    wait(facade, 1)
    accept(facade, controller, 1)
    wait(facade, 2)
    assert len(grants) == 1
    assert facade._cancelled.is_set()
    assert facade._pending_credit is None
    assert controller.phase == "cancelling"
    assert controller.hold is not None
    assert controller.hold["cancel_returned_ns"] >= controller.hold["cancel_called_ns"]
    assert "grant_called_ns" not in controller.transcripts[1].events[0]
    controller.terminal()
    facade.deleteLater()


@pytest.mark.parametrize(
    "fault", ["stale", "caller", "bool", "unknown", "early", "chain"]
)
def test_reply_mutations(qtbot: QtBot, journal: executor.Journal, fault: str) -> None:
    """Reject malformed callbacks without issuing the next observe."""
    facade, controller, requests, _ = ready_controller(qtbot, journal)
    _, proof = frame(1)
    payload = {
        "operation": "frame_accepted",
        "frame_id": 1,
        "proof_sha256": proof.digest.hex(),
        "sequence_sha256": hashlib.sha256(bytes(32) + proof.digest).hexdigest(),
    }
    if fault != "early":
        wait(facade, 1)
    if fault == "bool":
        payload["frame_id"] = True
    if fault == "unknown":
        payload["extra"] = 1
    if fault == "chain":
        payload["sequence_sha256"] = "f" * 64
    with pytest.raises(ValueError, match=r".+"):
        controller.reply(
            2 if fault == "stale" else 1,
            3 if fault == "caller" else 2,
            executor.contract.canonical(payload),
        )
    assert len(requests) == 2
    facade.deleteLater()


def test_exact_case_deadline_precedes_action(
    qtbot: QtBot, journal: executor.Journal, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No call or token processing is admitted at the original case deadline."""
    facade, controller, _, grants = ready_controller(qtbot, journal)
    monkeypatch.setattr(executor.time, "monotonic", lambda: journal.deadline)
    token = CreditToken.for_chunk(EPOCH, 2, 1, len(RGB), 0)
    with pytest.raises(RuntimeError, match="deadline"):
        controller.credit(1, encode(Message("frame_wait", 2, token.payload())))
    assert not grants
    assert not controller.transcripts[0].events
    facade.deleteLater()


def consumer(args: list[Any]) -> executor.RetainedConsumer:
    """Use the actual VerifiedSequence from collector and worker terminal fixture."""
    return executor.RetainedConsumer(*args, pid=7, **contracts.timing())


def test_actual_consumer_clears_aliases(
    produced: tuple[Any, ...], journal: executor.Journal
) -> None:
    """Both full buffers compare off GUI and inputs disappear before completion."""
    args, _, _ = produced
    runner = consumer(args)
    started = runner.begin(journal)
    runner.join(timeout=1)
    evidence = runner.poll(journal, started)
    assert evidence is not None
    assert evidence["full_rgb_exact"] is True
    assert runner.inputs is None
    runner.dispose(journal)


@pytest.mark.parametrize("fault", ["deadline", "log", "start"])
def test_consumer_prestart_failure_drops_inputs(
    produced: tuple[Any, ...],
    journal: executor.Journal,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    """No retained pixels survive a failed check, logging write or thread start."""
    args, _, _ = produced
    runner = consumer(args)
    if fault == "deadline":
        journal.deadline = 0
    elif fault == "log":
        monkeypatch.setattr(journal, "emit", Mock(side_effect=RuntimeError("log")))
    else:
        monkeypatch.setattr(runner, "start", Mock(side_effect=RuntimeError("start")))
    with pytest.raises(RuntimeError, match=r".+"):
        runner.begin(journal)
    assert runner.inputs is None
    assert not runner.is_alive()
    runner.dispose(journal)


def test_consumer_exact_deadline_disposal(
    produced: tuple[Any, ...],
    journal: executor.Journal,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Joining never starts a fresh three-second budget after comparison expiry."""
    args, _, _ = produced
    runner = consumer(args)
    started = runner.begin(journal)
    runner.join(timeout=1)
    monkeypatch.setattr(executor.time, "monotonic", lambda: started + 3)
    with pytest.raises(RuntimeError, match="exceeded3s"):
        runner.poll(journal, started)
    joined = Mock()
    monkeypatch.setattr(runner, "join", joined)
    runner.dispose(journal)
    joined.assert_called_once_with(timeout=0.0)


def test_child_preloop_failure_closes_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The journal closes even when installed provenance fails before QApplication."""
    captured = []

    def fail(_args: object, journal: executor.Journal) -> None:
        captured.append(journal)
        raise RuntimeError("provenance")

    monkeypatch.setattr(executor, "_run_child", fail)
    args = argparse.Namespace(output=tmp_path, preparation_start=time.monotonic())
    with pytest.raises(RuntimeError, match="provenance"):
        executor.child(args)
    assert captured[0].stream.closed


def test_child_first_log_failure_closes_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Journal constructor failure cannot leak its already-open log stream."""
    streams = []

    def fail(self: executor.Journal, *_args: object) -> None:
        streams.append(self.stream)
        raise RuntimeError("first log")

    monkeypatch.setattr(executor.Journal, "emit", fail)
    args = argparse.Namespace(output=tmp_path, preparation_start=time.monotonic())
    with pytest.raises(RuntimeError, match="first log"):
        executor.child(args)
    assert streams[0].closed


@pytest.mark.parametrize("name", ["gui.setup", "channel.start"])
@pytest.mark.parametrize("boundary", ["call", "case"])
def test_logged_flush_deadline_blocks_action(
    journal: executor.Journal, monkeypatch: pytest.MonkeyPatch, name: str, boundary: str
) -> None:
    """Actual setup/start helper freshly checks both budgets after logging flush."""
    now = 10.0
    monkeypatch.setattr(executor.time, "monotonic", lambda: now)
    journal.deadline = 12.0 if boundary == "case" else 100.0
    original = journal.emit

    def slow_flush(event: str, label: str) -> None:
        nonlocal now
        original(event, label)
        now = journal.deadline if boundary == "case" else 13.0

    monkeypatch.setattr(journal, "emit", slow_flush)
    action = Mock()
    with pytest.raises(RuntimeError, match="deadline"):
        executor.logged_call(journal, name, action)
    action.assert_not_called()
