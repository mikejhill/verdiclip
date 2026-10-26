"""Convenient scrollbar discovery and explicit mode use no input hardware."""

from __future__ import annotations

from dataclasses import replace
from typing import override

import pytest
from tests.capture.test_scroll_uia_protocol import Payload

from verdiclip.capture.scroll.entry import (
    ConfirmedScroll,
    EntryState,
    ScrollEntry,
    ScrollOffer,
)
from verdiclip.capture.scroll.uia_adapter import UiaAdapter
from verdiclip.capture.scroll.uia_messages import UiaAction, UiaRequest
from verdiclip.capture.scroll.uia_protocol import UiaDiscovery
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Point, Rect


class DiscoveryFixture(UiaAdapter):
    """Only read-only discovery is implemented; movement cannot occur here."""

    def __init__(self) -> None:
        super().__init__()
        self.pending = False
        self.requests: list[UiaRequest] = []
        self.points: list[Point] = []
        self.defer_cleanup = False
        self.unavailable = False
        self.cancel_count = 0

    @property
    @override
    def busy(self) -> bool:
        return self.pending

    @override
    def inspect(self, window_handle: int, point: Point) -> UiaRequest:
        if self.unavailable:
            raise CaptureError("Fixture semantic provider unavailable")
        assert window_handle > 0
        self.points.append(point)
        request = UiaRequest(str(len(self.requests)), UiaAction.INSPECT, b"{}")
        self.requests.append(request)
        self.pending = True
        return request

    def deliver(self, value: UiaDiscovery) -> None:
        """Reply to the latest fake request without touching a provider."""
        self.pending = False
        self.discovered.emit(self.requests[-1], value)

    @override
    def cancel(self) -> None:
        self.cancel_count += 1
        self.pending = self.defer_cleanup
        if not self.pending:
            self.scope_closed.emit()


class EntryFixture:
    """Record only entry contracts, without a widget or provider process."""

    def __init__(self) -> None:
        self.adapter = DiscoveryFixture()
        self.entry = ScrollEntry(self.adapter)
        self.offers: list[ScrollOffer] = []
        self.confirmed: list[ConfirmedScroll] = []
        self.fallbacks: list[bool] = []
        self.failures: list[str] = []
        self.cancellations: list[bool] = []
        self.entry.offered.connect(self.offers.append)
        self.entry.confirmed.connect(self.confirmed.append)
        self.entry.fallback.connect(lambda: self.fallbacks.append(True))
        self.entry.failed.connect(self.failures.append)
        self.entry.cancelled.connect(lambda: self.cancellations.append(True))

    @staticmethod
    def discovery() -> UiaDiscovery:
        """Return a validated scrollbar/content/window ancestry snapshot."""
        return UiaDiscovery.decode(Payload.discovery(Payload.nodes()))

    def begin(self, *, explicit: bool = False) -> bool:
        """Inspect the same known physical point through either entry mode."""
        return self.entry.inspect(
            1,
            Point(92, 10),
            "Example document",
            explicit=explicit,
            expected_window_identity=Payload.WINDOW,
        )


def test_scrollbar_proposes_then_requires_explicit_valid_start() -> None:
    """No discovery, duplicate request or invalid ROI can imply consent."""
    fixture = EntryFixture()
    assert fixture.begin()
    assert not fixture.begin()
    fixture.adapter.deliver(fixture.discovery())
    assert len(fixture.offers) == 1
    assert not fixture.confirmed
    assert not fixture.entry.start(0, Rect(0, 0, 90, 48), consent=False)
    assert not fixture.entry.start(2, Rect(0, 0, 90, 48), consent=True)
    assert not fixture.entry.start(0, Rect(0, 0, 200, 48), consent=True)
    assert not fixture.entry.start(0, Rect(float("nan"), 0, 90, 48), consent=True)
    assert not fixture.entry.start(0, Rect(0.5, 0, 80, 40), consent=True)
    assert fixture.entry.state is EntryState.OFFER
    assert not fixture.confirmed
    assert fixture.entry.start(0, Rect(0, 0, 90, 48), consent=True)
    assert len(fixture.confirmed) == 1
    assert (
        fixture.confirmed[0].target.identity == fixture.confirmed[0].reference.identity
    )
    assert fixture.entry.state is EntryState.TRANSFERRED
    assert not fixture.entry.start(0, Rect(0, 0, 90, 48), consent=True)
    fixture.entry.cancel()
    assert fixture.adapter.cancel_count == 0
    fixture.adapter.scope_closed.emit()
    assert fixture.entry.state is EntryState.IDLE


@pytest.mark.parametrize("explicit", [False, True])
def test_content_click_preserves_normal_behavior_but_explicit_mode_finds_hidden_bar(
    *, explicit: bool
) -> None:
    """Ordinary content clicks remain screenshots; explicit mode may propose it."""
    fixture = EntryFixture()
    assert fixture.begin(explicit=explicit)
    original = fixture.discovery()
    content = original.nodes[1]
    fixture.adapter.deliver(
        replace(original, hit_identity=content.identity, nodes=original.nodes[1:])
    )
    if explicit:
        assert fixture.offers
        assert not fixture.fallbacks
    else:
        assert fixture.fallbacks == [True]
        assert not fixture.offers
    assert not fixture.confirmed
    fixture.entry.cancel()


