"""No Win32 calls or native fixtures: owned cursor accounting controls."""

import pytest
from tests.capture.owned_cursor_policy import OwnedCursorScope


def test_one_decrement_one_balance_and_no_repeat() -> None:
    """One decrement one balance and no repeat."""
    calls = []
    scope = OwnedCursorScope(
        lambda: 1, lambda visible: calls.append(visible) or (0 if visible else -1)
    )
    accepted = scope.admit(lambda: True, natural=True)
    assert accepted
    scope.retire()
    scope.retire()
    assert calls == [False, True]
    assert not scope.accepted


@pytest.mark.parametrize(("natural", "safe"), [(False, True), (True, False)])
def test_refusal_before_decrement(*, natural: bool, safe: bool) -> None:
    """Refusal before decrement."""
    calls = []
    scope = OwnedCursorScope(lambda: 1, lambda visible: calls.append(visible) or 0)
    accepted = scope.admit(lambda: safe, natural=natural)
    assert not accepted
    scope.retire()
    assert calls == []


@pytest.mark.parametrize("count", [-5, -2, 0, 1, 3])
def test_unexpected_counter_balanced_once_without_normalization(count: int) -> None:
    """Unexpected counter balanced once without normalization."""
    calls = []
    scope = OwnedCursorScope(
        lambda: 1,
        lambda visible: calls.append(visible) or (count + 1 if visible else count),
    )
    accepted = scope.admit(lambda: True, natural=True)
    assert not accepted
    scope.retire()
    assert calls == [False, True]


@pytest.mark.parametrize("error", [RuntimeError, KeyboardInterrupt, SystemExit])
def test_callback_failure_after_decrement_retains_cleanup(
    error: type[BaseException],
) -> None:
    """Callback failure after decrement retains cleanup."""
    calls = []
    proofs = 0

    def safe() -> bool:
        nonlocal proofs
        proofs += 1
        if proofs == 2:
            raise error()
        return True

    scope = OwnedCursorScope(
        lambda: 1, lambda visible: calls.append(visible) or (0 if visible else -1)
    )

    def run() -> None:
        try:
            scope.admit(safe, natural=True)
        finally:
            scope.retire()

    with pytest.raises(error):
        run()
    assert calls == [False, True]


def test_foreign_capture_change_revokes_permanently() -> None:
    """Foreign capture change revokes permanently."""
    calls = []
    scope = OwnedCursorScope(
        lambda: 1, lambda visible: calls.append(visible) or (0 if visible else -1)
    )
    accepted = scope.admit(lambda: True, natural=True)
    assert accepted
    accepted = scope.observe(lambda: False)
    assert not accepted
    accepted = scope.observe(lambda: True)
    assert not accepted
    assert scope.failed
    assert calls == [False, True]


def test_wrong_thread_never_balances_other_thread() -> None:
    """Wrong thread never balances other thread."""
    thread = [1]
    calls = []
    scope = OwnedCursorScope(
        lambda: thread[0], lambda visible: calls.append(visible) or -1
    )
    accepted = scope.admit(lambda: True, natural=True)
    assert accepted
    thread[0] = 2
    with pytest.raises(RuntimeError, match="wrong-owner-thread"):
        scope.retire()
    assert calls == [False]


@pytest.mark.parametrize("visible_fault", [False, True])
@pytest.mark.parametrize("error", [RuntimeError, KeyboardInterrupt, SystemExit])
def test_native_callback_uncertainty_never_retries(
    *, visible_fault: bool, error: type[BaseException]
) -> None:
    """Native callback uncertainty never retries."""
    calls = []

    def show(visible: int, /) -> int:
        calls.append(visible)
        if visible == visible_fault:
            raise error()
        return 0 if visible else -1

    scope = OwnedCursorScope(lambda: 1, show)

    def run() -> None:
        try:
            scope.admit(lambda: True, natural=True)
        finally:
            scope.retire()

    with pytest.raises(error):
        run()
    scope.retire()
    assert scope.failed
    assert calls == ([False, True] if visible_fault else [False])


@pytest.mark.parametrize("cancel_at", [1, 2])
def test_reentrant_proof_retirement_cannot_resurrect_scope(cancel_at: int) -> None:
    """Reentrant proof retirement cannot resurrect scope."""
    calls = []
    proofs = 0
    scope = OwnedCursorScope(
        lambda: 1, lambda visible: calls.append(visible) or (0 if visible else -1)
    )

    def safe() -> bool:
        nonlocal proofs
        proofs += 1
        if proofs == cancel_at:
            scope.retire()
        return True

    accepted = scope.admit(safe, natural=True)
    assert not accepted
    scope.retire()
    assert calls == ([] if cancel_at == 1 else [False, True])
    assert not scope.accepted
