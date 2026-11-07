"""Real worker acceptance and cleanup deadlines, without native transport claims."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import cast
from unittest.mock import Mock

import pytest
from tests.capture import test_sequence_retention as values
from tests.platform import test_sequence_channel as helpers
from tests.platform.test_helper_channel import helper_status

from verdiclip.capture.frame_envelope import FrameHeader
from verdiclip.capture.retained_frame import FrameSink, FrameState, VerifiedCandidate
from verdiclip.capture.sequence_retention import SequenceCandidate
from verdiclip.platform import sequence_channel as channel
from verdiclip.platform.sequence_protocol import (
    Decoder,
    SequenceFrameProof,
    SequenceObservation,
)

state_worker = helpers.state_worker


def finish_sequence(worker: channel._Worker) -> None:
    """Run actual peer/worker acceptance before arranging terminal process evidence."""
    peer = helpers.begin_sequence(worker)
    helpers.complete_frame(worker, peer, 1)
    helpers.complete_frame(worker, peer, 2)
    worker.finish()
    request = Decoder().feed(cast("helpers.MemorySocket", worker.socket).writes[-1])[0]
    worker._message(peer.finish(request))
    worker.eof = True
    worker.launch_returned = True
    worker.helper = Mock(status=helper_status(retired=True, exit_code=0))


def test_actual_terminal_then_single_use_full_sequence(
    state_worker: channel._Worker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Natural retirement seals both original full buffers, not only their hashes."""
    worker = state_worker
    finish_sequence(worker)
    receipts: list[bytes] = []
    worker.terminal.connect(lambda _g, raw: receipts.append(raw))
    monkeypatch.setattr(channel.QThread, "currentThread", Mock)
    worker._terminal()
    assert worker.sequence_candidate is None
    assert json.loads(receipts[0])["passed"] is True
    worker.vault.authorize(receipts[0])
    result = worker.vault.take(1, receipts[0])
    assert len(result.frames) == 2
    assert all(item.rgb == bytes(range(36)) for item in result.frames)
    with pytest.raises(ValueError, match=r".+"):
        worker.vault.take(1, receipts[0])


