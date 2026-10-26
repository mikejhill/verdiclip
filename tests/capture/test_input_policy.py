"""Capture input routing is deterministic without keyboard or mouse hardware."""

import pytest

from verdiclip.capture.input_policy import CaptureInputPolicy, InputKind
from verdiclip.capture.input_routing import MoveMode
from verdiclip.geometry import Point, Rect


def test_owned_menu_trace_replays_without_forwarding_a_click() -> None:
    """Frozen button coordinates never overwrite tracked move endpoints."""
    policy = CaptureInputPolicy(Point(262, 168), Rect(0, 0, 1920, 1080))
    actions = []
    for message, point in [
        (0x200, Point(259, 194)),
        (0x201, Point(262, 168)),
        (0x200, Point(369, 244)),
        (0x202, Point(262, 168)),
    ]:
        decision = policy.mouse(
            message, point, mode=MoveMode.ABSOLUTE, anchor=Point(262, 168)
        )
        assert decision.consume
        actions.extend(decision.events)
    assert [(event.kind, event.point) for event in actions] == [
        (InputKind.MOVE, Point(259, 194)),
        (InputKind.PRESS, Point(259, 194)),
        (InputKind.MOVE, Point(369, 244)),
        (InputKind.RELEASE, Point(369, 244)),
    ]
    assert not policy.owns_pairs


@pytest.mark.parametrize("key", [0x1B, 0x0D])
def test_semantic_keys_consume_both_halves_before_acting(key: int) -> None:
    """Escape/Enter cannot leak their release into the preserved original menu."""
    policy = CaptureInputPolicy(Point(10, 10), Rect(0, 0, 100, 100))
    assert not policy.key(key, pressed=False).consume
    down = policy.key(key, pressed=True)
    assert down.consume
    assert not down.events
    assert policy.owns_pairs
    up = policy.key(key, pressed=False)
    assert up.consume
    assert up.events[0].kind is (InputKind.CANCEL if key == 0x1B else InputKind.ENTER)
    assert not policy.owns_pairs


def test_cancel_drains_only_owned_pairs_and_cannot_resume() -> None:
    """Focus loss cannot forward an owned release or later resurrect selection."""
    policy = CaptureInputPolicy(Point(10, 10), Rect(0, 0, 100, 100))
    assert policy.mouse(0x201, Point(0, 0), mode=None, anchor=Point(0, 0)).consume
    assert policy.key(0x25, pressed=True).consume
    assert len(policy.cancel("focus-lost")) == 1
    assert not policy.cancel("again")
    assert policy.key(0x25, pressed=False).consume
    assert policy.mouse(0x202, Point(99, 99), mode=None, anchor=Point(0, 0)).consume
    assert not policy.owns_pairs
    assert not policy.key(0x0D, pressed=True).events


def test_relative_pointer_keyboard_and_unknown_source_fail_closed() -> None:
    """Model physical deltas while refusing unknown injected movement semantics."""
    policy = CaptureInputPolicy(Point(10, 10), Rect(-100, -100, 300, 300))
    moved = policy.mouse(
        0x200, Point(12, 13), mode=MoveMode.OS_RELATIVE, anchor=Point(10, 10)
    )
    assert moved.events[0].point == Point(12, 13)
    assert policy.key(0x27, pressed=True, control=True).events[0].point == Point(22, 13)
    assert policy.key(0x27, pressed=False).consume
    assert not policy.key(0x11, pressed=True).consume
    decision = policy.mouse(0x200, Point(20, 20), mode=None, anchor=Point(10, 10))
    assert decision.consume
    assert decision.events[0].kind is InputKind.CANCEL


def test_right_button_cancels_on_release_and_wheel_does_not_scroll_target() -> None:
    """Alternative cancellation consumes the whole pair; wheel has no forwarding."""
    policy = CaptureInputPolicy(Point(10, 10), Rect(0, 0, 100, 100))
    assert policy.mouse(0x204, Point(0, 0), mode=None, anchor=Point(0, 0)).consume
    assert (
        policy.mouse(0x205, Point(0, 0), mode=None, anchor=Point(0, 0)).events[0].kind
        is InputKind.CANCEL
    )
    policy = CaptureInputPolicy(Point(10, 10), Rect(0, 0, 100, 100))
    assert policy.mouse(0x20A, Point(0, 0), mode=None, anchor=Point(0, 0)).consume


def test_overlapping_extra_buttons_keep_independent_release_ownership() -> None:
    """Both X-button releases stay swallowed even after the first cancels capture."""
    policy = CaptureInputPolicy(Point(10, 10), Rect(0, 0, 100, 100))
    for button in (1, 2):
        assert policy.mouse(
            0x20B, Point(0, 0), mode=None, anchor=Point(0, 0), button=button
        ).consume
    assert policy.mouse(
        0x20C, Point(0, 0), mode=None, anchor=Point(0, 0), button=1
    ).consume
    assert policy.owns_pairs
    assert policy.mouse(
        0x20C, Point(0, 0), mode=None, anchor=Point(0, 0), button=2
    ).consume
    assert not policy.owns_pairs


@pytest.mark.parametrize("initially_held", [False, True])
def test_forwarded_key_repeat_never_changes_release_owner(
    *, initially_held: bool
) -> None:
    """A down sent to the original app keeps its repeat/up path through draining."""
    policy = CaptureInputPolicy(Point(10, 10), Rect(0, 0, 100, 100))
    if initially_held:
        policy.prime_keys(frozenset({0x41}))
    assert not policy.key(0x41, pressed=True).consume
    policy.cancel("other-cancellation")
    assert not policy.key(0x41, pressed=True).consume
    assert not policy.key(0x41, pressed=False).consume
    assert not policy.owns_pairs
