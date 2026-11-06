"""Finite test-only cursor ownership evidence, never visibility qualification."""

from dataclasses import dataclass, field
from threading import RLock


@dataclass(frozen=True)
class PreservedState:
    """Opaque native identities remain in memory and never enter receipts."""

    foreground: int
    gui: tuple[int, ...]
    cursor: tuple[int, int, int, int]


@dataclass
class Admission:
    """A natural cursor message is necessary, not sufficient to prove hiding."""

    baseline: PreservedState
    messages: int = 0
    failed: bool = False
    _lock: RLock = field(default_factory=RLock, repr=False)

    def fail(self) -> None:
        """Latch asynchronous callback failure without racing preservation reads."""
        with self._lock:
            self.failed = True

    def observe(self, state: PreservedState, *, safe: bool) -> bool:
        """Preservation violations latch and cannot be repaired by later equality."""
        with self._lock:
            self.failed |= not safe or state != self.baseline
            return not self.failed

    def cursor_message(self, state: PreservedState, *, owned: bool, safe: bool) -> None:
        """Only the owned callback may record this; no synthesized test message."""
        with self._lock:
            if not self.observe(state, safe=safe) or not owned:
                self.failed = True
            elif self.messages < 64:
                self.messages += 1
            else:
                self.failed = True

    def verdict(self, *, broker_active: bool, destroyed: bool) -> str:
        """Even positive admission makes no cursor-visibility or crash claim."""
        if self.failed or not broker_active or not destroyed:
            return "unsupported-preservation-or-cleanup"
        if not self.messages:
            return "unsupported-no-natural-owned-message"
        return "natural-owned-message-observed"
