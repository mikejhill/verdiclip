"""Fixture-only direct pixel comparison; no native capture qualification."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from unittest.mock import Mock

import pytest
from scripts import installed_observe_smoke as smoke
from tests.platform.test_helper_channel import observe_value

from verdiclip.capture.frame_envelope import header_for_rgb
from verdiclip.capture.retained_frame import VerifiedFrame


def sample() -> tuple[VerifiedFrame, bytes, bytes]:
    """Build an independently known full 510 by 430 RGB reference."""
    observation = observe_value(Mock(epoch="ab" * 16), 510, 430)
    rgb = bytes(range(256)) * 2569 + bytes(range(236))
    assert len(rgb) == 510 * 430 * 3
    receipt = json.dumps(
        {"passed": True, "observe": {"observation": json.loads(observation.canonical)}}
    ).encode()
    frame = VerifiedFrame(
        rgb,
        header_for_rgb(observation.frame_identity(), rgb),
        observation.canonical,
        hashlib.sha256(receipt).digest(),
    )
    return frame, rgb, receipt


def run(
    frame: object,
    rgb: bytes,
    receipt: bytes,
    bounds: tuple[int, ...] = (0, 0, 510, 430),
) -> smoke.RetainedConsumer:
    """Run the actual consumer thread with a bounded portable test wait."""
    consumer = smoke.RetainedConsumer(frame, rgb, receipt, bounds)
    consumer.start()
    consumer.join(timeout=2)
    assert not consumer.is_alive()
    assert consumer.inputs is None
    return consumer


def test_consumer_compares_every_byte_off_gui_and_drops_inputs() -> None:
    """Success carries metadata only and cannot run on the GUI main thread."""
    frame, rgb, receipt = sample()
    consumer = run(frame, rgb, receipt)
    assert consumer.error is None
    assert consumer.result == {
        "full_rgb_exact": True,
        "bytes": len(rgb),
        "rect": [0, 0, 510, 430],
        "consumer_off_gui": True,
        "receipt_sha256": hashlib.sha256(receipt).hexdigest(),
    }
    with pytest.raises(RuntimeError, match="independent consumer"):
        smoke.compare_retained_frame(frame, rgb, receipt, (0, 0, 510, 430))


@pytest.mark.parametrize(
    "index", [0, 509 * 3, 510 * 429 * 3, 510 * 430 * 3 - 1, 100001]
)
def test_any_single_channel_mismatch_rejects(index: int) -> None:
    """Edges and interior require exact RGB, without channel tolerance."""
    frame, rgb, receipt = sample()
    changed = bytearray(rgb)
    changed[index] ^= 1
    consumer = run(frame, bytes(changed), receipt)
    assert consumer.result is None
    assert "differ" in str(consumer.error)


@pytest.mark.parametrize("fault", ["bounds", "length", "receipt", "identity", "type"])
def test_consumer_rejects_correlation_and_geometry_changes(fault: str) -> None:
    """Matching pixels alone cannot authorize a mismatched frame identity."""
    frame, rgb, receipt = sample()
    bounds = (0, 0, 510, 430)
    value: object = frame
    if fault == "bounds":
        bounds = (1, 0, 511, 430)
    elif fault == "length":
        rgb = rgb[:-1]
    elif fault == "receipt":
        receipt += b" "
    elif fault == "identity":
        value = replace(
            frame,
            header=replace(
                frame.header, identity=replace(frame.identity, request_id=2)
            ),
        )
    else:
        value = object()
    consumer = run(value, rgb, receipt, bounds)
    assert consumer.result is None
    assert consumer.error is not None


def test_consumer_exception_drops_frame_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure retains bounded text, not an exception with pixel traceback aliases."""
    frame, rgb, receipt = sample()
    monkeypatch.setattr(
        smoke, "compare_retained_frame", Mock(side_effect=MemoryError("x" * 3000))
    )
    consumer = run(frame, rgb, receipt)
    assert consumer.result is None
    assert consumer.error is not None
    assert len(consumer.error) == 2048


@pytest.mark.parametrize("late_at", ["entry", "after-end-log", "case"])
def test_completed_consumer_cannot_cross_original_deadlines(
    monkeypatch: pytest.MonkeyPatch, late_at: str
) -> None:
    """A completed result never resets the three-second or absolute case bound."""
    frame, rgb, receipt = sample()
    consumer = run(frame, rgb, receipt)
    clock = [1.0]
    monkeypatch.setattr(smoke.time, "monotonic", lambda: clock[0])
    journal = Mock(spec=smoke.Journal)
    if late_at == "entry":
        clock[0] = 4.0
    elif late_at == "after-end-log":
        journal.emit.side_effect = lambda *_: clock.__setitem__(0, 4.0)
    else:
        journal.check.side_effect = [None, RuntimeError("case expired")]
    with pytest.raises(RuntimeError, match=r"exceeded3s|case expired"):
        consumer.poll(journal, 1.0)


def test_live_consumer_never_admits_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """Result metadata cannot bypass evidence that the consumer has completed."""
    frame, rgb, receipt = sample()
    consumer = run(frame, rgb, receipt)
    monkeypatch.setattr(consumer, "is_alive", lambda: True)
    monkeypatch.setattr(smoke.time, "monotonic", lambda: 1.0)
    journal = Mock(spec=smoke.Journal)
    assert consumer.poll(journal, 1.0) is None
    journal.emit.assert_not_called()


def test_start_failure_drops_untransferred_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed thread start cannot leave the full frame in its input tuple."""
    frame, rgb, receipt = sample()
    consumer = smoke.RetainedConsumer(frame, rgb, receipt, (0, 0, 510, 430))
    monkeypatch.setattr(
        consumer, "start", Mock(side_effect=RuntimeError("start failed"))
    )
    with pytest.raises(RuntimeError, match="start failed"):
        consumer.begin(Mock(spec=smoke.Journal))
    assert consumer.inputs is None


def test_failed_comparison_cannot_admit_metadata() -> None:
    """A worker failure is a failure even after its thread has stopped."""
    frame, rgb, receipt = sample()
    consumer = run(frame, rgb[:-1], receipt)
    with pytest.raises(RuntimeError, match="comparison failed"):
        consumer.poll(Mock(spec=smoke.Journal), smoke.time.monotonic())


@pytest.mark.parametrize("failure", ["log-error", "case-expiry", "exact-call-expiry"])
def test_prestart_logging_cannot_launch_late_consumer(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """All logging and post-log deadline failures discard inputs before launch."""
    frame, rgb, receipt = sample()
    consumer = smoke.RetainedConsumer(frame, rgb, receipt, (0, 0, 510, 430))
    started = Mock()
    monkeypatch.setattr(consumer, "start", started)
    clock = [1.0]
    monkeypatch.setattr(smoke.time, "monotonic", lambda: clock[0])
    journal = Mock(spec=smoke.Journal)
    if failure == "log-error":
        journal.emit.side_effect = OSError("log failed")
    elif failure == "case-expiry":
        journal.check.side_effect = [None, RuntimeError("case expired")]
    else:
        journal.emit.side_effect = lambda *_: clock.__setitem__(0, 4.0)
    with pytest.raises((RuntimeError, OSError)):
        consumer.begin(journal)
    started.assert_not_called()
    assert consumer.inputs is None
