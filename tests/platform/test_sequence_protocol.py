"""Sequence wire is separate and permanently bounded."""

import pytest

from verdiclip.platform import helper_protocol
from verdiclip.platform import sequence_protocol as wire


def test_wire_version_is_separate() -> None:
    """Test wire version is separate."""
    message = wire.Message("request", 1, {"operation": "sequence_begin"})
    assert wire.Decoder().feed(wire.encode(message)) == [message]
    with pytest.raises(wire.ProtocolError):
        helper_protocol.decode(wire.encode(message)[4:])
    with pytest.raises(wire.ProtocolError):
        wire.decode(
            helper_protocol.encode(helper_protocol.Message("request", 1, {}))[4:]
        )


def test_global_count_never_replenishes() -> None:
    """Test global count never replenishes."""
    budget = wire.SequenceBudget()
    for _ in range(512):
        budget.record()
    with pytest.raises(wire.ProtocolError):
        budget.record()
    assert budget.records == 512
    with pytest.raises(wire.ProtocolError):
        budget.record()


def test_frame_count() -> None:
    """Test frame count."""
    budget = wire.SequenceBudget()
    for _ in range(64):
        budget.record(1)
    with pytest.raises(wire.ProtocolError):
        budget.record(1)
    assert budget.records == 64


def test_attribution_never_replenishes_global_records() -> None:
    """Classify inbound IDs after global admission without a second count."""
    budget = wire.SequenceBudget()
    for _ in range(64):
        budget.record()
        budget.attribute_frame(2)
    assert budget.records == 64
    assert budget.frames[1] == 64
    with pytest.raises(wire.ProtocolError):
        budget.attribute_frame(2)
    with pytest.raises(wire.ProtocolError):
        budget.record()