@pytest.mark.parametrize("explicit", [False, True])
def test_unavailable_provider_is_honest_and_allows_safe_retry(
    *, explicit: bool
) -> None:
    """Unsupported explicit capture fails; ordinary click falls back safely."""
    fixture = EntryFixture()
    fixture.adapter.unavailable = True
    assert not fixture.begin(explicit=explicit)
    assert fixture.entry.state is EntryState.IDLE
    assert bool(fixture.failures) is explicit
    assert bool(fixture.fallbacks) is not explicit
    fixture.adapter.unavailable = False
    assert fixture.begin(explicit=explicit)
    fixture.entry.cancel()


def test_cancel_waits_for_scope_cleanup_and_drops_late_discovery() -> None:
    """Cleanup acknowledgement precedes reuse, with late replies discarded."""
    fixture = EntryFixture()
    assert fixture.begin()
    fixture.adapter.defer_cleanup = True
    fixture.entry.cancel()
    assert fixture.entry.state is EntryState.CLOSING
    assert not fixture.begin()
    fixture.adapter.discovered.emit(fixture.adapter.requests[0], fixture.discovery())
    assert not fixture.offers
    assert not fixture.cancellations
    fixture.adapter.pending = False
    fixture.adapter.scope_closed.emit()
    assert fixture.cancellations == [True]
    assert fixture.entry.state is EntryState.IDLE


def test_cleanup_failure_retains_retry_barrier() -> None:
    """A failed provider cleanup cannot silently authorize another helper."""
    fixture = EntryFixture()
    assert fixture.begin()
    fixture.adapter.defer_cleanup = True
    fixture.entry.cancel()
    fixture.adapter.cleanup_failed.emit("Fixture cleanup not confirmed")
    assert fixture.entry.state is EntryState.BROKEN
    assert fixture.failures == ["Fixture cleanup not confirmed"]
    assert not fixture.begin()


def test_stale_request_and_changed_window_cannot_offer_another_target() -> None:
    """Only the original request object and window generation may propose."""
    fixture = EntryFixture()
    assert fixture.begin(explicit=True)
    request = fixture.adapter.requests[0]
    fixture.adapter.discovered.emit(replace(request), fixture.discovery())
    assert not fixture.offers
    fixture.adapter.deliver(replace(fixture.discovery(), window_identity="2:2:3"))
    assert not fixture.offers
    assert "window changed" in fixture.failures[0]


def test_reused_native_handle_with_new_generation_is_not_the_selected_window() -> None:
    """Handle reuse by another process cannot retarget a frozen selection."""
    fixture = EntryFixture()
    assert fixture.begin(explicit=True)
    fixture.adapter.deliver(replace(fixture.discovery(), window_identity="1:99:123"))
    assert not fixture.offers
    assert fixture.failures


def test_handle_identity_mismatch_is_rejected_before_inspection() -> None:
    """A handle from a different frozen target cannot enter the adapter."""
    fixture = EntryFixture()
    with pytest.raises(ValueError, match="identity does not match"):
        fixture.entry.inspect(
            2,
            Point(92, 10),
            "Other",
            explicit=True,
            expected_window_identity=Payload.WINDOW,
        )
    assert not fixture.adapter.requests


@pytest.mark.parametrize("receipt", ["failure", "scope_closed"])
def test_discovery_terminal_receipt_cannot_leave_an_unconfirmed_offer(
    receipt: str,
) -> None:
    """A failed or disappearing helper reports a useful explicit failure."""
    fixture = EntryFixture()
    assert fixture.begin(explicit=True)
    fixture.adapter.pending = False
    if receipt == "failure":
        fixture.adapter.failed.emit(fixture.adapter.requests[0], "Provider stopped")
    else:
        fixture.adapter.scope_closed.emit()
    assert fixture.entry.state is EntryState.IDLE
    assert len(fixture.failures) == 1
    assert not fixture.offers


def test_semantic_left_edge_bar_is_excluded_from_suggested_content() -> None:
    """RTL scrollbar exclusion follows semantic geometry, without pixel guesses."""
    fixture = EntryFixture()
    assert fixture.entry.inspect(
        1,
        Point(3, 10),
        "RTL",
        explicit=False,
        expected_window_identity=Payload.WINDOW,
    )
    discovery = fixture.discovery()
    bar = replace(discovery.nodes[0], bounds=Rect(0, 0, 6, 48))
    fixture.adapter.deliver(replace(discovery, nodes=(bar, *discovery.nodes[1:])))
    assert fixture.offers[0].viewports == (Rect(6, 0, 90, 48),)
    fixture.entry.cancel()
