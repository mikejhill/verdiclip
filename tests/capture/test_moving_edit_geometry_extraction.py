"""Portable tests for the independent own-client admission boundary."""

from dataclasses import replace
from typing import Any

import pytest

from verdiclip.capture.edit_geometry import Budget, ClientAdmissionError, ClientEvidence


def evidence() -> ClientEvidence:
    """Build independent native-only evidence."""
    return ClientEvidence(
        20,
        10,
        30,
        40,
        20,
        30,
        bool(1),
        10,
        "Edit",
        0x503008C4,
        0,
        96,
        (0, 0, 80, 60),
        (11, 11, 91, 71),
        (10, 10, 110, 90),
        (0, 0, 200, 200),
        (11, 71, 91, 89),
        (91, 11, 109, 71),
    )


def test_retains_entire_client_without_oracle() -> None:
    """Keep every margin and client pixel."""
    assert evidence().admit() == (11, 11, 91, 71)


@pytest.mark.parametrize(
    "change",
    [
        {"uia_hwnd": 10},
        {"hwnd": 10},
        {"same_element": False},
        {"parent": 99},
        {"uia_pid": 99},
        {"creation": 0},
        {"dpi": 120},
        {"ex_style": 0x400000},
        {"class_name": "RichEdit"},
        {"horizontal_bar": (11, 70, 91, 89)},
        {"client_screen": (11.5, 11, 91, 71)},
        {"client_local": (1, 0, 80, 60)},
        {"root_client": (0, 0, 90, 90)},
    ],
)
def test_rejects_ambiguous_identity_geometry(change: dict[str, Any]) -> None:
    """Reject native identity or geometric ambiguity."""
    with pytest.raises(
        ValueError, match=r"Invalid|Not the selected|Unsupported|Client mapping"
    ):
        replace(evidence(), **change).admit()


def test_phase_order_and_exact_boundary_latch() -> None:
    """Never admit at or after the phase deadline."""
    tick = [0.0]
    budget = Budget(lambda: tick[0])
    budget.enter("prepare")
    tick[0] = 10.0
    with pytest.raises(RuntimeError, match="deadline"):
        budget.enter("entry")
    tick[0] = 0.0
    with pytest.raises(RuntimeError, match="deadline"):
        budget.check()


def test_no_phase_restart() -> None:
    """Never extend a phase by entering it again."""
    budget = Budget(lambda: 0.0)
    budget.enter("prepare")
    with pytest.raises(RuntimeError, match="repeated"):
        budget.enter("prepare")


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        *[
            ({name: 0}, f"identity.{name}")
            for name in (
                "hwnd",
                "root",
                "pid",
                "creation",
                "uia_hwnd",
                "uia_pid",
                "parent",
            )
        ],
        ({"hwnd": 10}, "association.root_substitution"),
        ({"uia_hwnd": 99}, "association.uia_hwnd"),
        ({"uia_pid": 99}, "association.uia_pid"),
        ({"same_element": False}, "association.compare_elements"),
        ({"parent": 99}, "association.parent"),
        ({"class_name": "Other"}, "class_name"),
        ({"dpi": 120}, "dpi"),
        ({"style": None}, "style.type"),
        ({"style": 0}, "style.required_bits"),
        ({"style": 0x50B008C5}, "style.alignment"),
        ({"style": 0x50B008C4}, "style.unexpected_bits"),
        ({"ex_style": 0x200}, "ex_style.unexpected_bits"),
        ({"ex_style": None}, "ex_style.type"),
        ({"ex_style": 0x400000}, "ex_style.forbidden_bits"),
        *[
            ({name: (0, 0, 0, 0)}, f"rectangle.{name}")
            for name in (
                "client_local",
                "client_screen",
                "window",
                "root_client",
                "horizontal_bar",
                "vertical_bar",
            )
        ],
        ({"client_local": (1, 0, 80, 60)}, "mapping.local_origin"),
        ({"client_local": (0, 0, 79, 60)}, "mapping.size"),
        ({"window": (12, 10, 110, 90)}, "mapping.client_outside_window"),
        ({"root_client": (0, 0, 100, 100)}, "mapping.window_outside_root"),
        ({"horizontal_bar": (0, 71, 91, 89)}, "bar.horizontal_outside_window"),
        ({"vertical_bar": (91, 11, 111, 71)}, "bar.vertical_outside_window"),
        ({"horizontal_bar": (11, 70, 91, 89)}, "bar.horizontal_overlaps_client"),
        ({"vertical_bar": (90, 11, 109, 71)}, "bar.vertical_overlaps_client"),
    ],
)
def test_existing_admission_predicates_retain_reason(
    change: dict[str, Any], reason: str
) -> None:
    """Every original gate still rejects, with immutable actual observations."""
    observed = replace(evidence(), **change)
    with pytest.raises(ClientAdmissionError) as caught:
        observed.admit()
    assert caught.value.evidence is observed
    assert caught.value.reason == reason
    diagnostic = caught.value.diagnostic()
    assert diagnostic["reason"] == reason
    assert diagnostic["required_style"] == 0x503008C4
    if type(observed.style) is int:
        styles = diagnostic["styles"]
        assert isinstance(styles, dict)
        assert styles["style"]["value"] == observed.style
        assert styles["style"]["hex"] == hex(observed.style)


@pytest.mark.parametrize("bit", range(32))
def test_v2_rejects_every_style_bit_difference(bit: int) -> None:
    """The owned v2 contract is exact, not a required-mask fallback."""
    with pytest.raises(ClientAdmissionError, match="Unsupported EDIT styles"):
        replace(evidence(), style=0x503008C4 ^ (1 << bit)).admit()


@pytest.mark.parametrize("bit", range(32))
def test_v2_rejects_every_extended_style_bit(bit: int) -> None:
    """All extended variants, including CLIENTEDGE, remain unqualified."""
    with pytest.raises(ClientAdmissionError, match="Unsupported EDIT styles"):
        replace(evidence(), ex_style=1 << bit).admit()
