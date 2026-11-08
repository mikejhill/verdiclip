"""Exact canonical evidence for the fixed seven-frame owned EDIT profile.

These checks preserve the qualified producer's predicates, including its limited
scrollbar assertions. Digests bind evidence; they do not authenticate native I/O.
No Qt, scripts, fresh native reads, or raster copies belong in this module.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, cast

from verdiclip.capture.edit_scroll_contract import (
    FIXTURE_CONTRACT,
    FRAME_LINES,
    TARGET_STYLE,
    TEXT,
    validate_font_attestation,
    validate_target_setup,
)
from verdiclip.capture.frame_envelope import FrameIdentity
from verdiclip.capture.observe_protocol import ObserveTarget
from verdiclip.platform.helper_protocol import (
    ProtocolError,
    invalid_constant,
    unique_object,
)

MAX_OBSERVATION_BYTES = 32_768
MAX_NATIVE_STATE_BYTES = 16_384
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 4096
MAX_JSON_BYTES = 65_536
MAX_STAMP = 2**63 - 1
MAX_PID = 2**32 - 1
MAX_HANDLE = 2**64 - 1
MIN_I32 = -(2**31)
MAX_I32 = 2**31 - 1
MAX_COORDINATE = 1_000_000
MAX_FRAMEWORK_CHARS = 256
FULL_PERCENT = 100.0
_JSON_QUOTE = 34
_JSON_BACKSLASH = 92
WIDTH, HEIGHT = 493, 413
STRIDE = WIDTH * 3
IMAGE_BYTES = STRIDE * HEIGHT
PROFILE = "owned-edit-moving-v1"
CLIENT = [0, 0, WIDTH, HEIGHT]
CLIENT_SCREEN = [60, 60, 553, 473]
ROOT_CLIENT = [40, 40, 573, 701]
TEXT_DIGEST = hashlib.sha256(TEXT.encode("utf-16-le")).hexdigest()


def integer(value: object, low: int, high: int) -> int:
    """Reject booleans, coercion and overflows before using an integer."""
    if type(value) is not int or not low <= value <= high:
        raise ProtocolError("invalid-bounded-integer")
    return value


def fields(value: object, names: str) -> dict[str, Any]:
    """Require one exact schema, never ignore extra metadata."""
    if type(value) is not dict or set(value) != set(names.split()):
        raise ProtocolError("invalid-object-fields")
    return cast("dict[str, Any]", value)


def array(value: object, low: int, high: int) -> list[Any]:
    """Bound a native array before iterating it."""
    if type(value) is not list or not low <= len(value) <= high:
        raise ProtocolError("invalid-array-bound")
    return value


def constant(value: object, expected: object) -> None:
    """Preserve producer scalar distinctions rather than normalizing them."""
    if type(value) is not type(expected) or value != expected:
        raise ProtocolError("invalid-fixed-value")


def epoch(value: object) -> str:
    """Validate the independent nonzero transport epoch or pipe nonce."""
    if (
        type(value) is not str
        or re.fullmatch(r"[0-9a-f]{32}", value) is None
        or value == "0" * 32
    ):
        raise ProtocolError("invalid-epoch")
    return value


def digest_hex(value: object) -> str:
    """Require a complete lowercase SHA-256 value."""
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ProtocolError("invalid-digest")
    return value


def _preflight(data: bytes) -> None:  # noqa: C901 - allocation-free lexical state machine
    """Bound lexical depth/nodes before json.loads allocates a nested tree."""
    depth = nodes = 0
    quoted = escaped = atom = False
    for char in data:
        if quoted:
            if escaped:
                escaped = False
            elif char == _JSON_BACKSLASH:
                escaped = True
            elif char == _JSON_QUOTE:
                quoted = False
            continue
        if char == _JSON_QUOTE:
            quoted, atom = True, False
            nodes += 1
        elif char in (91, 123):
            depth += 1
            nodes += 1
            atom = False
            if depth > MAX_JSON_DEPTH:
                raise ProtocolError("json-depth-bound")
        elif char in (93, 125):
            depth -= 1
            atom = False
        elif char in (9, 10, 13, 32, 44, 58):
            atom = False
        elif not atom:
            atom = True
            nodes += 1
        if nodes > MAX_JSON_NODES:
            raise ProtocolError("json-node-bound")


def _validate_tree(value: object) -> None:  # noqa: C901 - exhaustive bounded JSON type dispatch
    """Traverse bounded iterators, without building a second nested object."""
    count = 0

    def visit(item: object, depth: int) -> None:  # noqa: C901, PLR0912 - one bounded type visitor
        nonlocal count
        count += 1
        if count > MAX_JSON_NODES:
            raise ProtocolError("json-node-bound")
        if depth > MAX_JSON_DEPTH:
            raise ProtocolError("json-depth-bound")
        if type(item) is dict:
            for key, child in item.items():
                if type(key) is not str:
                    raise ProtocolError("json-key-type")
                visit(key, depth)
                visit(child, depth + int(type(child) in (dict, list)))
        elif type(item) is list:
            for child in item:
                visit(child, depth + int(type(child) in (dict, list)))
        elif type(item) is float:
            if not math.isfinite(item):
                raise ProtocolError("json-nonfinite")
        elif type(item) is int:
            integer(item, -(2**64), MAX_HANDLE)
        elif type(item) is str:
            if len(item) > MAX_JSON_BYTES:
                raise ProtocolError("json-string-bound")
        elif item is not None and type(item) is not bool:
            raise ProtocolError("json-value-type")

    visit(value, 1)


def canonical_json(value: object, limit: int = MAX_JSON_BYTES) -> bytes:
    """Encode bounded canonical JSON without an unbounded intermediate output."""
    _validate_tree(value)
    chunks: list[bytes] = []
    size = 0
    try:
        for chunk in json.JSONEncoder(
            sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=True
        ).iterencode(value):
            encoded = chunk.encode("utf-8")
            size += len(encoded)
            if size > limit:
                raise ProtocolError("metadata-byte-bound")
            chunks.append(encoded)
    except (TypeError, ValueError, UnicodeError) as error:
        raise ProtocolError("invalid-json-value") from error
    return b"".join(chunks)


def parse_json(data: bytes, limit: int) -> dict[str, Any]:
    """Validate size and lexical limits before decoding any nested metadata."""
    if type(data) is not bytes or not 0 < len(data) <= limit:
        raise ProtocolError("metadata-byte-bound")
    _preflight(data)
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=invalid_constant,
        )
        if type(value) is not dict:
            raise ProtocolError("json-object-required")
        if canonical_json(value, limit) != data:
            raise ProtocolError("noncanonical-json")
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise ProtocolError("invalid-canonical-json") from error
    return value


def _rect(value: object) -> list[int]:
    """Use the original native physical rectangle bound without fitting."""
    result = [
        integer(part, -MAX_COORDINATE, MAX_COORDINATE) for part in array(value, 4, 4)
    ]
    if result[0] >= result[2] or result[1] >= result[3]:
        raise ProtocolError("empty-physical-rectangle")
    return result


def _contains(outer: list[int], inner: list[int]) -> bool:
    return (
        outer[0] <= inner[0] < inner[2] <= outer[2]
        and outer[1] <= inner[1] < inner[3] <= outer[3]
    )


def validate_target(value: object) -> ObserveTarget:
    """Read the fixed root descriptor, never a caller-selected capture region."""
    target = fields(value, "pid process_created_100ns hwnd point")
    point = array(target["point"], 2, 2)
    return ObserveTarget(
        integer(target["pid"], 1, MAX_PID),
        integer(target["process_created_100ns"], 1, MAX_HANDLE),
        integer(target["hwnd"], 1, MAX_HANDLE),
        (integer(point[0], MIN_I32, MAX_I32), integer(point[1], MIN_I32, MAX_I32)),
    )


def validate_profile_evidence(value: object, target: ObserveTarget) -> dict[str, Any]:
    """Bind immutable owned setup to the target, not to the transport epoch."""
    evidence = fields(value, "fixture_contract font_attestation target_setup")
    constant(evidence["fixture_contract"], FIXTURE_CONTRACT)
    attestation = evidence["font_attestation"]
    if type(attestation) is not dict:
        raise ProtocolError("missing-font-attestation")
    canonical_json(attestation, 8192)
    canonical_json(evidence["target_setup"], 8192)
    try:
        validated = validate_font_attestation(
            attestation,
            epoch=attestation.get("epoch"),
            pid=target.pid,
            creation=target.process_created_100ns,
            role="target",
            root=target.hwnd,
            child=attestation.get("child"),
        )
        identity = {
            key: validated[key] for key in ("epoch", "pid", "creation", "root", "child")
        }
        validate_target_setup(evidence["target_setup"], identity)
    except (TypeError, ValueError, KeyError) as error:
        raise ProtocolError("invalid-owned-profile-evidence") from error
    if (
        not CLIENT_SCREEN[0] <= target.point[0] < CLIENT_SCREEN[2]
        or not CLIENT_SCREEN[1] <= target.point[1] < CLIENT_SCREEN[3]
    ):
        raise ProtocolError("target-point-outside-client")
    return parse_json(canonical_json(evidence), MAX_JSON_BYTES)


def _native(value: object) -> dict[str, Any]:
    native = fields(
        value,
        "hwnd root pid creation uia_hwnd uia_pid same_element parent "
        "class_name style ex_style dpi client_local client_screen window "
        "root_client vertical_bar",
    )
    for key in ("hwnd", "root", "creation", "uia_hwnd", "parent"):
        integer(native[key], 1, MAX_HANDLE)
    for key in ("pid", "uia_pid"):
        integer(native[key], 1, MAX_PID)
    for key, expected in {
        "same_element": True,
        "class_name": "Edit",
        "style": TARGET_STYLE,
        "ex_style": 0,
        "dpi": 96,
        "uia_hwnd": native["hwnd"],
        "uia_pid": native["pid"],
        "parent": native["root"],
    }.items():
        constant(native[key], expected)
    if native["root"] == native["hwnd"]:
        raise ProtocolError("root-child-association")
    for name, expected in (
        ("client_local", CLIENT),
        ("client_screen", CLIENT_SCREEN),
        ("root_client", ROOT_CLIENT),
    ):
        if _rect(native[name]) != expected:
            raise ProtocolError("native-fixed-rectangle")
    window, rail = _rect(native["window"]), _rect(native["vertical_bar"])
    if (
        not _contains(ROOT_CLIENT, window)
        or not _contains(window, CLIENT_SCREEN)
        or window[:2] != CLIENT_SCREEN[:2]
        or window[3] != CLIENT_SCREEN[3]
    ):
        raise ProtocolError("native-window-layout")
    # Original _scroll rail predicates, not guessed state/thumb endpoint equations.
    if (
        not _contains(window, rail)
        or rail[:2] != [CLIENT_SCREEN[2], CLIENT_SCREEN[1]]
        or rail[2:] != window[2:]
    ):
        raise ProtocolError("native-rail-layout")
    return native


def _font(value: object, native: dict[str, Any]) -> dict[str, Any]:
    font = fields(value, "source attestation wm_getfont_opaque")
    constant(font["source"], "owner-attested-immutable-fixture")
    attestation = font["attestation"]
    if type(attestation) is not dict:
        raise ProtocolError("font-attestation-required")
    try:
        validate_font_attestation(
            attestation,
            epoch=attestation.get("epoch"),
            pid=native["pid"],
            creation=native["creation"],
            role="target",
            root=native["root"],
            child=native["hwnd"],
        )
    except (ValueError, TypeError) as error:
        raise ProtocolError("invalid-font-attestation") from error
    constant(font["wm_getfont_opaque"], attestation["opaque_hfont"])
    return font


def _text(value: object) -> None:
    text = fields(
        value, "sha256_utf16le characters lines line_indexes margins selection"
    )
    for key, expected in {
        "sha256_utf16le": TEXT_DIGEST,
        "characters": len(TEXT),
        "lines": 38,
        "margins": 0,
    }.items():
        constant(text[key], expected)
    for actual, expected in zip(
        array(text["line_indexes"], 38, 38), range(0, 38 * 45, 45), strict=True
    ):
        constant(actual, expected)
    for item in array(text["selection"], 2, 2):
        constant(item, 0)


def _monitors(value: object) -> None:
    previous = 0
    containing = False
    for entry in array(value, 1, 8):
        monitor = array(entry, 6, 6)
        handle = integer(monitor[0], 1, MAX_HANDLE)
        if handle <= previous or type(monitor[5]) is not bool:
            raise ProtocolError("invalid-monitor-order-or-type")
        previous = handle
        rect = _rect(monitor[1:5])
        containing |= _contains(rect, ROOT_CLIENT)
    if not containing:
        raise ProtocolError("root-not-contained-on-monitor")


def _scroll(value: object, pattern_value: object) -> tuple[dict[str, Any], list[Any]]:
    scroll = fields(value, "minimum maximum page position track bar_state thumb")
    for name, expected in (("minimum", 0), ("maximum", 37), ("page", 25)):
        constant(scroll[name], expected)
    for name in ("position", "track"):
        integer(scroll[name], 0, 13)
    states = [integer(item, 0, MAX_PID) for item in array(scroll["bar_state"], 6, 6)]
    if states[0] & (0x8000 | 0x10000):
        raise ProtocolError("native-rail-invisible")
    for component in array(scroll["thumb"], 3, 3):
        integer(component, MIN_I32, MAX_I32)
    pattern = array(pattern_value, 6, 6)
    for component in pattern[:4]:
        if type(component) is not float or not math.isfinite(component):
            raise ProtocolError("native-pattern-double")
    if (
        pattern[0].hex() != "0x0.0p+0"
        or pattern[2] != FULL_PERCENT
        or not 0 < pattern[3] < FULL_PERCENT
    ):
        raise ProtocolError("native-pattern-profile")
    constant(pattern[4], 0)
    constant(pattern[5], 1)
    return scroll, pattern


def _text_layout(value: object, first: int, advance: int) -> None:
    layout = fields(
        value, "first_visible_line line13_y line14_y pitch character_advance points"
    )
    for name, expected in {
        "first_visible_line": first,
        "line13_y": (13 - first) * 16,
        "line14_y": (14 - first) * 16,
        "pitch": 16,
        "character_advance": advance,
    }.items():
        constant(layout[name], expected)
    if advance <= 0 or advance * 43 > WIDTH:
        raise ProtocolError("invalid-character-advance")
    points = fields(
        layout["points"], " ".join(str(line) for line in dict.fromkeys((first, 13, 14)))
    )
    for name, raw_point in points.items():
        line = int(name)
        point = fields(raw_point, "index first last right")
        constant(point["index"], line * 45)
        constant(point["right"], 43 * advance)
        for key, expected in (
            ("first", [0, (line - first) * 16]),
            ("last", [42 * advance, (line - first) * 16]),
        ):
            for actual, wanted in zip(array(point[key], 2, 2), expected, strict=True):
                constant(actual, wanted)


def validate_native_state(
    value: object, expected_line: int | None = None
) -> dict[str, Any]:
    """Validate every field the exact qualified ScrollObservation produces."""
    state = parse_json(
        canonical_json(value, MAX_NATIVE_STATE_BYTES), MAX_NATIVE_STATE_BYTES
    )
    fields(
        state,
        "native font text input_tick monitors pattern_fixed scroll "
        "scroll_pattern text_layout",
    )
    native = _native(state["native"])
    font = _font(state["font"], native)
    _text(state["text"])
    integer(state["input_tick"], 0, MAX_PID)
    _monitors(state["monitors"])
    scroll, pattern = _scroll(state["scroll"], state["scroll_pattern"])
    fixed = array(state["pattern_fixed"], 5, 5)
    for actual, wanted in zip(fixed, [pattern[0], *pattern[2:]], strict=True):
        constant(actual, wanted)
    if type(state["text_layout"]) is not dict:
        raise ProtocolError("invalid-text-layout")
    first = integer(state["text_layout"].get("first_visible_line"), 0, 13)
    if expected_line is not None:
        constant(first, integer(expected_line, 0, 13))
    if first != scroll["position"] or not math.isclose(
        pattern[1], 100 * first / 13, rel_tol=1e-12, abs_tol=1e-9
    ):
        raise ProtocolError("native-position-percentage-disagreement")
    _text_layout(
        state["text_layout"],
        first,
        font["attestation"]["owner_measured_metrics"]["average_width"],
    )
    return state


def validate_identity_evidence(value: object, native: dict[str, Any]) -> dict[str, Any]:
    """Validate copied retained objects, never replace identity with HWND alone."""
    identity = fields(
        value, "root_runtime_id panel_runtime_id root_structure panel_structure"
    )
    for name in ("root_runtime_id", "panel_runtime_id"):
        for part in array(identity[name], 1, 32):
            integer(part, MIN_I32, MAX_I32)
    for name, hwnd in (
        ("root_structure", native["root"]),
        ("panel_structure", native["hwnd"]),
    ):
        structure = fields(identity[name], "pid hwnd offscreen framework rect")
        constant(structure["pid"], native["pid"])
        constant(structure["hwnd"], hwnd)
        constant(structure["offscreen"], expected=False)
        if (
            type(structure["framework"]) is not str
            or len(structure["framework"]) > MAX_FRAMEWORK_CHARS
        ):
            raise ProtocolError("invalid-framework")
        rect = array(structure["rect"], 4, 4)
        for component in rect:
            if (
                type(component) not in (int, float)
                or not math.isfinite(component)
                or abs(component) > MAX_COORDINATE
            ):
                raise ProtocolError("invalid-structure-rectangle")
        if rect[0] >= rect[2] or rect[1] >= rect[3]:
            raise ProtocolError("empty-structure-rectangle")
    return parse_json(canonical_json(identity), MAX_JSON_BYTES)


def invariant_identity(
    native_state: object, identity_evidence: object
) -> dict[str, Any]:
    """Enumerate the complete invariant mask; exclude only modeled vertical state."""
    state = validate_native_state(native_state)
    identity = validate_identity_evidence(identity_evidence, state["native"])
    invariant = {
        name: state[name]
        for name in (
            "native",
            "font",
            "text",
            "input_tick",
            "monitors",
            "pattern_fixed",
        )
    }
    invariant["scroll"] = {
        name: state["scroll"][name] for name in ("minimum", "maximum", "page")
    }
    invariant["text_layout"] = {
        name: state["text_layout"][name] for name in ("pitch", "character_advance")
    }
    return {"native_state": invariant, "identity_evidence": identity}


def baseline_identity_sha256(native_state: object, identity_evidence: object) -> str:
    """Bind every invariant guard and copied retained identity to one digest."""
    return hashlib.sha256(
        canonical_json(
            invariant_identity(native_state, identity_evidence), MAX_OBSERVATION_BYTES
        )
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class EditObservation:
    """Deeply immutable canonical moving evidence with full native guard identity."""

    canonical: bytes

    def __post_init__(self) -> None:
        """Require strict canonical bytes even for direct construction."""
        self._validated()

    def _validated(self) -> dict[str, Any]:
        value = parse_json(self.canonical, MAX_OBSERVATION_BYTES)
        fields(
            value,
            "schema profile epoch request_id frame_id step_id move_sha256 "
            "baseline_identity_sha256 native_state identity_evidence "
            "acquisition image target",
        )
        constant(value["schema"], 1)
        constant(value["profile"], PROFILE)
        epoch(value["epoch"])
        step = integer(value["step_id"], 1, 7)
        constant(value["frame_id"], step)
        constant(value["request_id"], step * 2 + 1)
        digest_hex(value["move_sha256"])
        digest_hex(value["baseline_identity_sha256"])
        state = validate_native_state(value["native_state"], FRAME_LINES[step - 1])
        target = validate_target(value["target"])
        native = state["native"]
        if (target.pid, target.process_created_100ns, target.hwnd) != (
            native["pid"],
            native["creation"],
            native["root"],
        ):
            raise ProtocolError("observation-target-native-binding")
        if (
            not CLIENT_SCREEN[0] <= target.point[0] < CLIENT_SCREEN[2]
            or not CLIENT_SCREEN[1] <= target.point[1] < CLIENT_SCREEN[3]
        ):
            raise ProtocolError("observation-target-point-outside-client")
        identity = validate_identity_evidence(
            value["identity_evidence"], state["native"]
        )
        if (
            baseline_identity_sha256(state, identity)
            != value["baseline_identity_sha256"]
        ):
            raise ProtocolError("observation-baseline-identity-mismatch")
        acquisition = fields(
            value["acquisition"], "started_ns finished_ns samples quiet_ns"
        )
        constant(acquisition["samples"], 3)
        started = integer(acquisition["started_ns"], 0, MAX_STAMP)
        finished = integer(acquisition["finished_ns"], 0, MAX_STAMP)
        quiet = integer(acquisition["quiet_ns"], 200_000_000, 2_000_000_000)
        if not started + quiet <= finished < started + 2_000_000_000:
            raise ProtocolError("observation-acquisition-time")
        image = fields(value["image"], "origin width height stride byte_count sha256")
        for name, expected in (
            ("width", WIDTH),
            ("height", HEIGHT),
            ("stride", STRIDE),
            ("byte_count", IMAGE_BYTES),
        ):
            constant(image[name], expected)
        for actual, expected in zip(
            array(image["origin"], 2, 2), CLIENT_SCREEN[:2], strict=True
        ):
            constant(actual, expected)
        digest_hex(image["sha256"])
        return value

    @classmethod
    def parse(cls, data: bytes) -> EditObservation:
        """Reject instead of normalizing untrusted metadata."""
        return cls(data)

    @classmethod
    def from_value(cls, value: object) -> EditObservation:
        """Freeze producer data at the publication boundary."""
        return cls(canonical_json(value, MAX_OBSERVATION_BYTES))

    @property
    def value(self) -> dict[str, Any]:
        """Return detached validated metadata; mutable edits never change evidence."""
        return self._validated()

    @property
    def digest(self) -> bytes:
        """Hash exact validated observation bytes."""
        return hashlib.sha256(self.canonical).digest()

    @property
    def target(self) -> ObserveTarget:
        """Return the exact original pinned descriptor, including selected point."""
        return validate_target(self.value["target"])

    def frame_identity(self) -> FrameIdentity:
        """Bind the complete observation to unchanged 128-byte RGB24 headers."""
        value = self.value
        return FrameIdentity(
            bytes.fromhex(value["epoch"]),
            value["request_id"],
            value["frame_id"],
            value["image"]["origin"][0],
            value["image"]["origin"][1],
            WIDTH,
            HEIGHT,
            self.digest,
        )


def validate_continuity(initial: EditObservation, current: EditObservation) -> None:
    """Compare all invariants and exact state for repeated/returned positions."""
    before, after = initial.value, current.value
    if (
        before["epoch"] != after["epoch"]
        or before["target"] != after["target"]
        or canonical_json(
            invariant_identity(before["native_state"], before["identity_evidence"])
        )
        != canonical_json(
            invariant_identity(after["native_state"], after["identity_evidence"])
        )
    ):
        raise ProtocolError("moving-identity-continuity")
    if before["native_state"]["text_layout"]["first_visible_line"] == after[
        "native_state"
    ]["text_layout"]["first_visible_line"] and canonical_json(
        before["native_state"]
    ) != canonical_json(after["native_state"]):
        raise ProtocolError("moving-repeated-state-changed")
