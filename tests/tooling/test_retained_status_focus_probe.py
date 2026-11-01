"""Fake ports only; never call the lazy Qt sampler or import application code."""

from __future__ import annotations

import ast
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast, override
from weakref import ref

import pytest
from scripts.retained_status_focus_probe import (
    MARKER,
    MAX_TOKENS,
    MAX_TOP_LEVELS,
    PREDECESSOR,
    Probe,
    admit_host,
    sample_from_ports,
)


class Token:
    """Record accidental coercion without raising from special methods."""

    def __init__(self) -> None:
        """Initialize pure attempted-inspection metadata."""
        self.attempts: list[str] = []

    def __bool__(self) -> bool:
        """Record truthiness."""
        self.attempts.append("bool")
        return False

    @override
    def __hash__(self) -> int:
        """Record hashing."""
        self.attempts.append("hash")
        return 17

    @override
    def __eq__(self, _other: object) -> bool:
        """Record value comparisons."""
        self.attempts.append("eq")
        return False

    @override
    def __repr__(self) -> str:
        """Record formatting."""
        self.attempts.append("repr")
        return "token"


@dataclass(eq=False)
class Widget:
    """An inert synthetic top-level object."""

    box: bool
    marker: str
    token: object | None


class Ports:
    """Explicit fake query calls; no Qt or live widgets exist."""

    def __init__(self, widgets: list[Widget]) -> None:
        """Retain fake inputs and count the narrow permitted getters."""
        self.widgets = widgets
        self.names = 0
        self.handles = 0

    def top_levels(self) -> Sequence[object]:
        """Return supplied inert objects."""
        return self.widgets

    def is_message_box(self, widget: object) -> bool:
        """Use only fake stored type admission."""
        return cast("Widget", widget).box

    def object_name(self, widget: object) -> str:
        """Count fixed marker getter queries."""
        self.names += 1
        return cast("Widget", widget).marker

    def window_token(self, widget: object) -> object | None:
        """Count queries only for correctly marked fake boxes."""
        self.handles += 1
        return cast("Widget", widget).token


def test_sampling_admits_only_exact_box_marker_and_existing_token() -> None:
    """Nonboxes and nonmatching boxes never reach the token getter."""
    token = Token()
    ports = Ports(
        [
            Widget(box=False, marker=MARKER, token=None),
            Widget(box=True, marker="other", token=None),
            Widget(box=True, marker=MARKER, token=token),
        ]
    )
    tokens = sample_from_ports(ports)
    assert len(tokens) == 1
    assert tokens[0] is token
    assert ports.names == 2
    assert ports.handles == 1
    probe = Probe()
    probe.register(tokens)
    assert probe.match(token)["manual_status_focus_match"] is True
    assert not token.attempts


def test_unsampled_empty_and_mismatched_identity_have_distinct_receipts() -> None:
    """Unknown sampling is not a known empty predecessor sample."""
    probe, token, other = Probe(), Token(), Token()
    assert probe.match(token)["manual_status_focus_match"] is None
    probe.register(())
    assert probe.match(token)["manual_status_sampling_valid"] is True
    assert probe.match(token)["manual_status_sample_empty"] is True
    assert probe.match(token)["manual_status_focus_match"] is None
    second = Probe()
    second.register((token,))
    assert second.match(other)["manual_status_focus_match"] is False
    assert second.match(None)["focus_present"] is False
    assert not token.attempts
    assert not other.attempts


def test_weak_identity_loss_is_unknown_without_extending_wrapper_lifetime() -> None:
    """A vanished canonical wrapper cannot justify a negative identity claim."""
    probe, token, other = Probe(), Token(), Token()
    weak = ref(token)
    probe.register((token,))
    del token
    assert weak() is None
    assert probe.match(other)["manual_status_focus_match"] is None
    assert probe.match(other)["manual_status_weak_identity_lost"] is True


