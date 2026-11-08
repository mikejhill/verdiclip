"""Legacy settings retain supported capture choices without obsolete limits."""

from verdiclip.settings import CaptureDestination, SettingsCodec
from verdiclip.shell.settings_dialog import HOTKEY_FIELDS


def test_legacy_scrolling_limits_are_ignored_without_losing_supported_choices() -> None:
    """Obsolete limits are ignored; the supported scrolling shortcut is retained."""
    settings = SettingsCodec.decode(
        {
            "capture": {"destination": "windows", "open_editor": True},
            "hotkeys": {"region": "ctrl+shift+f8", "scrolling": "ctrl+f9"},
            "scrolling": {"enabled": True, "maximum_steps": 200},
        }
    )
    assert settings.capture.destination is CaptureDestination.WINDOWS
    assert settings.hotkeys.region == "ctrl+shift+f8"
    assert {name for name, _ in HOTKEY_FIELDS} == {
        "region",
        "window",
        "fullscreen",
        "repeat",
        "scrolling",
    }
    encoded = SettingsCodec.encode(settings)
    assert "scrolling" not in encoded
    hotkeys = encoded["hotkeys"]
    assert isinstance(hotkeys, dict)
    assert hotkeys["scrolling"] == "ctrl+f9"
    assert settings.hotkeys.scrolling == "ctrl+f9"


def test_new_default_is_tabs() -> None:
    """Fresh settings select the requested tab destination."""
    assert SettingsCodec.decode({}).capture.destination is CaptureDestination.TABS
