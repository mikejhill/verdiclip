"""Retained-frame ordering and disposal, not a native thread-join qualification."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from unittest.mock import Mock

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent
from tests.platform import test_helper_channel as helpers

from verdiclip.capture.frame_envelope import header_for_rgb
from verdiclip.capture.retained_frame import FrameSink, FrameState
from verdiclip.platform import helper_channel as channel
from verdiclip.platform.helper_protocol import Message
from verdiclip.platform.helper_runtime import RuntimeManifest

runtime = helpers.runtime
worker = helpers.worker
observing = helpers.observing


@pytest.fixture
def lifetime(
    runtime: RuntimeManifest, qapp: QCoreApplication, monkeypatch: pytest.MonkeyPatch
) -> Iterator[channel._Lifetime]:
    """Use direct deterministic callbacks without starting a native thread."""
    del qapp
    monkeypatch.setattr(channel._Worker, "moveToThread", lambda *_: None)
    value = channel._Lifetime(1, runtime, threading.Event())
    value.expiry_timer.stop()
    yield value
    value.vault.invalidate()
    value.worker.helper = None
    value._missing_receipt = False
    if not value.stopped:
        value._stopped()
    if value.receipt is None:
        value._retired(1, b'{"passed":false}')
    value._release_if_disposed()
    if shiboken6.isValid(value.worker):
        value.worker.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert value not in channel._RETAINED


def sealed(lifetime: channel._Lifetime) -> tuple[bytes, bytes]:
    """Build pixels on a stand-in worker, leaving publication unauthorized."""
    lifetime.worker.observe_consumed = True
    observation = helpers.observe_value(lifetime.worker)
    rgb = bytes(range(36))
    header = header_for_rgb(observation.frame_identity(), rgb)
    vault = lifetime.vault
    vault.publish_stamp("busy", channel.time.monotonic() + 10)
    vault.begin(header.identity)
    sink = FrameSink(header.identity, lambda: None)
    sink.feed(rgb)
    candidate = sink.freeze(header, observation.canonical)
    vault.publish_stamp("retiring", channel.time.monotonic() + 5)
    receipt = b'{"passed":true,"observe":{"observation":{}}}'
    vault.seal(candidate, receipt)
    assert sink.allocated_bytes == 0
    return rgb, receipt


def facade_for(lifetime: channel._Lifetime) -> channel.HelperChannel:
    """Wire the real facade publication path without launching a process."""
    facade = channel.HelperChannel()
    facade._generation = 1
    facade._state = "closing"
    facade._observe_consumed = True
    facade._vault = lifetime.vault
    facade._cancelled = lifetime.cancelled
    facade._lifetime = lifetime
    facade.destroyed.connect(lifetime.abandon)
    return facade


@pytest.mark.parametrize("finished_first", [False, True])
def test_single_take_waits_for_both_metadata_and_finished(
    lifetime: channel._Lifetime, *, finished_first: bool
) -> None:
    """Neither event alone publishes raw pixels; both orders retain one result."""
    rgb, receipt = sealed(lifetime)
    facade = facade_for(lifetime)
    events = []
    lifetime.terminal.connect(lambda generation, raw: events.append((generation, raw)))
    if finished_first:
        lifetime._stopped()
    else:
        lifetime._retired(1, receipt)
    assert events == []
    with pytest.raises(RuntimeError, match="qualified"):
        facade.take_frame(1, 1)
    if finished_first:
        lifetime._retired(1, receipt)
    else:
        lifetime._stopped()
    assert lifetime in channel._RETAINED
    assert events == [(1, receipt)]
    facade._retired(*events[0])
    result = facade.take_frame(1, 1)
    assert result.rgb == rgb
    assert lifetime.vault.state == FrameState.TAKEN
    facade.cancel()  # A transferred immutable result is not revocable.
    assert result.rgb == rgb
    with pytest.raises(ValueError, match=r".+"):
        facade.take_frame(1, 1)
    assert lifetime not in channel._RETAINED


@pytest.mark.parametrize("when", ["before-facade", "after-facade"])
def test_cancel_after_lifetime_completion_invalidates_unclaimed_payload(
    lifetime: channel._Lifetime, when: str
) -> None:
    """Terminal-state cancellation still disposes a frame that was never taken."""
    _rgb, receipt = sealed(lifetime)
    facade = facade_for(lifetime)
    lifetime._retired(1, receipt)
    lifetime._stopped()
    assert lifetime.completed
    if when == "after-facade":
        facade._retired(1, receipt)
    facade.cancel()
    if when == "before-facade":
        facade._retired(1, receipt)
    assert lifetime.vault.state == FrameState.INVALID
    with pytest.raises((ValueError, RuntimeError), match=r".+"):
        facade.take_frame(1, 1)


@pytest.mark.parametrize("delivered", [False, True])
def test_destroyed_facade_cannot_leave_completed_lifetime_payload(
    lifetime: channel._Lifetime, *, delivered: bool
) -> None:
    """The cleanup receiver outlives deletion between completed and queued delivery."""
    _rgb, receipt = sealed(lifetime)
    facade = facade_for(lifetime)
    lifetime._retired(1, receipt)
    lifetime._stopped()
    if delivered:
        facade._retired(1, receipt)
    assert lifetime in channel._RETAINED
    shiboken6.delete(facade)
    assert lifetime.vault.state == FrameState.INVALID
    assert lifetime not in channel._RETAINED


def test_expired_payload_never_releases_a_running_worker(
    lifetime: channel._Lifetime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Payload invalidity is independent from live-thread/resource retention."""
    _rgb, _receipt = sealed(lifetime)
    stamp = lifetime.vault.stamp()
    assert stamp is not None
    monkeypatch.setattr(channel.time, "monotonic", lambda: stamp.deadline)
    lifetime._expiry()
    assert lifetime.vault.state == FrameState.INVALID
    assert not lifetime.completed
    assert lifetime in channel._RETAINED


