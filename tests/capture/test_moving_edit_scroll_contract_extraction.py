"""Frozen sequence and restoration admission, without native execution."""

from typing import Any

import pytest

from verdiclip.capture.edit_scroll_contract import (
    ASSEMBLY_LINES,
    EDIT_COMMON_STYLE,
    FIXTURE_CONTRACT,
    FONT_ARGS,
    FONT_ATTESTATION_SCHEMA,
    FONT_METRIC_BYTE_FIELDS,
    FONT_METRIC_LONG_FIELDS,
    FONT_METRIC_WORD_FIELDS,
    FRAME_LINES,
    ORACLE_STYLE,
    TARGET_STYLE,
    TEXT,
    WS_HSCROLL,
    WS_VSCROLL,
    SweepClock,
    assemble,
    character_point,
    phase_deadline,
    validate_font_attestation,
    validate_target_setup,
)
from verdiclip.capture.scroll_stitch import ScrollFrame


def test_authored_text_and_sequence() -> None:
    """Test authored text and sequence."""
    lines = TEXT.split("\r\n")
    assert len(lines) == 38
    assert all(len(line) == 43 for line in lines)
    assert not TEXT.endswith("\r\n")
    assert FRAME_LINES == (3, 0, 8, 13, 13, 0, 3)
    assert ASSEMBLY_LINES == (0, 8, 13)


@pytest.mark.parametrize(("now", "expected"), [(1.0, 3.0), (11.0, 12.0)])
def test_phase_uses_actual_entry_and_absolute_cap(now: float, expected: float) -> None:
    """Test phase uses actual entry and absolute cap."""
    assert phase_deadline(0.0, now, 2.0, 12.0) == expected


def test_normal_write_reserves_eight_seconds() -> None:
    """Test normal write reserves eight seconds."""
    now = [0.0]
    clock = SweepClock(30.0, lambda: now[0])
    now[0] = 21.999
    clock.admit_write(restoring=False)
    now[0] = 22.0
    with pytest.raises(ValueError, match="reserve"):
        clock.admit_write(restoring=False)


def test_restore_exact_reserve_and_only_once() -> None:
    """Test restore exact reserve and only once."""
    now = [0.0]
    clock = SweepClock(30.0, lambda: now[0])
    now[0] = 22.0
    clock.begin_restore()
    now[0] = 23.9  # Fresh guards consume the existing two-second subphase.
    clock.admit_write(restoring=True)
    with pytest.raises(ValueError, match="once"):
        clock.admit_write(restoring=True)


@pytest.mark.parametrize(
    "reason", ["timeout", "input", "identity", "unknown-write", "cleanup"]
)
def test_unsafe_failure_prohibits_restore(reason: str) -> None:
    """Test unsafe failure prohibits restore."""
    clock = SweepClock(30.0, lambda: 0.0)
    clock.prohibit(reason)
    with pytest.raises(ValueError, match=reason):
        clock.admit_write(restoring=True)


def test_regression_latches_even_if_clock_recovers() -> None:
    """Test regression latches even if clock recovers."""
    now = [1.0]
    clock = SweepClock(30.0, lambda: now[0])
    now[0] = 0.0
    with pytest.raises(ValueError, match="clock"):
        clock.check()
    now[0] = 2.0
    with pytest.raises(ValueError, match="clock"):
        clock.admit_write(restoring=True)


@pytest.mark.parametrize(
    ("raw", "expected"), [(0, (0, 0)), (0xFFF00002, (2, -16)), (0x0002FFFE, (-2, 2))]
)
def test_documented_packed_signed_coordinates(
    raw: int, expected: tuple[int, int]
) -> None:
    """Test documented packed signed coordinates."""
    assert character_point(raw) == expected


@pytest.mark.parametrize("raw", [-1, 0xFFFFFFFF, True, 1.5, 1 << 64])
def test_invalid_character_result_rejected(raw: object) -> None:
    """Test invalid character result rejected."""
    with pytest.raises(ValueError, match="Invalid"):
        character_point(raw)