def test_duplicate_registration_attempt_invalidates_sampling_without_replacing_it() -> (
    None
):
    """Sample exactly once at the admitted predecessor post-CALL boundary."""
    probe, token = Probe(), Token()
    probe.register((token,))
    with pytest.raises(RuntimeError, match="cannot be repeated"):
        probe.register((token,))
    assert probe.match(token)["manual_status_sampling_valid"] is False
    assert probe.match(token)["manual_status_focus_match"] is None


def test_identity_duplicates_need_no_hash_or_value_comparison() -> None:
    """Repeated canonical tokens are deduplicated by is only."""
    probe, token = Probe(), Token()
    probe.register((token, token))
    assert probe.match(token)["manual_status_focus_match"] is True
    assert not token.attempts


def test_invalid_admission_propagates_and_remains_unknown() -> None:
    """Unsupported identity tokens cannot fall back to handles or strong refs."""
    probe = Probe()
    with pytest.raises(TypeError):
        probe.register((object(),))
    assert probe.match(Token())["manual_status_sampling_valid"] is False
    none_probe = Probe()
    with pytest.raises(ValueError, match="Missing"):
        none_probe.register((None,))


def test_limits_are_bounded_and_do_not_silently_truncate_evidence() -> None:
    """Oversize samples fail rather than producing valid negative receipts."""
    with pytest.raises(OverflowError, match="top-level"):
        sample_from_ports(
            Ports([Widget(box=False, marker="", token=None)] * (MAX_TOP_LEVELS + 1))
        )
    token = Token()
    with pytest.raises(OverflowError, match="marked status"):
        sample_from_ports(
            Ports([Widget(box=True, marker=MARKER, token=token)] * (MAX_TOKENS + 1))
        )
    probe = Probe()
    with pytest.raises(ValueError, match="bounded"):
        probe.register((token,) * (MAX_TOKENS + 1))
    assert not token.attempts


def test_missing_existing_window_is_not_materialized_or_ignored() -> None:
    """No winId() fallback may create a native window during a read-only sample."""
    with pytest.raises(RuntimeError, match="no existing QWindow"):
        sample_from_ports(Ports([Widget(box=True, marker=MARKER, token=None)]))


def test_query_controls_propagate_unchanged_to_the_outer_observer() -> None:
    """The sampler owns no exception suppression or alternative Qt behavior."""
    control = KeyboardInterrupt("sample control")

    class Broken(Ports):
        @override
        def top_levels(self) -> Sequence[object]:
            raise control

    with pytest.raises(KeyboardInterrupt) as caught:
        sample_from_ports(Broken([]))
    assert caught.value is control


def test_host_admission_is_pure_and_requires_all_expected_facts() -> None:
    """Fake facts exercise gating without querying or constructing Qt."""
    facts = {
        "GITHUB_ACTIONS": "true",
        "RUNNER_OS": "Linux",
        "QT_QPA_PLATFORM": "offscreen",
    }
    admit_host("linux", facts)
    with pytest.raises(RuntimeError, match="hosted Linux"):
        admit_host("win32", facts)
    for key in facts:
        missing = facts.copy()
        del missing[key]
        with pytest.raises(RuntimeError, match="hosted Linux"):
            admit_host("linux", missing)


def test_lazy_sampler_is_guarded_and_uses_exact_getter_descriptors_only() -> None:
    """AST inspection never imports or invokes the real Qt factory."""
    tree = ast.parse(
        (
            Path(__file__).parents[2] / "scripts" / "retained_status_focus_probe.py"
        ).read_text(encoding="utf-8")
    )
    sampler = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "sample_manual_status_windows"
    )
    assert isinstance(sampler.body[1], ast.Expr)
    assert ast.unparse(sampler.body[1].value).startswith("admit_host(")
    source = ast.unparse(sampler)
    assert "QObject.objectName(" in source
    assert "QWidget.windowHandle(" in source
    assert "widget.objectName(" not in source
    assert "widget.windowHandle(" not in source
    assert "winId(" not in source
    assert PREDECESSOR == (
        "tests/ux/test_app_journeys.py::TestUpdates::"
        "test_numeric_checkout_checks_the_stable_release"
    )
