"""Frozen owned EDIT scroll research contract; unused by the application."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from typing import Any, cast

from verdiclip.capture.scroll_stitch import ScrollFrame, StitchedImage

BASE = "6381368301d2e1d648c5044e4d8ff6eb2acc04a1"
# Official Winuser.h: https://learn.microsoft.com/en-us/windows/win32/winmsg/window-styles
WS_HSCROLL = 0x00100000
WS_VSCROLL = 0x00200000
EDIT_COMMON_STYLE = 0x500008C4
TARGET_STYLE = EDIT_COMMON_STYLE | WS_VSCROLL
ORACLE_STYLE = EDIT_COMMON_STYLE
FRAME_LINES = (3, 0, 8, 13, 13, 0, 3)
ASSEMBLY_LINES = (0, 8, 13)
TEXT = "\r\n".join(
    f"{i:02d}|" + "".join(chr(65 + (i * 7 + j * 11) % 26) for j in range(36)) + "|END"
    for i in range(38)
)
# Diagnostic resource capacity only, not a native/time/correctness relaxation.
JOURNAL_PROFILE = "owned-edit-logical-records-32768-v2"
JOURNAL_MAX_RECORDS = 32768
JOURNAL_MAX_BYTES = 4_000_000
JOURNAL_MAX_RECORD_BYTES = 4096
JOURNAL_MAX_DEPTH = 32
RESTORE_SECONDS = 8
SWEEP_SECONDS = 30
UINT64_MAX = (1 << 64) - 1
DWORD_MAX = (1 << 32) - 1
SIGNED16 = 1 << 15
WORD = 1 << 16


FONT_ARGS = (16, 0, 0, 0, 400, 0, 0, 0, 0, 4, 0, 3, 0x31, "Courier New")
FONT_ATTESTATION_SCHEMA = "owned-edit-scroll-font-attestation-v1"
# Explicit prospective owned-provider profile, never a generic UIA fallback.
# Its raw horizontal observation is +0.0; no-write commands remain NoScroll(-1).
FIXTURE_CONTRACT = "builtin-edit-owned-scroll-focused-hidden-caret-v3"
FONT_ATTESTATION_MAX_BYTES = 8192
FONT_CELL_HEIGHT = 16
FONT_LINE_CHARACTERS = 43
FONT_CLIENT_WIDTH = 493
FONT_METRIC_LONG_FIELDS = (
    "height",
    "ascent",
    "descent",
    "internal_leading",
    "external_leading",
    "average_width",
    "maximum_width",
    "weight",
    "overhang",
    "aspect_x",
    "aspect_y",
)
FONT_METRIC_WORD_FIELDS = (
    "first_character",
    "last_character",
    "default_character",
    "break_character",
)
FONT_METRIC_BYTE_FIELDS = (
    "italic",
    "underlined",
    "struck_out",
    "pitch_family",
    "charset",
)
_FONT_ATTESTATION_KEYS = frozenset(
    (
        "schema",
        "fixture_contract",
        "epoch",
        "pid",
        "creation",
        "role",
        "root",
        "child",
        "opaque_hfont",
        "source_authored_createfont",
        "owner_measured_face",
        "owner_measured_metrics",
    )
)


def _font_identity_integer(value: object, maximum: int) -> None:
    if type(value) is not int or not 0 < value <= maximum:
        raise ValueError("Invalid font attestation owner identity")


def _validate_owner_metrics(value: object) -> None:
    fields = FONT_METRIC_LONG_FIELDS + FONT_METRIC_WORD_FIELDS + FONT_METRIC_BYTE_FIELDS
    if (
        type(value) is not dict
        or any(type(key) is not str for key in value)
        or set(value) != set(fields)
    ):
        raise ValueError("Invalid owner-measured font metric fields")
    value = cast("dict[str, Any]", value)
    for names, low, high in (
        (FONT_METRIC_LONG_FIELDS, -(1 << 31), (1 << 31) - 1),
        (FONT_METRIC_WORD_FIELDS, 0, (1 << 16) - 1),
        (FONT_METRIC_BYTE_FIELDS, 0, (1 << 8) - 1),
    ):
        for name in names:
            if type(value[name]) is not int or not low <= value[name] <= high:
                raise ValueError("Invalid bounded owner-measured font metric")
    # These are exactly the frozen owner's predicates, not a new fitted model.
    # In particular maximum_width need not equal the character advance.
    if (
        value["height"] != FONT_CELL_HEIGHT
        or value["external_leading"] != 0
        or value["average_width"] <= 0
        or value["pitch_family"] & 1
        or FONT_LINE_CHARACTERS * value["average_width"] > FONT_CLIENT_WIDTH
    ):
        raise ValueError("Owner-measured font predicates disagree")


def validate_font_attestation(  # noqa: PLR0913 - every pinned ownership component is mandatory
    value: object,
    *,
    epoch: str,
    pid: int,
    creation: int,
    role: str,
    root: int,
    child: int,
) -> dict[str, Any]:
    """Validate owner evidence for this immutable fixture, not generic provenance.

    GDI handles are private to their creating process. The handle in this
    receipt is only an opaque continuity token; no consumer may use it in GDI.
    Font arguments are source-authored inputs, not an observed LOGFONT. This
    attestation depends on the fixed owner's no-mutation lifetime and does not
    establish generic cross-process font identity or defeat handle reuse/ABA.
    """
    if (
        type(epoch) is not str
        or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", epoch) is None
        or type(role) is not str
        or role not in ("target", "oracle")
    ):
        raise ValueError("Invalid font attestation epoch/role")
    _font_identity_integer(pid, DWORD_MAX)
    for identity in (creation, root, child):
        _font_identity_integer(identity, UINT64_MAX)
    if root == child:
        raise ValueError("Invalid font attestation root/child association")
    if (
        type(value) is not dict
        or any(type(key) is not str for key in value)
        or set(value) != _FONT_ATTESTATION_KEYS
    ):
        raise ValueError("Invalid font attestation fields")
    value = cast("dict[str, Any]", value)
    expected = {
        "schema": FONT_ATTESTATION_SCHEMA,
        "fixture_contract": FIXTURE_CONTRACT,
        "epoch": epoch,
        "pid": pid,
        "creation": creation,
        "role": role,
        "root": root,
        "child": child,
        "owner_measured_face": "Courier New",
    }
    if any(
        type(value[name]) is not type(want) or value[name] != want
        for name, want in expected.items()
    ):
        raise ValueError("Font attestation contract/epoch/owner binding changed")
    _font_identity_integer(value["opaque_hfont"], UINT64_MAX)
    authored = value["source_authored_createfont"]
    if (
        type(authored) is not list
        or len(authored) != len(FONT_ARGS)
        or any(
            type(actual) is not type(want) or actual != want
            for actual, want in zip(authored, FONT_ARGS, strict=True)
        )
    ):
        raise ValueError("Font attestation source-authored CreateFont changed")
    _validate_owner_metrics(value["owner_measured_metrics"])
    encoded = json.dumps(value, allow_nan=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > FONT_ATTESTATION_MAX_BYTES:
        raise ValueError("Font attestation exceeds bounded receipt")
    return json.loads(encoded)


def finite(value: float) -> float:
    """Reject coercion and nonfinite clock readings."""
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("Invalid clock")
    return value


def phase_deadline(launch: float, entry: float, seconds: float, cap: float) -> float:
    """Actual-entry budget, never borrowed time or a scheduled-start wait."""
    launch, entry, seconds, cap = map(finite, (launch, entry, seconds, cap))
    deadline = min(entry + seconds, launch + cap)
    if entry < launch or seconds <= 0 or not entry < deadline:
        raise ValueError("Invalid phase clock")
    return deadline


def character_point(raw: object) -> tuple[int, int]:
    """Decode DWORD_PTR EM_POSFROMCHAR output, retaining signed16 coordinates."""
    if type(raw) is not int or not 0 <= raw <= UINT64_MAX:
        raise ValueError("Invalid character position")
    value = raw & DWORD_MAX
    if value == DWORD_MAX:
        raise ValueError("Invalid character position")
    x, y = value & (WORD - 1), value >> 16
    return (x - WORD if x >= SIGNED16 else x, y - WORD if y >= SIGNED16 else y)


class SweepClock:
    """Monotonic original budget and irreversible unsafe-restoration latch."""

    def __init__(self, deadline: float, clock: Callable[[], float]) -> None:
        self.clock = clock
        self.last = finite(clock())
        self.deadline = finite(deadline)
        if not self.last < deadline <= self.last + SWEEP_SECONDS:
            raise ValueError("Invalid sweep clock")
        self.prohibited: str | None = None
        self.restoration_attempted = False
        self.restoration_write = False

    def prohibit(self, reason: str) -> None:
        """Preserve the first reason that makes any further write unsafe."""
        if self.prohibited is None:
            self.prohibited = reason

    def check(self) -> float:
        """Latch ordinary callback/clock failures before more native work."""
        if self.prohibited is not None:
            raise ValueError(self.prohibited)
        try:
            now = finite(self.clock())
            if not self.last <= now < self.deadline:
                raise ValueError("Invalid or expired clock")  # noqa: TRY301 - frozen research constants and explicit failure-preserving lifecycle
        except Exception as error:
            self.prohibit(str(error) or type(error).__name__)
            raise ValueError(self.prohibited) from error
        self.last = now
        return now

    def begin_restore(self) -> None:
        """Consume the sole restoration attempt before its fresh guard phase."""
        now = self.check()
        if self.restoration_attempted:
            raise ValueError("Restoration is permitted once")
        if self.deadline - now < RESTORE_SECONDS:
            raise ValueError("Insufficient restoration reserve")
        self.restoration_attempted = True

    def admit_write(self, *, restoring: bool) -> None:
        """Normal writes reserve8s; restore dispatch consumes its reserved phase."""
        now = self.check()
        if type(restoring) is not bool:
            raise ValueError("Invalid restoring flag")
        if restoring:
            if not self.restoration_attempted or self.restoration_write:
                raise ValueError("Reserved restoration write is permitted once")
            self.restoration_write = True
        elif self.deadline - now <= RESTORE_SECONDS:
            raise ValueError("Insufficient restoration reserve")


def assemble(
    frames: tuple[ScrollFrame, ...],
    native_deltas: tuple[int, int],
) -> tuple[StitchedImage, tuple[tuple[int, int], ...]]:
    """Strict whole-overlap proof; expected pixels never participate in admission.

    The external producer must establish the independently observed native
    displacement before calling. Every output row records one source row;
    every column is retained unchanged, so row provenance covers all pixels.
    """
    if (
        type(frames) is not tuple
        or len(frames) != 3  # noqa: PLR2004 - frozen research constants and explicit failure-preserving lifecycle
        or any((frame.width, frame.height) != (493, 413) for frame in frames)
        or type(native_deltas) is not tuple
        or any(type(value) is not int for value in native_deltas)
        or native_deltas != (128, 80)
    ):
        raise ValueError("Invalid frozen frame/displacement evidence")
    stride = 493 * 3
    chunks = [frames[0].rgb]
    provenance = [(0, row) for row in range(413)]
    for index, delta in enumerate(native_deltas, 1):
        previous, current = frames[index - 1], frames[index]
        # Search is independent of the native delta; no hint chooses an overlap.
        candidates = [
            shift
            for shift in range(1, 207)
            if previous.rgb[shift * stride :] == current.rgb[: (413 - shift) * stride]
        ]
        if len(candidates) != 1 or previous.rgb == current.rgb:
            raise ValueError("Missing or ambiguous exact full-width overlap")
        if candidates[0] != delta:
            raise ValueError("Native and raster displacement disagree")
        overlap = 413 - delta
        seam = overlap // 2
        removed = overlap - seam
        chunks[-1] = chunks[-1][: -removed * stride]
        chunks.append(current.rgb[seam * stride :])
        del provenance[-removed:]
        provenance.extend((index, row) for row in range(seam, 413))
    return StitchedImage(493, 621, b"".join(chunks), 3), tuple(provenance)


def validate_target_setup(value: object, identity: dict[str, Any]) -> dict[str, Any]:
    """Validate owned setup evidence, never infer foreign caret authority."""
    expected = {
        "schema": "owned-edit-scroll-receipt-v1",
        "fixture_contract": FIXTURE_CONTRACT,
        "session_id": identity["epoch"],
        "sequence": 1,
        "action": "switch-to-target",
        "status": "completed",
        "fixture_pid": identity["pid"],
        "creation": identity["creation"],
        "root": identity["root"],
        "child": identity["child"],
        "phase": "target",
        "foreground": identity["root"],
        "focus": identity["root"],
    }
    if type(value) is not dict:
        raise ValueError("Missing focused-child setup receipt")
    value = cast("dict[str, Any]", value)
    if set(value) != set(expected) | {
        "before_input_tick",
        "after_input_tick",
        "completed_monotonic",
        "target_setup",
    }:
        raise ValueError("Invalid focused-child setup fields")
    for key, wanted in expected.items():
        if type(value.get(key)) is not type(wanted) or value[key] != wanted:
            raise ValueError(f"Focused-child setup binding: {key}")
    tick = value.get("before_input_tick")
    if (
        type(tick) is not int
        or not 0 <= tick <= DWORD_MAX
        or type(value.get("after_input_tick")) is not int
        or value["after_input_tick"] != tick
    ):
        raise ValueError("Focused-child setup input changed")
    nested = {
        "schema": "owned-edit-focused-hidden-caret-v1",
        "prior_focus": identity["root"],
        "focus": identity["child"],
        "caret": identity["child"],
        "active": identity["root"],
        "flags": 0,
        "capture": 0,
        "menu_owner": 0,
        "move_size": 0,
        "hide_caret": True,
        "before_input_tick": tick,
        "after_input_tick": tick,
    }
    setup = value.get("target_setup")
    if type(setup) is not dict or set(setup) != set(nested):
        raise ValueError("Invalid focused-child setup schema")
    setup = cast("dict[str, Any]", setup)
    for key, wanted in nested.items():
        if type(setup[key]) is not type(wanted) or setup[key] != wanted:
            raise ValueError(f"Focused-child setup state: {key}")
    stamp: Any = value.get("completed_monotonic")
    if type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp < 0:
        raise ValueError("Invalid focused-child setup timestamp")
    return {**value, "target_setup": dict(setup)}
