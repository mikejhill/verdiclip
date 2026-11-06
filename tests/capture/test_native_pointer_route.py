"""Portable cancellation and absolute-coordinate contracts; no Windows APIs."""

import pytest

from verdiclip.capture.native_pointer import NativePointerRoute, PointerPhase
from verdiclip.geometry import Point


def test_absolute_route_requires_natural_owned_admission() -> None:
    """Retain deterministic ownership and routing without native execution."""
    route = NativePointerRoute()
    route.bind(frozenset({7}))
    assert not route.move(Point(1, 2), lambda: True)
    assert not route.admit(8, lambda: True)
    assert route.admit(7, lambda: True)
    assert route.move(Point(10, 20), lambda: True)
    assert route.move(Point(11, 19), lambda: True)
    assert route.point == Point(11, 19)


@pytest.mark.parametrize("during_admission", [False, True])
def test_reentrant_cancellation_cannot_restore_ownership(
    *, during_admission: bool
) -> None:
    """Retain deterministic ownership and routing without native execution."""
    route = NativePointerRoute()
    route.bind(frozenset({7}))
    if not during_admission:
        assert route.admit(7, lambda: True)

    def cancelled() -> bool:
        route.close()
        return True

    accepted = (
        route.admit(7, cancelled)
        if during_admission
        else route.move(Point(1, 2), cancelled)
    )
    assert not accepted
    assert route.phase is PointerPhase.CLOSED
    assert not route.admit(7, lambda: True)
    assert route.point is None


def test_foreign_ownership_change_is_terminal() -> None:
    """Retain deterministic ownership and routing without native execution."""
    route = NativePointerRoute()
    route.bind(frozenset({7}))
    assert route.admit(7, lambda: True)
    assert not route.move(Point(2, 3), lambda: False)
    assert not route.move(Point(2, 3), lambda: True)
    assert route.phase is PointerPhase.CLOSED


@pytest.mark.parametrize("error", [ValueError, KeyboardInterrupt, SystemExit])
def test_failed_proof_closes_route_even_for_control_exception(
    error: type[BaseException],
) -> None:
    """Exceptions propagate with terminal ownership, never a stale open lease."""
    route = NativePointerRoute()
    route.bind(frozenset({7}))

    def failed() -> bool:
        raise error()

    with pytest.raises(error):
        route.admit(7, failed)
    assert route.phase is PointerPhase.CLOSED
