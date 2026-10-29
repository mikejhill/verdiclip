"""Resolve ordinary and explicit scroll selection without forwarding a click."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from PySide6.QtCore import QObject, Signal

from verdiclip.capture.scroll.models import ScrollTarget
from verdiclip.capture.scroll.targets import (
    NodeKind,
    SemanticTargets,
    TargetCandidate,
    TargetIntent,
)
from verdiclip.capture.scroll.uia_adapter import UiaAdapter
from verdiclip.capture.scroll.uia_messages import UiaRequest
from verdiclip.capture.scroll.uia_protocol import UiaDiscovery, UiaReference
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Point, Rect


class EntryState(StrEnum):
    """Discovery never grants permission to scroll."""

    IDLE = "idle"
    DISCOVERING = "discovering"
    OFFER = "offer"
    TRANSFERRED = "transferred"
    CLOSING = "closing"
    BROKEN = "broken"


@dataclass(frozen=True, slots=True)
class ScrollOffer:
    """Nearest-first semantic choices and immutable provider references."""

    title: str
    point: Point
    candidates: tuple[TargetCandidate, ...]
    references: tuple[UiaReference, ...]
    viewports: tuple[Rect, ...]


@dataclass(frozen=True, slots=True)
class ConfirmedScroll:
    """An explicit Start transfers the same adapter scope to acquisition."""

    target: ScrollTarget
    reference: UiaReference


class ScrollEntry(QObject):
    """Share one semantic entry path for scrollbar clicks and hidden-bar mode.

    The caller retains its nonactivating input lease through discovery and
    confirmation, displays offers without activation, then transfers this same
    adapter to the backend. This object never starts movement or creates UI.
    """

    offered = Signal(object)  # ScrollOffer
    confirmed = Signal(object)  # ConfirmedScroll
    fallback = Signal()  # ordinary click retains ordinary window capture
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, adapter: UiaAdapter, parent: QObject | None = None) -> None:
        """Own an exclusive discovery scope; inject the adapter for replay tests."""
        super().__init__(parent)
        self._adapter = adapter
        self._state = EntryState.IDLE
        self._request: UiaRequest | None = None
        self._offer: ScrollOffer | None = None
        self._window_identity = ""
        self._point = Point(0, 0)
        self._title = ""
        self._explicit = False
        self._outcome = ""
        self._message = ""
        adapter.discovered.connect(self._discovered)
        adapter.failed.connect(self._failed)
        adapter.scope_closed.connect(self._closed)
        adapter.cleanup_failed.connect(self._cleanup_failed)

    @property
    def state(self) -> EntryState:
        """A closing or transferred scope cannot be reused for another click."""
        return self._state

    def inspect(
        self,
        window: int,
        point: Point,
        title: str,
        *,
        explicit: bool,
        expected_window_identity: str,
    ) -> bool:
        """Suppress the selection click; inspect only semantic roles/ancestors."""
        if self._state is not EntryState.IDLE or self._adapter.busy:
            return False
        if not expected_window_identity.startswith(f"{window}:"):
            raise ValueError("The selected window identity does not match its handle")
        self._state = EntryState.DISCOVERING
        self._point, self._title = point, title
        self._window_identity = expected_window_identity
        self._explicit = explicit
        try:
            self._request = self._adapter.inspect(window, point)
        except (CaptureError, ValueError) as error:
            self._decline(str(error))
        return self._state in {EntryState.DISCOVERING, EntryState.OFFER}

    def _discovered(self, request: UiaRequest, discovery: UiaDiscovery) -> None:
        if self._state is not EntryState.DISCOVERING or request is not self._request:
            return
        self._request = None
        if discovery.window_identity != self._window_identity:
            self._decline("The selected window changed during discovery.")
            return
        resolver = SemanticTargets(discovery.nodes)
        hint = (resolver.explicit_mode if self._explicit else resolver.normal_click)(
            discovery.hit_identity, self._point, discovery.window_identity
        )
        references = {
            reference.identity: reference for reference in discovery.references
        }
        candidates = tuple(
            choice for choice in hint.candidates if choice.identity in references
        )
        if hint.intent is not TargetIntent.SCROLL_CANDIDATE or not candidates:
            self._decline("No supported scroll area was found at this point.")
            return
        self._offer = ScrollOffer(
            self._title,
            self._point,
            candidates,
            tuple(references[choice.identity] for choice in candidates),
            tuple(self._viewport(choice.bounds, discovery) for choice in candidates),
        )
        self._state = EntryState.OFFER
        self.offered.emit(self._offer)

    @staticmethod
    def _viewport(bounds: Rect, discovery: UiaDiscovery) -> Rect:
        """Exclude only a verified semantic scrollbar covering an exact edge."""
        hit = next(
            node for node in discovery.nodes if node.identity == discovery.hit_identity
        )
        bar = hit.bounds
        if (
            hit.kind is NodeKind.VERTICAL_SCROLLBAR
            and bar.y <= bounds.y
            and bar.bottom >= bounds.bottom
        ):
            if bar.right == bounds.right and bounds.x < bar.x < bounds.right:
                return Rect(bounds.x, bounds.y, bar.x - bounds.x, bounds.height)
            if bar.x == bounds.x and bounds.x < bar.right < bounds.right:
                return Rect(
                    bar.right, bounds.y, bounds.right - bar.right, bounds.height
                )
        return bounds

    def start(self, index: int, viewport: Rect, *, consent: bool) -> bool:
        """Only explicit Start may transfer one verified candidate to acquisition."""
        offer = self._offer
        if not consent or self._state is not EntryState.OFFER or offer is None:
            return False
        if not 0 <= index < len(offer.candidates):
            return False
        choice = offer.candidates[index]
        coordinates = (viewport.x, viewport.y, viewport.width, viewport.height)
        if (
            not all(
                math.isfinite(value) and int(value) == value for value in coordinates
            )
            or viewport.is_empty
            or viewport.intersected(choice.bounds) != viewport
        ):
            return False
        selected = ConfirmedScroll(
            ScrollTarget(choice.identity, offer.title, viewport),
            offer.references[index],
        )
        self._state = EntryState.TRANSFERRED
        self.confirmed.emit(selected)
        return True

    def _failed(self, request: UiaRequest, message: str) -> None:
        if request is self._request and self._state is EntryState.DISCOVERING:
            self._decline(message)

    def _decline(self, message: str) -> None:
        self._retire("failed" if self._explicit else "fallback", message)

    def cancel(self) -> None:
        """Discard unconfirmed proposals; a transferred backend owns its cleanup."""
        if self._state in {EntryState.DISCOVERING, EntryState.OFFER}:
            self._retire("cancelled", "")

    def _retire(self, outcome: str, message: str) -> None:
        self._request = self._offer = None
        self._outcome, self._message = outcome, message
        self._state = EntryState.CLOSING
        self._adapter.cancel()
        if not self._adapter.busy:
            self._closed()

    def _closed(self) -> None:
        if self._state is EntryState.TRANSFERRED:
            self._offer = None
            self._state = EntryState.IDLE
            return
        if self._state in {EntryState.DISCOVERING, EntryState.OFFER}:
            self._decline("The discovery helper stopped before confirmation.")
            return
        if self._state is not EntryState.CLOSING:
            return
        outcome, self._outcome = self._outcome, ""
        message, self._message = self._message, ""
        self._state = EntryState.IDLE
        if outcome == "fallback":
            self.fallback.emit()
        elif outcome == "failed":
            self.failed.emit(message)
        else:
            self.cancelled.emit()

    def _cleanup_failed(self, message: str) -> None:
        if self._state is EntryState.CLOSING:
            self._state = EntryState.BROKEN
            self.failed.emit(message)