def test_three_frame_exact_assembly_and_row_provenance() -> None:
    """Test three frame exact assembly and row provenance."""
    stride = 493 * 3
    rows = [bytes([i % 256, i // 256, 71]) * 493 for i in range(621)]
    frames = tuple(
        ScrollFrame(493, 413, b"".join(rows[start : start + 413]))
        for start in (0, 128, 208)
    )
    output, provenance = assemble(frames, (128, 80))
    assert output.rgb == b"".join(rows)
    assert output.height == 621
    assert len(provenance) == 621
    for row, (frame, source_row) in enumerate(provenance):
        assert (
            output.rgb[row * stride : (row + 1) * stride]
            == frames[frame].rgb[source_row * stride : (source_row + 1) * stride]
        )


@pytest.mark.parametrize("delta", [(127, 80), (128, 81), (207, 80), (True, 80)])
def test_native_displacement_is_not_fitted(delta: tuple[int, int]) -> None:
    """Test native displacement is not fitted."""
    frames = (ScrollFrame(493, 413, bytes(493 * 413 * 3)),) * 3
    with pytest.raises(ValueError, match="Invalid"):
        assemble(frames, delta)


def test_blank_content_rejected_despite_expected_native_displacement() -> None:
    """Test blank content rejected despite expected native displacement."""
    frames = (ScrollFrame(493, 413, bytes(493 * 413 * 3)),) * 3
    with pytest.raises(ValueError, match="ambiguous"):
        assemble(frames, (128, 80))


def test_scroll_axis_bits_match_documented_winuser_constants() -> None:
    """Orthogonal SDK-bit assertions catch a self-consistent wrong literal."""
    assert WS_HSCROLL == 0x00100000
    assert WS_VSCROLL == 0x00200000
    assert TARGET_STYLE & 0x00200000 != 0
    assert TARGET_STYLE & 0x00100000 == 0
    assert ORACLE_STYLE & 0x00300000 == 0
    assert TARGET_STYLE & ~0x00300000 == EDIT_COMMON_STYLE == ORACLE_STYLE
    assert TARGET_STYLE == 0x502008C4


FONT_OWNER: dict[str, Any] = {
    "epoch": "a" * 32,
    "pid": 17,
    "creation": 999,
    "role": "target",
    "root": 101,
    "child": 102,
}


def _font_attestation() -> dict[str, Any]:
    metrics = dict.fromkeys(
        FONT_METRIC_LONG_FIELDS + FONT_METRIC_WORD_FIELDS + FONT_METRIC_BYTE_FIELDS,
        0,
    )
    metrics.update(height=16, average_width=8, maximum_width=10, pitch_family=54)
    return {
        "schema": FONT_ATTESTATION_SCHEMA,
        "fixture_contract": FIXTURE_CONTRACT,
        **FONT_OWNER,
        "opaque_hfont": 777,
        "source_authored_createfont": list(FONT_ARGS),
        "owner_measured_face": "Courier New",
        "owner_measured_metrics": metrics,
    }


def test_owner_attestation_is_detached_and_preserves_8_10_metrics() -> None:
    """Owner measurements are distinct from authored arguments and opaque token."""
    receipt = _font_attestation()
    result = validate_font_attestation(receipt, **FONT_OWNER)
    assert result == receipt
    assert result is not receipt
    assert result["owner_measured_metrics"]["average_width"] == 8
    assert result["owner_measured_metrics"]["maximum_width"] == 10
    receipt["owner_measured_metrics"]["average_width"] = 10
    receipt["source_authored_createfont"][0] = 18
    assert result["owner_measured_metrics"]["average_width"] == 8
    assert result["source_authored_createfont"] == list(FONT_ARGS)
    assert "observed_logfont" not in result


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema", "another-schema"),
        ("fixture_contract", "mutable-font-fixture"),
        ("fixture_contract", "builtin-edit-owned-scroll-v1"),
        ("epoch", "b" * 32),
        ("epoch", 1),
        ("pid", 18),
        ("pid", True),
        ("creation", 998),
        ("creation", 999.0),
        ("role", "oracle"),
        ("root", 102),
        ("child", 103),
        ("owner_measured_face", "Arial"),
        ("opaque_hfont", 0),
        ("opaque_hfont", -1),
        ("opaque_hfont", True),
        ("opaque_hfont", 1 << 64),
        ("opaque_hfont", 777.0),
        ("source_authored_createfont", tuple(FONT_ARGS)),
        ("source_authored_createfont", list(FONT_ARGS[:-1])),
        ("owner_measured_metrics", None),
    ],
)
def test_font_attestation_rejects_stale_or_malformed_owner_data(
    field: str, value: object
) -> None:
    """No copied old epoch/role/owner/HWND receipt may admit a new target."""
    receipt = _font_attestation()
    receipt[field] = value
    with pytest.raises(ValueError, match=r"[Ff]ont"):
        validate_font_attestation(receipt, **FONT_OWNER)


@pytest.mark.parametrize("field", tuple(_font_attestation()))
def test_font_attestation_requires_every_exact_field(field: str) -> None:
    """Omission never falls back to a helper font query or substitute."""
    receipt = _font_attestation()
    del receipt[field]
    with pytest.raises(ValueError, match="fields"):
        validate_font_attestation(receipt, **FONT_OWNER)


def test_font_attestation_rejects_added_provenance_claim() -> None:
    """Even an extra invented observed LOGFONT field is outside this schema."""
    receipt = _font_attestation()
    receipt["observed_logfont"] = list(FONT_ARGS)
    with pytest.raises(ValueError, match="fields"):
        validate_font_attestation(receipt, **FONT_OWNER)


@pytest.mark.parametrize("index", range(len(FONT_ARGS)))
def test_source_authored_createfont_is_strict_and_unchanged(index: int) -> None:
    """Exact source inputs reject bool/int coercion and altered font requests."""
    receipt = _font_attestation()
    receipt["source_authored_createfont"][index] = False if index < 13 else "Consolas"
    with pytest.raises(ValueError, match="source-authored"):
        validate_font_attestation(receipt, **FONT_OWNER)


@pytest.mark.parametrize(
    "field", FONT_METRIC_LONG_FIELDS + FONT_METRIC_WORD_FIELDS + FONT_METRIC_BYTE_FIELDS
)
@pytest.mark.parametrize("invalid", [True, 1.0, "1", None])
def test_owner_measured_metrics_have_strict_integer_abi(
    field: str, invalid: object
) -> None:
    """Every scalar is a genuine native-width integer, never inferred/coerced."""
    receipt = _font_attestation()
    receipt["owner_measured_metrics"][field] = invalid
    with pytest.raises(ValueError, match="metric"):
        validate_font_attestation(receipt, **FONT_OWNER)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ascent", -(1 << 31) - 1),
        ("maximum_width", 1 << 31),
        ("first_character", -1),
        ("last_character", 1 << 16),
        ("italic", -1),
        ("charset", 1 << 8),
        ("height", 17),
        ("external_leading", 1),
        ("average_width", 0),
        ("average_width", 12),
        ("pitch_family", 55),
    ],
)
def test_owner_metrics_preserve_bounds_and_frozen_predicates(
    field: str, value: int
) -> None:
    """Receipt validation applies existing owner predicates without new fitting."""
    receipt = _font_attestation()
    receipt["owner_measured_metrics"][field] = value
    with pytest.raises(ValueError, match=r"font|metric"):
        validate_font_attestation(receipt, **FONT_OWNER)