def test_finished_without_receipt_expires_once_at_authoritative_stamp(
    lifetime: channel._Lifetime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An absent receipt never upgrades sealed pixels to successful metadata."""
    _rgb, _receipt = sealed(lifetime)
    stamp = lifetime.vault.stamp()
    assert stamp is not None
    events = []
    lifetime.terminal.connect(lambda _g, raw: events.append(json.loads(raw)))
    lifetime._stopped()
    assert events == []
    monkeypatch.setattr(channel.time, "monotonic", lambda: stamp.deadline)
    lifetime._expiry()
    lifetime._expiry()
    assert len(events) == 1
    assert events[0] == {"passed": False, "failure": "missing-terminal-receipt"}
    assert lifetime.vault.state == FrameState.INVALID


def test_unclosed_native_ownership_is_retained_even_after_missing_receipt(
    lifetime: channel._Lifetime,
) -> None:
    """A synthetic failure is not evidence that native handles were released."""
    lifetime.worker.helper = Mock(status=helpers.helper_status())
    lifetime._stopped()
    lifetime._expiry()  # No worker stamp was ever published.
    assert lifetime.completed
    assert lifetime._missing_receipt
    assert lifetime in channel._RETAINED
    assert lifetime.vault.state == FrameState.INVALID


def test_retirement_stamp_keeps_existing_session_cap_exception(
    worker: channel._Worker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sink mirrors deadlines instead of imposing a new session clamp."""
    monkeypatch.setattr(channel.time, "monotonic", lambda: 5.0)
    worker.session_deadline = 4.0
    worker.deadline = 8.0
    worker.phase = "retiring"
    worker._admit()
    stamp = worker.vault.stamp()
    assert stamp is not None
    assert stamp.deadline == 8.0


def test_actual_frame_flow_retains_bytes_only_after_natural_terminal(
    observing: tuple[channel._Worker, Mock, Mock, bytearray, list[int]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real pairing and EOF code retain the exact bytes rather than a digest alone."""
    worker, control, helper, data, _clock = observing
    metadata = helpers.observe_value(worker)
    rgb = bytes(range(36))
    helpers.observed_response(worker, control, metadata)
    data.extend(header_for_rgb(metadata.frame_identity(), rgb).encode() + rgb)
    worker._frame_readable()
    assert worker.vault.state == FrameState.BUILDING
    assert worker.pixel_sink is not None
    worker._frame_disconnected()
    assert worker.pixel_sink is None
    assert worker.pixel_candidate.rgb == rgb
    helpers.feed_peer(
        worker, control, Message("response", 2, {"operation": "finished"})
    )
    worker._disconnected()
    helper.status = helpers.helper_status(retired=True, exit_code=0)
    helper.poll.return_value = helper.status
    receipts = []
    worker.terminal.connect(lambda _g, receipt: receipts.append(receipt))
    monkeypatch.setattr(channel.QThread, "currentThread", Mock)
    worker._poll()
    assert worker.pixel_candidate is None
    assert worker.vault.state == FrameState.SEALED
    assert json.loads(receipts[0])["passed"]
    worker.vault.authorize(receipts[0])
    assert worker.vault.take(1, 1, receipts[0]).rgb == rgb


@pytest.mark.parametrize("fault", ["trailing", "truncated", "digest", "cancel"])
def test_frame_failure_disposes_worker_storage_before_any_publication(
    observing: tuple[channel._Worker, Mock, Mock, bytearray, list[int]], fault: str
) -> None:
    """Complete payload length alone never qualifies retained data for a take."""
    worker, control, _helper, data, _clock = observing
    metadata = helpers.observe_value(worker)
    rgb = bytes(range(36))
    helpers.observed_response(worker, control, metadata)
    body = rgb[:-1] if fault == "truncated" else rgb
    if fault == "digest":
        body = b"x" + body[1:]
    data.extend(header_for_rgb(metadata.frame_identity(), rgb).encode() + body)
    worker._frame_readable()
    assert worker.vault.state == FrameState.BUILDING
    assert worker.pixel_candidate is None
    if fault == "cancel":
        worker.cancelled.set()
    elif fault == "trailing":
        data.extend(b"x")
    worker._frame_disconnected()
    assert worker.failure is not None
    assert worker.vault.state == FrameState.INVALID
    assert worker.pixel_sink is None
    assert worker.pixel_candidate is None


def test_terminal_serialization_exception_discards_frozen_pixels(
    observing: tuple[channel._Worker, Mock, Mock, bytearray, list[int]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A terminal construction error cannot leave the candidate privately retained."""
    worker, control, helper, data, _clock = observing
    metadata = helpers.observe_value(worker)
    rgb = bytes(range(36))
    helpers.observed_response(worker, control, metadata)
    data.extend(header_for_rgb(metadata.frame_identity(), rgb).encode() + rgb)
    worker._frame_readable()
    worker._frame_disconnected()
    assert worker.pixel_candidate is not None
    helpers.feed_peer(
        worker, control, Message("response", 2, {"operation": "finished"})
    )
    worker._disconnected()
    helper.status = helpers.helper_status(retired=True, exit_code=0)
    monkeypatch.setattr(
        channel, "_serialized", Mock(side_effect=MemoryError("receipt"))
    )
    with pytest.raises(MemoryError, match="receipt"):
        worker._terminal()
    assert worker.pixel_candidate is None
    assert worker.pixel_sink is None
    assert worker.vault.state == FrameState.INVALID


def test_late_take_discards_without_second_terminal(
    lifetime: channel._Lifetime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Receipt delivery does not start a fresh payload publication allowance."""
    _rgb, receipt = sealed(lifetime)
    facade = facade_for(lifetime)
    events = []
    facade.terminal.connect(lambda *_: events.append(True))
    lifetime._retired(1, receipt)
    lifetime._stopped()
    facade._retired(1, receipt)
    stamp = lifetime.vault.stamp()
    assert stamp is not None
    monkeypatch.setattr(channel.time, "monotonic", lambda: stamp.deadline)
    with pytest.raises(ValueError, match=r".+"):
        facade.take_frame(1, 1)
    assert events == [True]
    assert lifetime.vault.state == FrameState.INVALID
    assert lifetime not in channel._RETAINED


def test_stale_expiry_revision_cannot_discard_current_phase(
    lifetime: channel._Lifetime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transition between timer snapshot and expiry defeats the old revision."""
    lifetime.vault.publish_stamp("busy", 5.0)
    original = lifetime.vault.expire

    def transition(revision: int, now: float) -> bool:
        lifetime.vault.publish_stamp("retiring", 10.0)
        return original(revision, now)

    monkeypatch.setattr(channel.time, "monotonic", lambda: 5.0)
    monkeypatch.setattr(lifetime.vault, "expire", transition)
    lifetime._expiry()
    assert lifetime.vault.state == FrameState.EMPTY
    assert lifetime in channel._RETAINED


@pytest.mark.parametrize(
    "order",
    [
        "expire-receipt-finished",
        "expire-finished-receipt",
        "receipt-expire-finished",
        "finished-expire-receipt",
    ],
)
def test_expiry_before_completion_never_publishes_observe_success(
    lifetime: channel._Lifetime, monkeypatch: pytest.MonkeyPatch, order: str
) -> None:
    """An invalid vault cannot bypass the receipt/finished authorization gate."""
    _rgb, receipt = sealed(lifetime)
    stamp = lifetime.vault.stamp()
    assert stamp is not None
    facade = facade_for(lifetime)
    events = []
    lifetime.terminal.connect(facade._retired)
    facade.terminal.connect(lambda _generation, raw: events.append(json.loads(raw)))
    for event in order.split("-"):
        if event == "expire":
            monkeypatch.setattr(channel.time, "monotonic", lambda: stamp.deadline)
            lifetime._expiry()
        elif event == "receipt":
            lifetime._retired(1, receipt)
        else:
            lifetime._stopped()
    assert len(events) == 1
    assert events[0]["passed"] is False
    assert lifetime.vault.state == FrameState.INVALID
    with pytest.raises(RuntimeError, match="qualified"):
        facade.take_frame(1, 1)


def test_expiry_between_lifetime_and_facade_delivery_rejects_success(
    lifetime: channel._Lifetime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An internal queued receipt is not yet a publicly delivered success."""
    _rgb, receipt = sealed(lifetime)
    facade = facade_for(lifetime)
    queued = []
    public = []
    lifetime.terminal.connect(lambda generation, raw: queued.append((generation, raw)))
    facade.terminal.connect(lambda _generation, raw: public.append(json.loads(raw)))
    lifetime._retired(1, receipt)
    lifetime._stopped()
    assert json.loads(queued[0][1])["passed"] is True
    stamp = lifetime.vault.stamp()
    assert stamp is not None
    monkeypatch.setattr(channel.time, "monotonic", lambda: stamp.deadline)
    lifetime._expiry()
    facade._retired(*queued[0])
    assert public[0]["passed"] is False
    assert public[0]["failure"] == "retained-frame-unavailable"
    with pytest.raises(RuntimeError, match="qualified"):
        facade.take_frame(1, 1)
