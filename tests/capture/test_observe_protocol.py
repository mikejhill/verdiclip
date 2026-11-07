"""Pure one-observe schemas and pairing; no native, Qt, pipe or provider calls."""

import hashlib
import json
from dataclasses import FrozenInstanceError, replace
from functools import partial
from typing import Any
from unittest.mock import Mock

import pytest

from verdiclip.capture import observe_protocol as protocol
from verdiclip.capture.frame_envelope import FrameHeader, header_for_rgb
from verdiclip.platform.helper_protocol import ProtocolError


@pytest.fixture
def target() -> protocol.ObserveTarget:
    """Use a small full panel whose edges remain visible in envelope geometry."""
    return protocol.ObserveTarget(7, 1234, 99, (-1, 2))


@pytest.fixture
def value(target: protocol.ObserveTarget) -> dict[str, Any]:
    """Build the complete exact schema with limited visibility evidence."""
    return {
        "observation_schema": 1,
        "epoch": "a" * 32,
        "request_id": 1,
        "frame_id": 1,
        "target": {**target.value(), "runtime_id": [42, -1], "framework": "WPF"},
        "guard": {
            "foreground_hwnd": 99,
            "foreground_pid": 7,
            "input_tick": 123,
            "root_client_rect": [-10, -10, 100, 100],
            "target_rect": [-2, 1, 2, 4],
            "capture_rect": [-2, 1, 2, 4],
            "dpi": [96, 96],
            "monitors": [[1, -100, -100, 100, 100, True]],
            "visibility_evidence": {
                "scope": "owned-wpf-fixture-external-check-v1",
                "root_visible": True,
                "root_minimized": False,
                "root_cloaked": False,
                "external_intersection_count": 0,
                "point_root_checks": 6,
                "internal_overlay_assessment": "fixture-assumption-only",
            },
        },
        "scroll": {
            "horizontal": -1,
            "vertical": 0,
            "horizontal_view": 100,
            "vertical_view": 25,
            "horizontal_scrollable": False,
            "vertical_scrollable": True,
        },
        "settled": {"samples": 3, "quiet_ns": 200_000_000},
    }


def observation(value: dict[str, Any]) -> protocol.Observation:
    """Use the public bounded wire decoder, not an unchecked construction seam."""
    return protocol.Observation.parse(json.dumps(value).encode())


def test_target_golden_and_detached_value(target: protocol.ObserveTarget) -> None:
    """Target request bytes are deterministic and contain no dispatch selectors."""
    expected = (
        b'{"operation":"observe","target":{"hwnd":99,"pid":7,"point":[-1,2],'
        b'"process_created_100ns":1234},"target_schema":1}'
    )
    assert target.serialize() == expected
    assert protocol.ObserveTarget.parse(expected) == target
    detached = target.value()
    detached["pid"] = 8
    assert target.pid == 7
    with pytest.raises(FrozenInstanceError):
        target.pid = 8  # ty: ignore[invalid-assignment] - frozen contract


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("pid", True),
        ("pid", 0),
        ("pid", 2**32),
        ("hwnd", -1),
        ("process_created_100ns", 2**64),
        ("point", [1, 2]),
        ("point", (1,)),
        ("point", (False, 2)),
        ("point", (2**31, 2)),
    ],
)
def test_target_constructor_bounds(
    target: protocol.ObserveTarget, field: str, bad: object
) -> None:
    """Direct DTO construction is as strict as wire parsing."""
    with pytest.raises(protocol.ObservationError):
        replace(target, **{field: bad})


@pytest.mark.parametrize("data", [b"", b" " * 1025, b"[]", b'{"pid":1,"pid":2}'])
def test_target_bad_wire(data: bytes) -> None:
    """Bound bytes and strict duplicate/shape validation precede interpretation."""
    with pytest.raises(ValueError, match=r".+"):
        protocol.ObserveTarget.parse(data)


