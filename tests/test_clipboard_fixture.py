"""Native fixture cleanup must not touch the user's clipboard."""

import ast
from pathlib import Path
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


def test_no_application_cleanup_does_not_obtain_clipboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-Qt tests must never access either clipboard or platform state."""

    def forbidden() -> None:
        pytest.fail("Cleanup accessed Qt without an application")

    monkeypatch.setattr(
        conftest,
        "QGuiApplication",
        SimpleNamespace(
            instance=lambda: None, platformName=forbidden, clipboard=forbidden
        ),
    )
    conftest.clear_offscreen_clipboard()


@pytest.mark.parametrize(
    ("filename", "class_name", "marker"),
    [
        ("test_keyboard_hook.py", "TestLowLevelKeyboardHook", "native_hooks"),
        ("test_associations.py", "TestWinRegAssociations", "native_registry"),
    ],
)
def test_known_native_side_effect_classes_keep_exclusion_markers(
    filename: str,
    class_name: str,
    marker: str,
) -> None:
    """Inspect source without importing or executing native test bodies."""
    source = Path(__file__).parent / "platform" / filename
    tree = ast.parse(source.read_text(encoding="utf-8"))
    matches = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    ]
    assert len(matches) == 1
    assert f"pytest.mark.{marker}" in {
        ast.unparse(decorator) for decorator in matches[0].decorator_list
    }
