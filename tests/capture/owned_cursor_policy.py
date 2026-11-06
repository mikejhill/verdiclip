"""Portable policy skeleton, not wired into capture or native cursor APIs."""

from collections.abc import Callable


class OwnedCursorScope:
    """One dedicated-thread decrement, never a global counter normalization."""

    def __init__(self, thread: Callable[[], int], show: Callable[[bool], int]) -> None:
        self._thread = thread
        self._show = show
        self._owner = thread()
        self._attempted = False
        self._decremented = False
        self._balanced = False
        self._closed = False
        self.accounting_confirmed = True
        self.balance_confirmed = False
        self.counter_refused = False
        self._expected_balance = 0
        self.accepted = False
        self.failed = False

    def is_closed(self) -> bool:
        """Re-read cancellation after callbacks may have reentered retirement."""
        return self._closed

    def _check_thread(self) -> None:
        if self._thread() != self._owner:
            self.failed = True
            raise RuntimeError("wrong-owner-thread")

    def admit(self, safe: Callable[[], bool], *, natural: bool) -> bool:
        """Caller supplies fresh native ownership and independent foreign proof."""
        self._check_thread()
        if self._attempted or self.is_closed():
            raise RuntimeError("scope-not-fresh")
        self._attempted = True
        if not natural or not safe() or self.is_closed():
            return False
        self.accounting_confirmed = False
        returned = [False]

        def call_returned() -> bool:
            return returned[0]

        try:
            hide = False
            count = self._show(hide)
            returned[0] = True
        finally:
            if not call_returned():
                # The native call outcome is unknown: no blind compensating call.
                self.failed = True
        # Ownership is recorded before calling any fallible proof callback.
        self._decremented = True
        self._expected_balance = count + 1
        self.counter_refused = count != -1
        safe_after = count == -1 and safe()
        self.accepted = safe_after and not self.is_closed()
        if not self.accepted:
            self.retire()
        return self.accepted

    def observe(self, safe: Callable[[], bool]) -> bool:
        """Loss of foreign preservation revokes this scope permanently."""
        self._check_thread()
        if not self.accepted or self.is_closed():
            return False
        if not safe():
            self.failed = True
            self.retire()
        return self.accepted

    def retire(self) -> None:
        """Run on owner thread BEFORE HWND destruction; failed balance is terminal."""
        self._check_thread()
        self.accepted = False
        self._closed = True
        if self._decremented and not self._balanced:
            # Do not retry an uncertain call and accidentally over-increment.
            self._balanced = True
            completed = [False]

            def call_completed() -> bool:
                return completed[0]

            try:
                show = True
                count = self._show(show)
                completed[0] = True
                self.balance_confirmed = count == self._expected_balance
                self.accounting_confirmed = self.balance_confirmed
                if not self.balance_confirmed:
                    self.failed = True
            finally:
                if not call_completed():
                    self.failed = True
                # Completion is separate from 'attempted' if a callback raises.
                self._decremented = False

    @property
    def balance_attempted(self) -> bool:
        """Whether the single balancing call was attempted, not proven successful."""
        return self._balanced

    @property
    def crash_eligible(self) -> bool:
        """Only a currently admitted, unbalanced owner may enter forced termination."""
        self._check_thread()
        return (
            self.accepted
            and self._decremented
            and not self._balanced
            and not self._closed
        )
