"""Semantic targeting keeps ordinary window clicks and never invokes controls."""

from __future__ import annotations

from verdiclip.capture.scroll.targets import (
    NodeKind,
    SemanticNode,
    SemanticTargets,
    TargetIntent,
)
from verdiclip.geometry import Point, Rect


class Tree:
    """A window with a scrollable outer pane and a nested scrollable list."""

    @staticmethod
    def nodes() -> tuple[SemanticNode, ...]:
        """Return read-only provider data, with explicit semantic scrollbar roles."""
        return (
            SemanticNode(
                "window", "process-window", NodeKind.WINDOW, Rect(0, 0, 600, 600)
            ),
            SemanticNode(
                "outer",
                "process-window",
                NodeKind.CONTENT,
                Rect(0, 0, 500, 500),
                "window",
                vertically_scrollable=True,
            ),
            SemanticNode(
                "list",
                "process-window",
                NodeKind.CONTENT,
                Rect(100, 100, 200, 200),
                "outer",
                vertically_scrollable=True,
            ),
            SemanticNode(
                "bar",
                "process-window",
                NodeKind.VERTICAL_SCROLLBAR,
                Rect(280, 100, 20, 200),
                "list",
            ),
            SemanticNode(
                "body",
                "process-window",
                NodeKind.OTHER,
                Rect(110, 110, 120, 120),
                "list",
            ),
        )


def test_normal_scrollbar_click_proposes_nearest_container() -> None:
    """A nested semantic bar proposes its list before the outer page."""
    hint = SemanticTargets(Tree.nodes()).normal_click(
        "bar", Point(290, 180), "process-window"
    )
    assert hint.intent is TargetIntent.SCROLL_CANDIDATE
    assert [candidate.identity for candidate in hint.candidates] == ["list", "outer"]
    assert hint.requires_confirmation


def test_window_body_click_keeps_ordinary_window_capture() -> None:
    """Scrollable ancestry alone must not change normal window-click behavior."""
    hint = SemanticTargets(Tree.nodes()).normal_click(
        "body", Point(150, 150), "process-window"
    )
    assert hint.intent is TargetIntent.WINDOW
    assert hint.candidates == ()


def test_explicit_mode_can_resolve_a_hidden_scrollbar() -> None:
    """Explicit mode uses container semantics without demanding visible bar pixels."""
    hint = SemanticTargets(Tree.nodes()).explicit_mode(
        "body", Point(150, 150), "process-window"
    )
    assert hint.intent is TargetIntent.SCROLL_CANDIDATE
    assert hint.candidates[0].identity == "list"


def test_unknown_or_custom_bar_does_not_guess_from_pixels() -> None:
    """An unrecognized role offers manual selection rather than synthetic clicks."""
    hint = SemanticTargets(Tree.nodes()).explicit_mode(
        "missing", Point(290, 180), "process-window"
    )
    assert hint.intent is TargetIntent.MANUAL_AREA
    assert hint.candidates == ()


def test_wrong_window_provider_data_cannot_select_a_target() -> None:
    """A stale hit from another process/window remains an ordinary window click."""
    hint = SemanticTargets(Tree.nodes()).normal_click(
        "bar", Point(290, 180), "another-window"
    )
    assert hint.intent is TargetIntent.WINDOW
    assert hint.candidates == ()


def test_cycles_are_rejected_instead_of_partially_selecting() -> None:
    """Provider graph corruption cannot return a plausible incomplete ancestry."""
    nodes = (
        SemanticNode(
            "bar", "w", NodeKind.VERTICAL_SCROLLBAR, Rect(0, 0, 20, 100), "loop"
        ),
        SemanticNode(
            "loop",
            "w",
            NodeKind.CONTENT,
            Rect(0, 0, 100, 100),
            "bar",
            vertically_scrollable=True,
        ),
    )
    hint = SemanticTargets(nodes).normal_click("bar", Point(10, 10), "w")
    assert hint.intent is TargetIntent.WINDOW
    assert hint.candidates == ()