def test_observation_canonical_value_golden(value: dict[str, Any]) -> None:
    """Normalization ignores JSON lexical formatting but preserves value identity."""
    actual = observation(value)
    normalized = json.loads(actual.canonical)
    assert normalized["scroll"] == {
        "horizontal": -1.0,
        "vertical": 0.0,
        "horizontal_view": 100.0,
        "vertical_view": 25.0,
        "horizontal_scrollable": False,
        "vertical_scrollable": True,
    }
    assert (
        actual.digest.hex()
        == "2036099d69749d1ffb1f1ce99479a95fa1a2c8dcc35af47ede73f9b93ec5c101"
    )
    value["scroll"]["vertical"] = -0.0
    equivalent = json.dumps(value, indent=4).encode()
    assert protocol.Observation.parse(equivalent) == actual
    with pytest.raises(protocol.ObservationError, match="normalize"):
        protocol.Observation(equivalent)
    value["guard"]["input_tick"] += 1
    assert observation(value).digest != actual.digest
    assert actual.target.pid == 7
    assert actual.frame_identity().width == 4
    assert actual.frame_identity().height == 3
    with pytest.raises(FrozenInstanceError):
        actual.canonical = b"{}"  # ty: ignore[invalid-assignment] - immutable contract


@pytest.mark.parametrize(
    ("path", "bad"),
    [
        (("extra",), 1),
        (("observation_schema",), True),
        (("epoch",), "0" * 32),
        (("epoch",), "A" * 32),
        (("request_id",), 128),
        (("frame_id",), 2),
        (("target", "runtime_id"), []),
        (("target", "runtime_id"), [1] * 33),
        (("target", "runtime_id"), [True]),
        (("target", "framework"), "Chrome"),
        (("guard", "foreground_pid"), 8),
        (("guard", "input_tick"), -1),
        (("guard", "root_client_rect"), [-1, 1, 2, 4]),
        (("guard", "capture_rect"), [-1, 1, 2, 4]),
        (("guard", "target_rect"), [0, 0, 0, 1]),
        (("guard", "dpi"), [96, 120]),
        (("guard", "dpi"), [96, True]),
        (("guard", "visibility_evidence", "root_visible"), 1),
        (("guard", "visibility_evidence", "internal_overlay_assessment"), "proven"),
        (("guard", "monitors"), []),
        (("guard", "monitors"), [[1, 0, 0, 2, 2, True]]),
        (("guard", "monitors"), [[1, -100, -100, 100, 100, 1]]),
        (("guard", "monitors"), [[1, -100, -100, 100, 100, False]]),
        (("guard", "monitors"), [[1, -100, -100, 100, 100, True]] * 2),
        (
            ("guard", "monitors"),
            [[2, -100, -100, 100, 100, True], [1, 200, 200, 300, 300, False]],
        ),
        (("scroll", "vertical"), float("nan")),
        (("scroll", "vertical"), float("inf")),
        (("scroll", "vertical"), True),
        (("scroll", "horizontal"), -0.5),
        (("scroll", "horizontal_view"), 0),
        (("scroll", "vertical_view"), 101),
        (("scroll", "horizontal_scrollable"), True),
        (("settled", "samples"), 2),
        (("settled", "quiet_ns"), 199_999_999),
    ],
)
def test_observation_invalid_evidence(
    value: dict[str, Any], path: tuple[str, ...], bad: object
) -> None:
    """Malformed, optimistic or unsupported evidence cannot become a frame identity."""
    current = value
    for key in path[:-1]:
        current = current[key]
    current[path[-1]] = bad
    with pytest.raises(ValueError, match=r".+"):
        observation(value)


@pytest.mark.parametrize("size", [(1025, 512), (1024, 513)])
def test_full_panel_limits_before_envelope(
    value: dict[str, Any], size: tuple[int, int]
) -> None:
    """The one-observe cap is stricter than generic frame-envelope dimensions."""
    rect = [-2, 1, size[0] - 2, size[1] + 1]
    value["guard"].update(
        target_rect=rect, capture_rect=rect, root_client_rect=[-100, -100, 2000, 2000]
    )
    with pytest.raises(protocol.ObservationError, match="one-observe bounds"):
        observation(value)


def test_selected_point_must_be_inside_full_panel(value: dict[str, Any]) -> None:
    """Target correlation cannot silently switch to an unrelated rectangle."""
    value["target"]["point"] = [10, 10]
    with pytest.raises(protocol.ObservationError, match="outside full panel"):
        observation(value)


