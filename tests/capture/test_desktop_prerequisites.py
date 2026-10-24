"""Known desktop limitations skip; unknown states cannot conceal regressions."""

from __future__ import annotations

import pytest
from tests.capture.desktop import DesktopSession


@pytest.mark.parametrize(
    ("state", "flags", "expected"),
    [
        (0, 0, "Windows session is locked; unlock its interactive desktop"),
        (4, 1, "Windows session is disconnected; reconnect to its desktop"),
        (4, 0, "Windows session is disconnected; reconnect to its desktop"),
        (0, 1, None),
        (0, -1, None),
        (1, 0, None),
    ],
)
def test_only_known_unsupported_sessions_skip(
    state: int, flags: int, expected: str | None
) -> None:
    """An unlocked or unknown session must still exercise the capture boundary."""
    reason = DesktopSession(state, flags).unavailable_reason()

    assert reason == expected, (
        f"Expected {expected!r} for session ({state}, {flags}), got {reason!r}"
    )
