"""One-frame credit state and facade races, not native pipe qualification."""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from tests.platform import test_helper_channel as helpers

from verdiclip.capture.frame_envelope import header_for_rgb
from verdiclip.capture.retained_frame import FrameState
from verdiclip.platform import helper_channel as channel
from verdiclip.platform.helper_protocol import Message, encode

runtime = helpers.runtime
worker = helpers.worker
observing = helpers.observing

type Session = tuple[channel._Worker, Mock, Mock, bytearray, list[int]]


def facade_for(worker: channel._Worker) -> channel.HelperChannel:
    """Wire actual public slots to the real worker with deterministic affinity."""
    facade = channel.HelperChannel()
    facade._generation = worker.generation
    facade._state = "busy"
    facade._observe_consumed = True
    facade._cancelled = worker.cancelled
    facade._vault = worker.vault
    facade._credit_epoch = worker.epoch
    facade._lifetime = Mock(credit_gate=worker.credit_gate)
    facade._credit.connect(worker.grant_credit)
    facade._cancel.connect(worker.cancel)
    worker.credit_required.disconnect()
    worker.credit_required.connect(facade._credit_required)
    return facade


def announce(
    observing: Session, width: int = 4, height: int = 3
) -> tuple[channel.HelperChannel, bytes, list[tuple[int, bytes]]]:
    """Only a paired header and wait make a public grant available."""
    owner, control, _helper, data, _clock = observing
    facade = facade_for(owner)
    metadata = helpers.observe_value(owner, width, height)
    rgb = bytes(width * height * 3)
    events = []
    facade.credit_required.connect(
        lambda generation, raw: events.append((generation, raw))
    )
    helpers.observed_response(owner, control, metadata)
    assert events == []
    data.extend(header_for_rgb(metadata.frame_identity(), rgb).encode())
    owner._frame_readable()
    assert len(events) == 1
    assert owner.frame_bytes == 0
    assert owner.pixel_sink is not None
    return facade, rgb, events


def test_public_credit_consumes_once_and_duplicate_irreversibly_cancels(
    observing: Session,
) -> None:
    """A second facade call cannot queue a second grant or keep capture alive."""
    owner, control, _helper, _data, _clock = observing
    facade, _rgb, _events = announce(observing)
    facade.grant_frame_credit(1, 1, 0)
    assert owner.credits is not None
    assert owner.credits.snapshot().granted_bytes == 36
    assert control.write.call_count == 2
    with pytest.raises(channel.ProtocolError, match="public-credit"):
        facade.grant_frame_credit(1, 1, 0)
    assert facade._state == "closing"
    assert owner.failure == "cancelled"
    assert owner.pixel_sink is None
    assert owner.vault.state == FrameState.INVALID
    assert control.write.call_count == 2


@pytest.mark.parametrize("index", [0, 1])
def test_public_cancel_at_first_or_interior_wait_has_worker_witness(
    observing: Session, index: int
) -> None:
    """No grant for the announced held token exists before or after cancellation."""
    owner, control, _helper, data, _clock = observing
    facade, rgb, events = announce(observing, 256, 256)
    if index:
        facade.grant_frame_credit(1, 1, 0)
        data.extend(rgb[:65536])
        owner._frame_readable()
        token = channel.CreditToken.for_chunk(owner.epoch, 1, len(rgb), 1)
        helpers.feed_peer(owner, control, Message("frame_wait", 1, token.payload()))
    assert len(events) == index + 1
    assert owner.credits is not None
    token = facade._pending_credit
    assert token is not None
    prior_writes = control.write.call_count
    facade.cancel()
    assert owner.failure == "cancelled"
    owner.credits.witness().validate_held(token)
    owner.grant_credit(1, encode(Message("frame_credit", 1, token.payload())))
    owner.credits.witness().validate_held(token)
    assert control.write.call_count == prior_writes
    assert owner.pixel_sink is None


def test_next_wait_cannot_be_granted_until_prior_body_is_accepted(
    observing: Session,
) -> None:
    """Control may overtake binary delivery without enlarging the credit window."""
    owner, control, _helper, data, _clock = observing
    facade, rgb, events = announce(observing, 256, 256)
    facade.grant_frame_credit(1, 1, 0)
    token = channel.CreditToken.for_chunk(owner.epoch, 1, len(rgb), 1)
    helpers.feed_peer(owner, control, Message("frame_wait", 1, token.payload()))
    assert len(events) == 1
    data.extend(rgb[:65535])
    owner._frame_readable()
    assert len(events) == 1
    data.extend(rgb[65535:65536])
    owner._frame_readable()
    assert len(events) == 2
    facade.grant_frame_credit(1, 1, 1)
    assert owner.credits is not None
    assert owner.credits.snapshot().granted_bytes == 131072


@pytest.mark.parametrize(
    "fault", ["wrong-generation", "wrong-request", "wrong-index", "bool"]
)
def test_invalid_public_grant_cancels_without_native_write(
    observing: Session, fault: str
) -> None:
    """Bad API calls cannot merely raise while the transfer continues."""
    owner, control, _helper, _data, _clock = observing
    facade, _rgb, _events = announce(observing)
    args = {
        "wrong-generation": (2, 1, 0),
        "wrong-request": (1, 2, 0),
        "wrong-index": (1, 1, 1),
        "bool": (True, 1, 0),
    }[fault]
    with pytest.raises(channel.ProtocolError):
        facade.grant_frame_credit(*args)
    assert owner.failure == "cancelled"
    assert control.write.call_count == 1


