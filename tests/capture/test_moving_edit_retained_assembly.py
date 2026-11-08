"""Independent full-width assembly and fixed source provenance ownership tests."""

from __future__ import annotations

import gc
import hashlib
import json
from dataclasses import replace
from unittest.mock import Mock

import pytest

from verdiclip.capture import moving_edit_frame as owned
from verdiclip.capture import moving_edit_retention as retention
from verdiclip.capture.frame_envelope import FrameHeader, FrameIdentity

LINES = (3, 0, 8, 13, 13, 0, 3)
SCENE = b"".join(
    hashlib.sha256(str(row).encode()).digest()[:3] * 493 for row in range(621)
)


def sources(slots: owned.ParentPixelSlots) -> tuple[owned.VerifiedCandidate, ...]:
    """Build original frames through the actual bounded sink in fixed order."""
    result = []
    for index, line in enumerate(LINES, 1):
        rgb = SCENE[line * 16 * 1479 : (line * 16 + 413) * 1479]
        canonical = json.dumps({"frame": index}, sort_keys=True).encode()
        identity = FrameIdentity(
            b"s" * 16,
            2 * index + 1,
            index,
            10,
            20,
            493,
            413,
            hashlib.sha256(canonical).digest(),
        )
        sink = owned.FrameSink(identity, lambda: None, slots=slots)
        for offset in range(0, len(rgb), 65536):
            sink.feed(rgb[offset : offset + 65536])
        candidate = sink.freeze(
            FrameHeader(identity, hashlib.sha256(rgb).digest()), canonical
        )
        candidate.storage.move(f"source_{index}")
        result.append(candidate)
    return tuple(result)


def test_independent_assembly_all_pixels_provenance_and_slot_lifetimes() -> None:
    """Seven sources remain owned while only2/3/4 contribute every exact output row."""
    slots = owned.ParentPixelSlots()
    frames = sources(slots)
    result = retention.assemble_sources(frames, slots, lambda: None)
    assert result.rgb == SCENE
    expected = tuple(
        [(2, row) for row in range(270)]
        + [(3, row) for row in range(142, 246)]
        + [(4, row) for row in range(166, 413)]
    )
    assert result.row_provenance == expected
    assert len(result.provenance) == 621 * 4
    assert (
        result.provenance_sha256
        == hashlib.sha256(
            json.dumps(expected, separators=(",", ":")).encode()
        ).hexdigest()
    )
    for row, (frame_id, source_row) in enumerate(expected):
        assert (
            result.rgb[row * 1479 : (row + 1) * 1479]
            == frames[frame_id - 1].rgb[source_row * 1479 : (source_row + 1) * 1479]
        )
    assert slots.reserved_bytes == 7 * 610827 + 918459
    assert slots.peak_bytes == 7 * 610827 + 2 * 918459
    del result
    gc.collect()
    assert slots.reserved_bytes == 7 * 610827
    del frames
    gc.collect()
    assert slots.reserved_bytes == 0


@pytest.mark.parametrize("position", [0, 1, 3])
def test_returned_repeated_and_restored_bytes_reject_before_assembly(
    position: int,
) -> None:
    """Independent repeats are full-byte equality checks, never digest substitution."""
    slots = owned.ParentPixelSlots()
    frames = list(sources(slots))
    victim = frames[position]
    victim.storage.release()
    storage = slots.reserve(f"source_{position + 1}")
    changed = (
        b"x"
        + victim.header.image_sha256 * 0
        + SCENE[LINES[position] * 16 * 1479 + 1 : (LINES[position] * 16 + 413) * 1479]
    )
    storage.bind(changed)
    frames[position] = owned.VerifiedCandidate(
        storage,
        replace(victim.header, image_sha256=hashlib.sha256(changed).digest()),
        victim.observation,
    )
    with pytest.raises(ValueError, match=r"repeat|return|restor"):
        retention.assemble_sources(tuple(frames), slots, lambda: None)
    assert "assembly_chunks" not in slots.active_slots
    assert "assembly_final" not in slots.active_slots


def test_cancellation_before_and_after_join_releases_assembly_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation blocks new allocation and disposes failed final output."""
    slots = owned.ParentPixelSlots()
    frames = sources(slots)
    with pytest.raises(TimeoutError, match="expired"):
        retention.assemble_sources(
            frames, slots, Mock(side_effect=TimeoutError("expired"))
        )
    original = retention._join_chunks
    expired = [False]

    def check() -> None:
        if expired[0]:
            raise TimeoutError("expired")

    def join(chunks: tuple[bytes, ...]) -> bytes:
        result = original(chunks)
        expired[0] = True
        return result

    monkeypatch.setattr(retention, "_join_chunks", join)
    with pytest.raises(TimeoutError, match="expired"):
        retention.assemble_sources(frames, slots, check)
    assert slots.reserved_bytes == 7 * 610827


def test_ambiguous_raster_never_uses_native_delta_as_a_pixel_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Uniform overlaps cannot qualify a unique fixed displacement."""
    monkeypatch.setitem(globals(), "SCENE", bytes(len(SCENE)))
    slots = owned.ParentPixelSlots()
    frames = sources(slots)
    with pytest.raises(ValueError, match="ambiguous"):
        retention.assemble_sources(frames, slots, lambda: None)
    assert slots.reserved_bytes == 7 * 610827


@pytest.mark.parametrize("changed", [b"", b"x" * (621 * 4), bytearray(b"x")])
def test_missing_or_changed_packed_row_provenance_is_rejected(changed: bytes) -> None:
    """The final provenance is bounded immutable evidence, not optional metadata."""
    slots = owned.ParentPixelSlots()
    frames = sources(slots)
    assembly = retention.assemble_sources(frames, slots, lambda: None)
    with pytest.raises(ValueError, match="provenance"):
        replace(assembly, provenance=changed)
    assert assembly.summary["sha256"] == hashlib.sha256(SCENE).hexdigest()


def test_assembly_source_shape_and_storage_domain_are_exact() -> None:
    """Arbitrary candidate lists or another channel's rasters are not adopted."""
    slots = owned.ParentPixelSlots()
    frames = sources(slots)
    with pytest.raises(ValueError, match="seven"):
        retention.assemble_sources(frames[:-1], slots, lambda: None)
    with pytest.raises(ValueError, match="ownership"):
        retention.assemble_sources(frames, owned.ParentPixelSlots(), lambda: None)


def test_cancel_between_output_slices_prevents_the_next_pixel_allocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each assembly slice has its own original-deadline admission boundary."""
    slots = owned.ParentPixelSlots()
    frames = sources(slots)
    calls = []
    original = retention._slice_rows

    def check() -> None:
        if calls:
            raise TimeoutError("cancelled after first slice")

    def slice_rows(frame: owned.VerifiedCandidate, begin: int, end: int) -> bytes:
        result = original(frame, begin, end)
        calls.append(frame.header.identity.frame_id)
        return result

    monkeypatch.setattr(retention, "_slice_rows", slice_rows)
    with pytest.raises(TimeoutError, match="first slice"):
        retention.assemble_sources(frames, slots, check)
    assert calls == [2]
    assert slots.reserved_bytes == 7 * 610827