@pytest.mark.parametrize("change", ["missing", "extra"])
def test_owner_metrics_require_exact_existing_textmetric_fields(change: str) -> None:
    """Abbreviated helper fields or omitted owner fields cannot stand in."""
    receipt = _font_attestation()
    if change == "missing":
        del receipt["owner_measured_metrics"]["first_character"]
    else:
        receipt["owner_measured_metrics"]["first"] = 0
    with pytest.raises(ValueError, match="fields"):
        validate_font_attestation(receipt, **FONT_OWNER)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("epoch", "x" * 65),
        ("epoch", ""),
        ("epoch", "bad/epoch"),
        ("role", "other"),
        ("pid", 0),
        ("pid", 1 << 32),
        ("pid", True),
        ("creation", 0),
        ("creation", 1 << 64),
        ("root", -1),
        ("child", 0),
        ("child", 101),
    ],
)
def test_expected_font_binding_is_itself_strict(field: str, value: object) -> None:
    """Supplying equally malformed expected values cannot validate a receipt."""
    receipt = _font_attestation()
    expected: dict[str, Any] = dict(FONT_OWNER)
    receipt[field] = expected[field] = value
    with pytest.raises(ValueError, match=r"[Ff]ont"):
        validate_font_attestation(receipt, **expected)


def test_oracle_attestation_requires_explicit_oracle_binding() -> None:
    """Oracle and target roles are distinct even with the same font handle."""
    receipt = _font_attestation()
    receipt["role"] = "oracle"
    expected: dict[str, Any] = {**FONT_OWNER, "role": "oracle"}
    assert validate_font_attestation(receipt, **expected)["role"] == "oracle"
    with pytest.raises(ValueError, match="binding"):
        validate_font_attestation(receipt, **FONT_OWNER)


def focused_receipt(identity: dict[str, Any], tick: int = 7) -> dict[str, Any]:
    """Fixed owner receipt, not a replacement for native setup."""
    return {
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
        "before_input_tick": tick,
        "after_input_tick": tick,
        "completed_monotonic": 100.0,
        "target_setup": {
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
        },
    }


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        (
            "outer",
            "fixture_contract",
            "builtin-edit-owned-scroll-hpercent-positive-zero-v2",
        ),
        ("outer", "fixture_pid", True),
        ("outer", "creation", True),
        ("outer", "unexpected", "extra"),
        ("outer", "root", 99),
        ("outer", "child", 99),
        ("outer", "after_input_tick", 8),
        ("outer", "before_input_tick", True),
        ("outer", "completed_monotonic", float("nan")),
        ("inner", "prior_focus", 11),
        ("inner", "focus", 10),
        ("inner", "caret", 0),
        ("inner", "flags", 1),
        ("inner", "capture", 1),
        ("inner", "menu_owner", 1),
        ("inner", "move_size", 1),
        ("inner", "hide_caret", 1),
        ("inner", "flags", False),
        ("inner", "after_input_tick", 8),
    ],
)
def test_focused_setup_strict_bindings(section: str, key: str, value: object) -> None:
    """No old profile, bool alias, focus alternate, or input rebaseline."""
    identity = {"epoch": "epoch", "pid": 42, "creation": 123, "root": 10, "child": 11}
    receipt = focused_receipt(identity)
    accepted = validate_target_setup(receipt, identity)
    assert accepted == receipt
    assert accepted["target_setup"] is not receipt["target_setup"]
    (receipt if section == "outer" else receipt["target_setup"])[key] = value
    with pytest.raises(ValueError, match=r"[Ff]ocused-child"):
        validate_target_setup(receipt, identity)
