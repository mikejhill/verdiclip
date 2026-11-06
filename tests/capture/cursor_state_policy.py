"""Strict phase policy for a disposable hosted cursor-state falsification."""

from typing import override

from tests.capture.cursor_admission_policy import Admission, PreservedState


class CursorStatePolicy(Admission):
    """Only the cursor shape can differ during demonstrated owned input."""

    def __init__(self, baseline: PreservedState) -> None:
        super().__init__(baseline)
        self.writes = 0
        self.retired = False
        self.first_restored: bool | None = None

    @override
    def observe(self, state: PreservedState, *, safe: bool) -> bool:
        """Keep GUI recipients strict while allowing only the intended NULL state."""
        with self._lock:
            unchanged = state == self.baseline
            if self.writes and not self.retired:
                unchanged = (
                    state.foreground == self.baseline.foreground
                    and state.gui == self.baseline.gui
                    and state.cursor[2:] == self.baseline.cursor[2:]
                    and state.cursor[1] == 0
                    and state.cursor[0] == 0
                )
            self.failed |= not safe or not unchanged
            return not self.failed

    def admit_write(self, state: PreservedState, *, owned: bool, safe: bool) -> bool:
        """Reject stale ownership, menu state, capture and repeated phase misuse."""
        with self._lock:
            if self.retired or state.gui[3] or state.gui[4] or state.gui[0] & 0x14:
                self.failed = True
            if not owned or not self.observe(state, safe=safe):
                self.failed = True
            if self.messages >= 64:
                self.failed = True
            if self.failed:
                return False
            self.messages += 1
            return True

    def wrote(self) -> None:
        """Record only a returned scoped API invocation."""
        with self._lock:
            self.writes += 1

    def preserved(self) -> bool:
        """Read the shared failure latch without narrowing mutable state."""
        with self._lock:
            return not self.failed

    def retire(self, first: PreservedState) -> bool:
        """The first observation decides; a later match cannot repair failure."""
        with self._lock:
            if self.retired:
                self.failed = True
                return False
            self.retired = True
            self.first_restored = first == self.baseline
            return self.observe(first, safe=True)
