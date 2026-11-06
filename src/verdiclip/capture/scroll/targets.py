"""Resolve read-only semantic hits without clicking or invoking target controls."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from verdiclip.geometry import Point, Rect


class NodeKind(StrEnum):
    """Only an explicit vertical scrollbar role changes normal click intent."""

    WINDOW = "window"
    CONTENT = "content"
    VERTICAL_SCROLLBAR = "vertical_scrollbar"
    OTHER = "other"


class TargetIntent(StrEnum):
    """Selection proposes a target; it never authorizes scrolling by itself."""

    WINDOW = "window"
    SCROLL_CANDIDATE = "scroll_candidate"
    MANUAL_AREA = "manual_area"


@dataclass(frozen=True, slots=True)
class SemanticNode:
    """One bounded UIA-provider observation; no live automation element escapes."""

    identity: str
    window_identity: str
    kind: NodeKind
    bounds: Rect
    parent_identity: str | None = None
    vertically_scrollable: bool = False


@dataclass(frozen=True, slots=True)
class UnresolvedHit:
    """A proven OTHER initial leaf with no persistent or selectable identity."""

    window_identity: str
    bounds: Rect
    parent_identity: str


@dataclass(frozen=True, slots=True)
class TargetCandidate:
    """Unconfirmed container bounds; setup must choose the content viewport."""

    identity: str
    bounds: Rect


@dataclass(frozen=True, slots=True)
class TargetHint:
    """Keyboard-selectable candidates and a mandatory confirmation boundary."""

    intent: TargetIntent
    candidates: tuple[TargetCandidate, ...] = ()
    requires_confirmation: bool = True


class SemanticTargets:
    """Bound traversal work and refuse inconsistent or cross-window ancestry."""

    MAX_NODES = 512
    MAX_ANCESTORS = 32

    def __init__(self, nodes: Sequence[SemanticNode]) -> None:
        """Snapshot semantic metadata without touching another application's UI."""
        self._nodes = {node.identity: node for node in nodes[: self.MAX_NODES]}
        self._valid = len(nodes) <= self.MAX_NODES and len(self._nodes) == len(nodes)

    def normal_click(
        self,
        hit: str | None,
        point: Point,
        window: str,
        unresolved: UnresolvedHit | None = None,
    ) -> TargetHint:
        """Propose scrolling only for a verified semantic scrollbar hit."""
        node = self.scrollbar_at(hit, point, window, unresolved)
        if node is None:
            return TargetHint(TargetIntent.WINDOW)
        candidates = self._ancestors(node, window)
        return (
            TargetHint(TargetIntent.SCROLL_CANDIDATE, candidates)
            if candidates
            else TargetHint(TargetIntent.WINDOW)
        )

    def scrollbar_at(
        self,
        hit: str | None,
        point: Point,
        window: str,
        unresolved: UnresolvedHit | None = None,
    ) -> SemanticNode | None:
        """Accept native scrollbar parts only through their verified ancestry."""
        node = self._initial_parent(hit, point, window, unresolved)
        visited: set[str] = set()
        for _depth in range(self.MAX_ANCESTORS):
            if node is None or node.identity in visited:
                return None
            visited.add(node.identity)
            if node.kind is NodeKind.VERTICAL_SCROLLBAR:
                return node if self._ancestors(node, window) else None
            if node.kind is not NodeKind.OTHER or node.parent_identity is None:
                return None
            node = self._hit(node.parent_identity, point, window)
        return None

    def explicit_mode(
        self,
        hit: str | None,
        point: Point,
        window: str,
        unresolved: UnresolvedHit | None = None,
    ) -> TargetHint:
        """Offer hidden-bar containers or fall back to an explicit manual area."""
        node = self.scrollbar_at(
            hit, point, window, unresolved
        ) or self._initial_parent(hit, point, window, unresolved)
        candidates = () if node is None else self._ancestors(node, window)
        return (
            TargetHint(TargetIntent.SCROLL_CANDIDATE, candidates)
            if candidates
            else TargetHint(TargetIntent.MANUAL_AREA)
        )

    def _initial_parent(
        self,
        hit: str | None,
        point: Point,
        window: str,
        unresolved: UnresolvedHit | None,
    ) -> SemanticNode | None:
        """Follow an explicit unresolved OTHER link without assigning it an ID."""
        if unresolved is None:
            return None if hit is None else self._hit(hit, point, window)
        if (
            hit is not None
            or unresolved.window_identity != window
            or not unresolved.bounds.contains(point)
            or len(self._nodes) >= self.MAX_ANCESTORS
        ):
            return None
        return self._hit(unresolved.parent_identity, point, window)

    def _hit(self, hit: str, point: Point, window: str) -> SemanticNode | None:
        """Ignore stale provider results, changed windows and out-of-bounds hits."""
        node = self._nodes.get(hit)
        if (
            not self._valid
            or node is None
            or node.window_identity != window
            or not node.bounds.contains(point)
        ):
            return None
        return node

    def _ancestors(
        self, node: SemanticNode, window: str
    ) -> tuple[TargetCandidate, ...]:
        """Return nearest-first choices only after validating the entire chain."""
        visited: set[str] = set()
        choices: list[TargetCandidate] = []
        for _depth in range(self.MAX_ANCESTORS):
            if node.identity in visited or node.window_identity != window:
                return ()
            visited.add(node.identity)
            if node.vertically_scrollable and not node.bounds.is_empty:
                choices.append(TargetCandidate(node.identity, node.bounds))
            if node.parent_identity is None:
                return tuple(choices)
            parent = self._nodes.get(node.parent_identity)
            if parent is None:
                return ()
            node = parent
        return ()
