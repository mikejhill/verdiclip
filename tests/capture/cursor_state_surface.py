"""One scoped NULL write site, exclusively within a natural owned callback."""

import ctypes.wintypes as w
from collections.abc import Callable
from typing import override

from tests.capture.cursor_admission_surface import NativeState, OwnedSurface
from tests.capture.cursor_state_policy import CursorStatePolicy


class CursorStateSurface(OwnedSurface):
    """Never restore another client's handle, class cursor or visibility count."""

    def __init__(
        self, state: NativeState, policy: CursorStatePolicy, safe: Callable[[], bool]
    ) -> None:
        super().__init__(state, policy, safe)
        self.policy = policy
        self.api.SetCursor.argtypes = [w.HANDLE]
        self.api.SetCursor.restype = w.HANDLE

    @override
    def _message(self, hwnd: int, message: int, wp: int, lp: int) -> int:
        if message != 0x20:
            return super()._message(hwnd, message, wp, lp)
        try:
            state = self.state.read()
            owned = (
                hwnd == wp == self.handle != 0
                and lp & 0xFFFF == 1
                and self.state.hit(state) == self.handle
            )
            if self.policy.admit_write(state, owned=owned, safe=self.safe()):
                self.api.SetCursor(None)
                self.policy.wrote()
                self.policy.observe(self.state.read(), safe=self.safe())
        except Exception:  # noqa: BLE001 - latch callback errors and still retire
            self.policy.fail()
        return 1