def test_wire_limits_and_duplicate_metadata() -> None:
    """Reject raw oversized or duplicate metadata before schema normalization."""
    with pytest.raises(protocol.ObservationError, match="byte bound"):
        protocol.Observation.parse(b" " * 8193)
    with pytest.raises(ProtocolError, match="duplicate"):
        protocol.Observation.parse(b'{"epoch":"a","epoch":"b"}')


@pytest.fixture
def pairing(
    target: protocol.ObserveTarget,
) -> tuple[protocol.ObservePairing, Mock, Mock]:
    """The first clock reading reserves the unchanged two-second operation budget."""
    clock, cancelled = Mock(return_value=10.0), Mock(return_value=False)
    return (
        protocol.ObservePairing(target, "a" * 32, 1, clock=clock, cancelled=cancelled),
        clock,
        cancelled,
    )


def paired(
    value: dict[str, Any], state: protocol.ObservePairing
) -> tuple[FrameHeader, bytes]:
    """Admit a tiny real envelope for exact streamed hashing."""
    metadata = observation(value)
    pixels = bytes(range(36))
    header = header_for_rgb(metadata.frame_identity(), pixels)
    state.metadata(metadata.canonical)
    state.header(header.encode())
    return header, pixels


@pytest.mark.parametrize("header_first", [True, False])
def test_reordered_fragmented_pairing_and_exact_eof(
    value: dict[str, Any],
    pairing: tuple[protocol.ObservePairing, Mock, Mock],
    *,
    header_first: bool,
) -> None:
    """Both callback orders converge without accepting success at payload length."""
    state, _clock, _cancelled = pairing
    metadata = observation(value)
    pixels = bytes(range(36))
    header = header_for_rgb(metadata.frame_identity(), pixels)
    if not header_first:
        state.metadata(metadata.canonical)
    state.header(header.encode()[:63])
    assert not state.payload_ready
    state.header(header.encode()[63:])
    if header_first:
        assert not state.payload_ready
        state.metadata(metadata.canonical)
    assert state.payload_ready
    state.feed(pixels[:7])
    state.feed(pixels[7:])
    assert state.eof() == header
    assert not state.payload_ready
    assert state.failure is None
    with pytest.raises(protocol.ObservationError, match="terminal"):
        state.eof()


@pytest.mark.parametrize("action", ["metadata", "header", "body", "eof", "poll"])
def test_expiry_and_first_failure(
    value: dict[str, Any],
    pairing: tuple[protocol.ObservePairing, Mock, Mock],
    action: str,
) -> None:
    """Every entry point rejects exact-deadline arrival without resetting budget."""
    state, clock, cancelled = pairing
    _header, pixels = paired(value, state)
    clock.return_value = 12.0
    operations = {
        "metadata": lambda: state.metadata(observation(value).canonical),
        "header": lambda: state.header(b"x"),
        "body": lambda: state.feed(pixels),
        "eof": state.eof,
        "poll": state.check,
    }
    with pytest.raises(protocol.ObservationError, match="deadline"):
        operations[action]()
    first = state.failure
    cancelled.return_value = True
    with pytest.raises(protocol.ObservationError, match="deadline"):
        state.check()
    assert state.failure == first


@pytest.mark.parametrize("action", ["metadata", "header", "body", "eof"])
def test_cancellation_blocks_all_admission(
    value: dict[str, Any],
    pairing: tuple[protocol.ObservePairing, Mock, Mock],
    action: str,
) -> None:
    """Cancellation remains terminal even if later callbacks contain valid evidence."""
    state, _clock, cancelled = pairing
    _header, pixels = paired(value, state)
    cancelled.return_value = True
    with pytest.raises(protocol.ObservationError, match="cancelled"):
        {
            "metadata": lambda: state.metadata(observation(value).canonical),
            "header": lambda: state.header(b"x"),
            "body": lambda: state.feed(pixels),
            "eof": state.eof,
        }[action]()
    cancelled.return_value = False
    with pytest.raises(protocol.ObservationError, match="cancelled"):
        state.check()


