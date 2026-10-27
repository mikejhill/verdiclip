"""Native fixture cleanup must not touch the user's clipboard."""

from types import SimpleNamespace

import pytest
from tests import conftest


@pytest.mark.parametrize("platform", ["windows", "xcb", "cocoa", "offscreen"])
def test_clipboard_cleanup_is_exclusively_an_offscreen_workaround(
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
) -> None:
    """Fake platform names prove native teardown never even obtains a clipboard."""
    cleared: list[bool] = []

    def clipboard() -> SimpleNamespace:
        if platform != "offscreen":
            pytest.fail("Native teardown tried to access the system clipboard")
        return SimpleNamespace(clear=lambda: cleared.append(True))

    monkeypatch.setattr(
        conftest,
        "QGuiApplication",
        SimpleNamespace(
            instance=object,
            platformName=lambda: platform,
            clipboard=clipboard,
        ),
    )
    conftest.clear_offscreen_clipboard()
    assert cleared == ([True] if platform == "offscreen" else [])
