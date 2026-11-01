"""Synthetic live Python frames only; never import Qt or application modules."""

from __future__ import annotations

import sys
from types import FrameType
from typing import cast, override

import pytest
from scripts.retained_overlay_signal_observer import (
    DECISION_FUNCTIONS,
    FocusProbe,
    input_owner_decision,
)

ADMITTED = "/retained/mutants/src/verdiclip/capture/service.py"


def observe(
    function: str,
    filename: str = ADMITTED,
    statements: str | None = None,
    probe: FocusProbe | None = None,
) -> list[dict[str, object] | None]:
    """Compile fixed synthetic storage and inspect its live optimized frame."""
    result: list[dict[str, object] | None] = []

    def callback(frame: FrameType) -> None:
        result.append(input_owner_decision(frame, ADMITTED, probe))

    hostile = Hostile()
    namespace: dict[str, object] = {
        "callback": callback,
        "getframe": sys._getframe,
        "hostile": hostile,
    }
    body = statements or (
        "owns_focus = False\n"
        "lost_focus = True\n"
        "owns_grabs = True\n"
        "focus = hostile\n"
        "active = None\n"
        "unrelated_secret = hostile\n"
    )
    source = (
        f"def {function}():\n"
        + "".join("    " + line + "\n" for line in body.splitlines())
        + "    callback(getframe())\n"
        + f"{function}()\n"
    )
    exec(compile(source, filename, "exec"), namespace)  # noqa: S102 - fixed pure frame fixture
    assert not hostile.attempts
    return result


class Hostile:
    """No truthiness, formatting, property or comparison may be invoked."""

    __hash__ = object.__hash__

    def __init__(self) -> None:
        """Retain attempted inspections without raising from special methods."""
        self.attempts: list[str] = []

    def __bool__(self) -> bool:
        """Record any coercion without changing exception control flow."""
        self.attempts.append("truthiness")
        return False

    @override
    def __repr__(self) -> str:
        """Record any formatting without changing exception control flow."""
        self.attempts.append("repr")
        return "hostile"

    @override
    def __eq__(self, _other: object) -> bool:
        """Record any comparison without changing exception control flow."""
        self.attempts.append("comparison")
        return False


@pytest.mark.parametrize("function", sorted(DECISION_FUNCTIONS))
def test_exact_plain_and_mutmut_frames_copy_only_precomputed_values(
    function: str,
) -> None:
    """Both observed exact names retain the same bounded primitive schema."""
    assert observe(function) == [
        {
            "owns_focus": False,
            "lost_focus": True,
            "owns_grabs": True,
            "focus_present": True,
            "active_present": False,
        }
    ]


@pytest.mark.parametrize(
    ("function", "filename"),
    [
        ("_check_input_owner", "/foreign/service.py"),
        ("_on_cancel", ADMITTED),
        ("xǁCaptureServiceǁ_check_input_owner__mutmut_1", ADMITTED),
        ("_check_input_owner_extra", ADMITTED),
    ],
)
def test_nonallowlisted_frame_is_not_inspected(function: str, filename: str) -> None:
    """Near names and foreign code cannot admit decision locals."""
    assert observe(function, filename) == [None]


def test_missing_and_nonboolean_decision_values_remain_unknown() -> None:
    """Unknown values never become fabricated true/false branch evidence."""
    assert observe(
        "_check_input_owner",
        statements="owns_focus = hostile\nlost_focus = 0\nowns_grabs = 1\n",
    ) == [
        {
            "owns_focus": None,
            "lost_focus": None,
            "owns_grabs": None,
            "focus_present": None,
            "active_present": None,
        }
    ]


def test_real_optimized_frame_proxy_is_accepted_without_exact_dict_requirement() -> (
    None
):
    """CPython3.13 f_locals proxy is supported using fixed-key reads only."""
    assert type(sys._getframe().f_locals) is not dict
    assert observe("_check_input_owner")[0] is not None


def test_nonframe_is_rejected_before_any_attribute_access() -> None:
    """A hostile fake cannot emulate a frame and expose dynamic properties."""
    hostile = Hostile()
    assert input_owner_decision(hostile, ADMITTED) is None
    assert not hostile.attempts


def test_focus_match_receives_only_exact_precomputed_token() -> None:
    """Identity matching uses the existing local; no Qt query is recomputed."""

    class Probe:
        def match(self, focus: object | None) -> dict[str, object]:
            assert type(focus) is Hostile
            return {"identity_match_only": True}

    result = observe("_check_input_owner", probe=cast(FocusProbe, Probe()))[0]
    assert result is not None
    assert result["predecessor_focus_identity"] == {"identity_match_only": True}


def test_missing_focus_local_cannot_become_a_negative_identity_match() -> None:
    """Unavailable frame data remains unknown rather than inventing None focus."""

    class Probe:
        def match(self, _focus: object | None) -> dict[str, object]:
            pytest.fail("Missing focus must not call the identity matcher")

    result = observe(
        "_check_input_owner",
        statements="owns_focus = False",
        probe=cast(FocusProbe, Probe()),
    )[0]
    assert result is not None
    assert result["focus_present"] is None
    assert "predecessor_focus_identity" not in result