def test_uncredited_body_fails_before_sink_copy(observing: Session) -> None:
    """A correct image digest cannot excuse an unauthorized body byte."""
    owner, _control, _helper, data, _clock = observing
    facade, rgb, _events = announce(observing)
    data.extend(rgb[:1])
    owner._frame_readable()
    assert owner.failure == "body-without-credit"
    assert owner.frame_bytes == 0
    assert owner.pixel_sink is None
    facade.cancel()


def test_queued_grant_loses_to_cancel_before_worker_reservation(
    observing: Session,
) -> None:
    """A queued GUI command cannot reserve bytes after the shared cancel latch."""
    owner, control, _helper, _data, _clock = observing
    facade, _rgb, _events = announce(observing)
    queued = []
    facade._credit.disconnect()
    facade._credit.connect(lambda generation, raw: queued.append((generation, raw)))
    facade.grant_frame_credit(1, 1, 0)
    assert len(queued) == 1
    facade.cancel()
    owner.grant_credit(*queued[0])
    assert owner.credits is not None
    assert owner.credits.snapshot().granted_bytes == 0
    assert control.write.call_count == 1
    assert owner.failure == "cancelled"


@pytest.mark.parametrize("phase", ["closing", "terminal", "stale-generation"])
def test_stale_or_closing_public_wait_never_revives_credit(
    observing: Session, phase: str
) -> None:
    """Late queued notification cannot reopen the public grant path."""
    _owner, _control, _helper, _data, _clock = observing
    facade, _rgb, events = announce(observing)
    facade._pending_credit = None
    if phase != "stale-generation":
        facade._state = phase
    generation, raw = events[0]
    facade._credit_required(generation + (phase == "stale-generation"), raw)
    assert facade._pending_credit is None
    assert len(events) == 1
    facade.cancel()


def test_same_generation_duplicate_wait_after_grant_irreversibly_fails(
    observing: Session,
) -> None:
    """Already consumed public tokens cannot be announced again."""
    owner, _control, _helper, _data, _clock = observing
    facade, _rgb, events = announce(observing)
    facade.grant_frame_credit(1, 1, 0)
    with pytest.raises(channel.ProtocolError, match="stale-public"):
        facade._credit_required(*events[0])
    assert owner.failure == "cancelled"


@pytest.mark.parametrize("when", ["before-write", "after-write"])
def test_original_observe_deadline_still_bounds_credit_write(
    observing: Session, when: str
) -> None:
    """A grant cannot restart the observation clock, even after a slow write."""
    owner, control, _helper, _data, clock = observing
    facade, _rgb, _events = announce(observing)
    if when == "before-write":
        clock[0] = 3_000_000_000
    else:

        def slow_write(raw: bytes) -> int:
            clock[0] = 3_000_000_000
            return len(raw)

        control.write.side_effect = slow_write
    facade.grant_frame_credit(1, 1, 0)
    assert owner.failure is not None
    assert "deadline" in owner.failure
    assert owner.pixel_sink is None
    assert control.write.call_count == (1 if when == "before-write" else 2)


def test_compact_full_credit_history_stays_below_terminal_budget() -> None:
    """A late cancellation witness does not duplicate large token dictionaries."""
    import threading  # noqa: PLC0415 - local pure fixture

    transfer = channel.ParentCredit(
        "ab" * 16, 1, 1572864, gate=channel.CreditGate(threading.Event())
    )
    for index in range(23):
        token = channel.CreditToken.for_chunk("ab" * 16, 1, 1572864, index)
        transfer.receive_wait(token)
        transfer.reserve_grant(token)
        transfer.accept_body(token.count)
    held = channel.CreditToken.for_chunk("ab" * 16, 1, 1572864, 23)
    transfer.receive_wait(held)
    transfer.cancel()
    transfer.observe_cancel()
    witness = transfer.witness()
    witness.validate_held(held)
    encoded = channel._serialized(
        {
            "credit": channel._credit_snapshot(transfer.snapshot()),
            "cancellation": {
                "latched": channel._credit_snapshot(witness.latched),
                "final": channel._credit_snapshot(witness.final),
            },
        }
    )
    assert len(encoded) < 8192


def test_explicit_transfer_control_cap_prevents_next_grant(observing: Session) -> None:
    """The documented control count bound is checked before reservation and I/O."""
    owner, control, _helper, _data, _clock = observing
    facade, _rgb, _events = announce(observing)
    owner.transfer_messages = channel.MAX_TRANSFER_MESSAGES
    facade.grant_frame_credit(1, 1, 0)
    assert owner.failure == "transfer-control-budget"
    assert owner.credits is not None
    assert owner.credits.snapshot().granted_bytes == 0
    assert control.write.call_count == 1


def test_explicit_transfer_control_cap_rejects_inbound_record(
    observing: Session,
) -> None:
    """Flooded control cannot enlarge the fixed per-observation transcript."""
    owner, control, _helper, _data, _clock = observing
    facade, rgb, _events = announce(observing)
    owner.transfer_messages = channel.MAX_TRANSFER_MESSAGES
    token = channel.CreditToken.for_chunk(owner.epoch, 1, len(rgb), 0)
    helpers.feed_peer(owner, control, Message("frame_wait", 1, token.payload()))
    assert owner.failure == "transfer-control-budget"
    assert owner.transfer_messages == channel.MAX_TRANSFER_MESSAGES
    facade.cancel()