@pytest.mark.parametrize(
    "kind",
    [
        "header",
        "metadata",
        "body",
        "eof",
        "wrong-header",
        "wrong-metadata",
        "chunk",
        "trailer",
        "digest",
        "truncated",
    ],
)
def test_bad_pairing_is_permanent(
    value: dict[str, Any],
    pairing: tuple[protocol.ObservePairing, Mock, Mock],
    kind: str,
) -> None:
    """Duplicates, mismatches and incomplete streams never yield a frame receipt."""
    state, _clock, _cancelled = pairing
    metadata = observation(value)

    def exercise() -> None:
        if kind == "body":
            state.feed(b"x")
        elif kind == "eof":
            state.eof()
        elif kind == "wrong-metadata":
            value["request_id"] = 2
            state.metadata(observation(value).canonical)
        elif kind == "wrong-header":
            state.metadata(metadata.canonical)
            wrong = replace(metadata.frame_identity(), request_id=2)
            state.header(header_for_rgb(wrong, bytes(36)).encode())
        else:
            header, pixels = paired(value, state)
            if kind == "header":
                state.header(header.encode())
            elif kind == "metadata":
                state.metadata(metadata.canonical)
            elif kind == "chunk":
                state.feed(b"x" * 65537)
            elif kind == "trailer":
                state.feed(pixels + b"x")
            else:
                state.feed(pixels[:-1] if kind == "truncated" else bytes(36))
                state.eof()

    with pytest.raises(ValueError, match=r".+"):
        exercise()
    assert state.failure is not None
    with pytest.raises(protocol.ObservationError):
        state.check()


@pytest.mark.parametrize("time", [float("nan"), float("inf"), True, 9.0])
def test_invalid_or_regressed_clock(
    pairing: tuple[protocol.ObservePairing, Mock, Mock], time: object
) -> None:
    """A malformed clock cannot extend an operation or permit further data."""
    state, clock, _cancelled = pairing
    clock.return_value = time
    with pytest.raises(protocol.ObservationError):
        state.check()


