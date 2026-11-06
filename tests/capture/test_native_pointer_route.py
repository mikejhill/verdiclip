"""Portable cancellation and absolute-coordinate contracts; no Windows APIs."""

import pytest

from verdiclip.capture.native_pointer import NativePointerRoute, PointerPhase
from verdiclip.geometry import Point


def test_absolute_route_requires_natural_owned_admission() -> None:
    """Retain deterministic ownership and routing without native execution."""
    route = NativePointerRoute()
    route.bind(frozenset({7}))
    operation_result_1 = route.move(Point(1, 2), lambda: True)
    assert not operation_result_1
    operation_result_2 = route.admit(8, lambda: True)
    assert not operation_result_2
    operation_result_3 = route.admit(7, lambda: True)
    assert operation_result_3
    operation_result_4 = route.move(Point(10, 20), lambda: True)
    assert operation_result_4
    operation_result_5 = route.move(Point(11, 19), lambda: True)
    assert operation_result_5
    assert route.point == Point(11, 19)


@pytest.mark.parametrize("during_admission", [False, True])
def test_reentrant_cancellation_cannot_restore_ownership(
    *, during_admission: bool
) -> None:
    """Retain deterministic ownership and routing without native execution."""
    route = NativePointerRoute()
    route.bind(frozenset({7}))
    if not during_admission:
        operation_result_11 = route.admit(7, lambda: True)
        assert operation_result_11

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
    operation_result_6 = route.admit(7, lambda: True)
    assert not operation_result_6
    assert route.point is None


def test_foreign_ownership_change_is_terminal() -> None:
    """Retain deterministic ownership and routing without native execution."""
    route = NativePointerRoute()
    route.bind(frozenset({7}))
    operation_result_7 = route.admit(7, lambda: True)
    assert operation_result_7
    operation_result_8 = route.move(Point(2, 3), lambda: False)
    assert not operation_result_8
    operation_result_9 = route.move(Point(2, 3), lambda: True)
    assert not operation_result_9
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


@pytest.mark.parametrize("windows", [frozenset(), frozenset({0}), frozenset({True})])
def test_invalid_surface_set_cannot_be_bound(windows: frozenset[int]) -> None:
    """Empty, null and coercible identities are not ownership evidence."""
    route = NativePointerRoute()
    with pytest.raises(ValueError, match="surface lifetime"):
        route.bind(windows)
    assert route.phase is PointerPhase.WAITING


def test_failed_admission_proof_is_terminal() -> None:
    """An explicit proof refusal cannot be retried into success."""
    route = NativePointerRoute()
    route.bind(frozenset({7}))
    operation_result_10 = route.admit(7, lambda: False)
    assert not operation_result_10
    assert route.phase is PointerPhase.CLOSED