@pytest.mark.parametrize("fault", ["terminate", "exit", "cleanup", "eof", "deadline"])
def test_terminal_cannot_publish_without_all_natural_cleanup_evidence(
    state_worker: channel._Worker, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """A completed collector cannot substitute for process and channel retirement."""
    worker = state_worker
    finish_sequence(worker)
    helper = cast("Mock", worker.helper)
    if fault == "terminate":
        helper.status = replace(helper.status, termination_requested=True)
    elif fault == "exit":
        helper.status = replace(helper.status, exit_code=1)
    elif fault == "cleanup":
        helper.status = replace(helper.status, cleanup_errors=("close",))
    elif fault == "eof":
        worker.eof = False
    else:
        monkeypatch.setattr(channel.time, "monotonic", lambda: worker.deadline)
    receipts: list[bytes] = []
    worker.terminal.connect(lambda _g, raw: receipts.append(raw))
    monkeypatch.setattr(channel.QThread, "currentThread", Mock)
    worker._terminal()
    assert json.loads(receipts[0])["passed"] is False
    assert worker.vault.state is FrameState.INVALID
    assert worker.sequence_candidate is None


@pytest.mark.parametrize("point", ["freeze", "accept-write"])
def test_frame_deadline_includes_freeze_and_accept_commitment(
    state_worker: channel._Worker, monkeypatch: pytest.MonkeyPatch, point: str
) -> None:
    """Expiry after payload EOF still prevents collector commitment and next request."""
    worker = state_worker
    peer = helpers.begin_sequence(worker)
    if point == "freeze":
        original = FrameSink.freeze

        def freeze(
            self: FrameSink, header: FrameHeader, canonical: bytes
        ) -> VerifiedCandidate:
            result = original(self, header, canonical)
            monkeypatch.setattr(channel.time, "monotonic_ns", lambda: 3_000_000_000)
            return result

        monkeypatch.setattr(FrameSink, "freeze", freeze)
    else:
        socket = cast("helpers.MemorySocket", worker.socket)
        original_write = socket.write

        def write(raw: bytes) -> int:
            result = original_write(raw)
            if Decoder().feed(raw)[0].kind == "frame_accept":
                monkeypatch.setattr(channel.time, "monotonic_ns", lambda: 3_000_000_000)
            return result

        monkeypatch.setattr(socket, "write", write)
    with pytest.raises(ValueError, match="deadline"):
        helpers.complete_frame(worker, peer, 1)
    worker._fail("expired-frame")
    assert worker.failure == "expired-frame"
    assert worker.proofs == []
    assert worker.collector is not None
    assert worker.collector.retained_bytes == 0
    assert worker.vault.state is FrameState.INVALID


def test_retirement_deadline_is_not_silently_clamped_to_sequence(
    state_worker: channel._Worker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The distinct reviewed cleanup allowance preserves existing lifetime semantics."""
    worker = state_worker
    worker.phase = "retiring"
    worker.session_deadline = 1.0
    worker.deadline = 6.5
    monkeypatch.setattr(channel.time, "monotonic", lambda: 6.4)
    worker._admit()
    stamp = worker.vault.stamp()
    assert stamp is not None
    assert stamp.deadline == 6.5


@pytest.mark.parametrize("bad", [True, 1.0, -1, 2**63, "throw"])
def test_startup_bad_clock_never_launches_and_reports_failure(
    state_worker: channel._Worker, monkeypatch: pytest.MonkeyPatch, bad: object
) -> None:
    """Even the first clock read belongs inside the owned terminal error boundary."""
    worker = state_worker
    worker.helper = None
    worker.socket = None
    worker.collector = None
    worker.sequence_clock = None
    worker.phase = "starting"
    worker.launch_returned = False
    launch = Mock()
    monkeypatch.setattr(channel, "_build_command", launch)
    if bad == "throw":
        monkeypatch.setattr(
            channel.time, "monotonic_ns", Mock(side_effect=OSError("clock"))
        )
    else:
        monkeypatch.setattr(channel.time, "monotonic_ns", lambda: bad)
    events: list[bytes] = []
    worker.terminal.connect(lambda _g, raw: events.append(raw))
    monkeypatch.setattr(channel.QThread, "currentThread", Mock)
    worker.start()
    assert worker.failure is not None
    assert worker.launch_returned
    assert worker.phase == "retiring"
    launch.assert_not_called()
    worker._poll()
    assert len(events) == 1
    assert json.loads(events[0])["passed"] is False
    assert worker.done


def test_clock_regression_between_frames_cannot_start_new_listener(
    state_worker: channel._Worker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The original nanosecond history survives completed frame acceptance."""
    worker = state_worker
    peer = helpers.begin_sequence(worker)
    helpers.complete_frame(worker, peer, 1)
    original = worker.sequence_clock
    assert original is not None
    assert original.frame_deadline_ns is None
    monkeypatch.setattr(channel.time, "monotonic_ns", lambda: 1_499_999_999)
    before = len(cast("helpers.MemorySocket", worker.socket).writes)
    with pytest.raises(ValueError, match="deadline"):
        worker._send(
            {
                "operation": "observe",
                "frame_id": 2,
                "requested_ns": 1_000_000_000,
                "deadline_ns": 3_000_000_000,
            }
        )
    assert worker.sequence_clock is original
    assert original.failed
    assert worker.frame_server is None
    assert len(cast("helpers.MemorySocket", worker.socket).writes) == before
    worker._fail("clock-regression")
    assert worker.collector is not None
    assert worker.collector.retained_bytes == 0


@pytest.mark.parametrize("fault", ["serialize", "seal", "cancel-during-seal"])
def test_terminal_exception_disposes_entire_sequence(
    state_worker: channel._Worker, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """No first frame escapes a final receipt, sealing or cancellation failure."""
    worker = state_worker
    finish_sequence(worker)
    receipts: list[bytes] = []
    worker.terminal.connect(lambda _g, raw: receipts.append(raw))
    monkeypatch.setattr(channel.QThread, "currentThread", Mock)
    if fault == "serialize":
        monkeypatch.setattr(
            channel, "_serialized", Mock(side_effect=MemoryError("receipt"))
        )
        with pytest.raises(MemoryError, match="receipt"):
            worker._terminal()
    else:
        if fault == "seal":
            monkeypatch.setattr(
                worker.vault, "seal", Mock(side_effect=ValueError("seal"))
            )
        else:
            original = worker.vault.seal

            def seal(candidate: SequenceCandidate, receipt: bytes) -> None:
                original(candidate, receipt)
                worker.cancelled.set()

            monkeypatch.setattr(worker.vault, "seal", seal)
        worker._terminal()
        assert json.loads(receipts[0])["passed"] is False
    assert worker.vault.state is FrameState.INVALID
    assert worker.sequence_candidate is None


@pytest.mark.parametrize("field", ["runtime_id", "input_tick", "vertical"])
def test_changed_read_only_source_rejects_before_second_accept(
    state_worker: channel._Worker, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    """Actual serialized second metadata cannot change source under identical pixels."""
    original = values.frame

    def changed(index: int) -> tuple[VerifiedCandidate, SequenceFrameProof]:
        candidate, proof = original(index)
        if index == 1:
            return candidate, proof
        wrapper = json.loads(candidate.observation)
        inner = wrapper["observation"]
        if field == "runtime_id":
            inner["target"][field] = [42, -2]
        elif field == "input_tick":
            inner["guard"][field] += 1
        else:
            inner["scroll"][field] = 1
        observation = SequenceObservation(channel._canonical(wrapper))
        header = FrameHeader(
            observation.frame_identity(), candidate.header.image_sha256
        )
        return VerifiedCandidate(candidate.rgb, header, observation.canonical), proof

    monkeypatch.setattr(values, "frame", changed)
    monkeypatch.setattr(helpers, "frame", changed)
    worker = state_worker
    peer = helpers.begin_sequence(worker)
    helpers.complete_frame(worker, peer, 1)
    with pytest.raises(ValueError, match="Read-only sequence source state changed"):
        helpers.complete_frame(worker, peer, 2)
    writes = cast("helpers.MemorySocket", worker.socket).writes
    accepts = [
        Decoder().feed(raw)[0]
        for raw in writes
        if Decoder().feed(raw)[0].kind == "frame_accept"
    ]
    assert [message.payload["frame_id"] for message in accepts] == [1]
    assert worker.collector is not None
    assert worker.collector.retained_bytes == 0
    worker._fail("read-only-source-changed")
    assert worker.vault.state is FrameState.INVALID


@pytest.mark.parametrize("fault", ["serialize", "emit", "disposal", "delete", "quit"])
def test_terminal_failure_always_attempts_thread_quit_and_preserves_first_error(
    state_worker: channel._Worker, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """Missing metadata cannot strand a done worker behind a stopped poll timer."""
    worker = state_worker
    finish_sequence(worker)
    primary = RuntimeError("original terminal failure")
    fake_thread = Mock()
    with monkeypatch.context() as patch:
        patch.setattr(channel.QThread, "currentThread", lambda: fake_thread)
        delete = Mock()
        patch.setattr(worker, "deleteLater", delete)
        if fault == "serialize":
            patch.setattr(channel, "_serialized", Mock(side_effect=primary))
        elif fault == "emit":
            patch.setattr(worker, "terminal", Mock(emit=Mock(side_effect=primary)))
        elif fault == "disposal":
            worker.pixel_sink = Mock(discard=Mock(side_effect=primary))
        elif fault == "delete":
            delete.side_effect = primary
        else:
            fake_thread.quit.side_effect = primary
        with pytest.raises(RuntimeError, match="original terminal failure") as caught:
            worker._terminal()
        assert caught.value is primary
        delete.assert_called_once_with()
        fake_thread.quit.assert_called_once_with()
        assert worker.vault.state is FrameState.INVALID
        assert worker.pixel_sink is None
        assert worker.sequence_candidate is None


def test_serialization_error_survives_disposal_and_delete_errors(
    state_worker: channel._Worker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Secondary teardown errors never replace the original failed publication."""
    worker = state_worker
    finish_sequence(worker)
    primary = MemoryError("original serialization")
    fake_thread = Mock()
    with monkeypatch.context() as patch:
        patch.setattr(channel.QThread, "currentThread", lambda: fake_thread)
        patch.setattr(channel, "_serialized", Mock(side_effect=primary))
        patch.setattr(worker, "deleteLater", Mock(side_effect=RuntimeError("delete")))
        assert worker.collector is not None
        patch.setattr(
            worker.collector, "discard", Mock(side_effect=RuntimeError("discard"))
        )
        with pytest.raises(MemoryError, match="original serialization") as caught:
            worker._terminal()
        assert caught.value is primary
        assert worker.collector is None
        assert worker.vault.state is FrameState.INVALID
        fake_thread.quit.assert_called_once_with()


@pytest.mark.parametrize("reason", ["done", "launch-pending"])
def test_terminal_noop_cannot_stop_a_live_or_unstarted_worker(
    state_worker: channel._Worker, monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    """The guaranteed quit applies only after real terminalization is admitted."""
    worker = state_worker
    worker.done = reason == "done"
    worker.launch_returned = reason != "launch-pending"
    fake_thread = Mock()
    with monkeypatch.context() as patch:
        patch.setattr(channel.QThread, "currentThread", lambda: fake_thread)
        delete = Mock()
        patch.setattr(worker, "deleteLater", delete)
        worker._terminal()
        delete.assert_not_called()
        fake_thread.quit.assert_not_called()