def test_post_hash_deadline_checked(
    value: dict[str, Any],
    pairing: tuple[protocol.ObservePairing, Mock, Mock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Synchronous digest work cannot cross the old deadline and publish success."""
    state, clock, _cancelled = pairing
    _header, pixels = paired(value, state)
    state.feed(pixels)
    original = protocol.FrameStream.finish

    def slow_finish(stream: protocol.FrameStream) -> FrameHeader:
        result = original(stream)
        clock.return_value = 12.0
        return result

    monkeypatch.setattr(protocol.FrameStream, "finish", slow_finish)
    with pytest.raises(protocol.ObservationError, match="deadline"):
        state.eof()


def test_before_boundary_success(
    value: dict[str, Any], pairing: tuple[protocol.ObservePairing, Mock, Mock]
) -> None:
    """Progress keeps the original deadline; a complete frame just before it passes."""
    state, clock, _cancelled = pairing
    header, pixels = paired(value, state)
    clock.return_value = 11.999
    state.feed(pixels)
    assert state.eof() == header
    assert header.image_sha256 == hashlib.sha256(pixels).digest()


def test_pairing_rejects_mutable_target() -> None:
    """Pairing cannot retain mutable caller-owned identity dictionaries."""
    with pytest.raises(protocol.ObservationError, match="immutable target"):
        protocol.ObservePairing(
            {},  # ty: ignore[invalid-argument-type] - deliberate invalid DTO
            "a" * 32,
            1,
            clock=lambda: 0.0,
            cancelled=lambda: False,
        )


@pytest.mark.parametrize("phase", ["metadata", "header", "feed"])
def test_expiry_during_processing_is_not_admitted(
    value: dict[str, Any],
    pairing: tuple[protocol.ObservePairing, Mock, Mock],
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    """Parsing, pairing or hashing may consume time but never renew the deadline."""
    state, clock, _cancelled = pairing
    metadata = observation(value)
    pixels = bytes(range(36))
    header = header_for_rgb(metadata.frame_identity(), pixels)
    if phase == "metadata":
        original_parse = protocol.Observation.parse

        def slow_parse(data: bytes) -> protocol.Observation:
            result = original_parse(data)
            clock.return_value = 12.0
            return result

        monkeypatch.setattr(protocol.Observation, "parse", slow_parse)
        action = partial(state.metadata, metadata.canonical)
    elif phase == "header":
        state.metadata(metadata.canonical)
        original_pair = state._pair

        def slow_pair() -> None:
            original_pair()
            clock.return_value = 12.0

        monkeypatch.setattr(state, "_pair", slow_pair)
        action = partial(state.header, header.encode())
    else:
        paired(value, state)
        original_feed = protocol.FrameStream.feed

        def slow_feed(stream: protocol.FrameStream, data: bytes) -> None:
            original_feed(stream, data)
            clock.return_value = 12.0

        monkeypatch.setattr(protocol.FrameStream, "feed", slow_feed)
        action = partial(state.feed, pixels)
    with pytest.raises(protocol.ObservationError, match="deadline"):
        action()
    assert not state.payload_ready


@pytest.mark.parametrize("phase", ["initial", "header", "body"])
@pytest.mark.parametrize("callback", ["clock", "cancelled"])
@pytest.mark.parametrize("error", [RuntimeError("callback failed"), TypeError()])
def test_callback_exceptions_latch_first_failure(
    value: dict[str, Any],
    pairing: tuple[protocol.ObservePairing, Mock, Mock],
    phase: str,
    callback: str,
    error: Exception,
) -> None:
    """Ordinary callback errors cannot leave a partially consumed operation live."""
    state, clock, cancelled = pairing
    metadata = observation(value)
    header = header_for_rgb(metadata.frame_identity(), bytes(36))
    if phase == "header":
        state.header(header.encode()[:32])
    elif phase == "body":
        paired(value, state)
        state.feed(bytes(range(10)))
    failing = clock if callback == "clock" else cancelled
    failing.side_effect = error
    with pytest.raises(protocol.ObservationError, match=r"callback failed|TypeError"):
        state.check()
    first = state.failure
    assert first is not None
    assert first != ""
    failing.side_effect = None
    for operation in (
        partial(state.metadata, metadata.canonical),
        partial(state.header, header.encode()),
        partial(state.feed, bytes(36)),
        state.eof,
    ):
        with pytest.raises(protocol.ObservationError) as caught:
            operation()
        assert str(caught.value) == first
        assert state.failure == first
    assert not state.payload_ready


@pytest.mark.parametrize("result", [0, 1, None, object()])
@pytest.mark.parametrize("phase", ["initial", "header", "body"])
def test_cancellation_callback_requires_exact_bool(
    value: dict[str, Any],
    pairing: tuple[protocol.ObservePairing, Mock, Mock],
    result: object,
    phase: str,
) -> None:
    """Neither truthy objects nor falsy sentinels can substitute for the latch."""
    state, _clock, cancelled = pairing
    if phase == "header":
        state.header(b"x")
    elif phase == "body":
        paired(value, state)
        state.feed(bytes(range(10)))
    cancelled.return_value = result
    with pytest.raises(protocol.ObservationError, match="cancellation callback result"):
        state.check()
    cancelled.return_value = False
    with pytest.raises(protocol.ObservationError, match="cancellation callback result"):
        state.eof()
    assert not state.payload_ready


@pytest.mark.parametrize("phase", ["metadata", "header", "feed", "eof"])
def test_processing_exception_remains_terminal(
    value: dict[str, Any],
    pairing: tuple[protocol.ObservePairing, Mock, Mock],
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    """Unexpected validation/stream errors preserve a nonempty first diagnostic."""
    state, _clock, _cancelled = pairing
    metadata = observation(value)
    failure = Mock(side_effect=RuntimeError())
    if phase == "metadata":
        monkeypatch.setattr(protocol.Observation, "parse", failure)
        action = partial(state.metadata, metadata.canonical)
    elif phase == "header":
        monkeypatch.setattr(state, "_pair", failure)
        action = partial(state.header, b"x")
    else:
        paired(value, state)
        monkeypatch.setattr(
            protocol.FrameStream, "feed" if phase == "feed" else "finish", failure
        )
        action = partial(state.feed, b"x") if phase == "feed" else state.eof
    with pytest.raises(protocol.ObservationError, match="RuntimeError"):
        action()
    with pytest.raises(protocol.ObservationError, match="RuntimeError"):
        state.check()
    assert state.failure == "RuntimeError"
