"""Actual moving facade and wire pairing, without installed native claims."""

import hashlib
import json
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication
from tests.capture.moving_wire_fixtures import make_move_ack, make_observation

from verdiclip.capture.frame_envelope import FrameHeader
from verdiclip.capture.moving_edit_frame import DeadlineStamp
from verdiclip.platform import moving_edit_channel as channel


def test_exact_original_target_and_eof_gate() -> None:
    """Require exact target correlation and actual EOF before qualification."""
    rgb = bytes(610827)
    obs = make_observation(image_sha256=hashlib.sha256(rgb).hexdigest())
    identity = obs.frame_identity()
    header = FrameHeader(identity, hashlib.sha256(rgb).digest())
    pair = channel.SequencePairing(obs.target, obs.value["epoch"], 3, 1, lambda: None)
    pair.header(header.encode())
    pair.metadata(obs.canonical)
    pair.feed(rgb)
    assert pair.eof() == header
    with pytest.raises(ValueError, match=r".+"):
        pair.feed(b"x")


def test_swapped_point_rejects_before_pixels() -> None:
    """Never replace the selected point with an image origin."""
    obs = make_observation()
    target = type(obs.target)(
        obs.target.pid, obs.target.process_created_100ns, obs.target.hwnd, (81, 80)
    )
    pair = channel.SequencePairing(target, obs.value["epoch"], 3, 1, lambda: None)
    with pytest.raises(ValueError, match=r".+"):
        pair.metadata(obs.canonical)


@pytest.fixture
def facade(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> channel.MovingEditChannel:
    """Use the actual facade with a fixed owner clock and no native transport."""
    assert qapp is not None
    value = channel.MovingEditChannel()
    value._state = "ready"
    value._sequence_begun = True
    value._sequence_deadline_ns = 30_000_000_000
    value._last_caller = 1
    value._vault = Mock()
    value._vault.stamp.return_value = DeadlineStamp("ready", 1, 30.0)
    monkeypatch.setattr(channel.time, "monotonic_ns", lambda: 1_000_000_000)
    return value


def test_move_stamped_before_queue_includes_original_reserve(
    facade: channel.MovingEditChannel,
) -> None:
    """A move consumes its deadline before queued delivery."""
    sent = []
    facade._request.connect(lambda caller, raw: sent.append((caller, json.loads(raw))))
    facade.move_next(2)
    assert sent == [
        (
            2,
            {
                "operation": "move_next",
                "step_id": 1,
                "requested_ns": 1_000_000_000,
                "deadline_ns": 4_000_000_000,
            },
        )
    ]
    assert facade._state == "busy"
    with pytest.raises(ValueError, match=r".+"):
        facade.observe(3)
    assert facade._cancelled.is_set()


def test_frame_request_correlates_preceding_move(
    facade: channel.MovingEditChannel,
) -> None:
    """The actual serialized frame request binds the completed fixed move."""
    facade._last_caller = 2
    facade._move_ack = make_move_ack()
    sent = []
    facade._request.connect(lambda caller, raw: sent.append((caller, json.loads(raw))))
    facade.observe(3)
    assert len(sent) == 1
    caller, payload = sent[0]
    assert caller == 3
    assert payload["frame_id"] == payload["step_id"] == 1
    assert payload["requested_ns"] == 1_000_000_000
    assert payload["deadline_ns"] == 3_000_000_000
    assert (
        payload["move_sha256"]
        == hashlib.sha256(channel._canonical(make_move_ack())).hexdigest()
    )


def test_slow_serialization_cannot_queue_move(
    facade: channel.MovingEditChannel, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Serialization cannot consume the deadline and still enqueue a write."""
    sent = []
    facade._request.connect(lambda *args: sent.append(args))
    serialize = channel._serialized

    def slow(value: object) -> bytes:
        result = serialize(value)
        monkeypatch.setattr(channel.time, "monotonic_ns", lambda: 4_000_000_000)
        return result

    monkeypatch.setattr(channel, "_serialized", slow)
    with pytest.raises(ValueError, match="expired"):
        facade.move_next(2)
    assert not sent
    assert facade._cancelled.is_set()
