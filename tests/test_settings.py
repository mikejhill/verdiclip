"""Test tolerant settings persistence and strict field decoding."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from verdiclip.document.annotations import JsonValue
from verdiclip.document.style import Color
from verdiclip.exceptions import SettingsError
from verdiclip.settings import (
    AppearanceSettings,
    CaptureSettings,
    EditorSettings,
    HotkeySettings,
    ImageFormat,
    OutputSettings,
    Settings,
    SettingsCodec,
    SettingsStore,
    StartupSettings,
    Theme,
)


class TestSettingsStore:
    """Settings files, defaults, warnings, and atomic write errors."""

    def test_missing(self, tmp_path: Path) -> None:
        """A missing settings file returns defaults without creating files."""
        store = SettingsStore(tmp_path / "settings.json")

        loaded = store.load()

        assert loaded == Settings()
        assert store.path == tmp_path / "settings.json"
        assert not store.path.exists()

    @pytest.mark.parametrize(
        "payload", ["{", "[]", "null", "42"], ids=["bad-json", "list", "null", "number"]
    )
    def test_bad_file(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture, payload: str
    ) -> None:
        """Bad JSON and non-object roots fall back with a warning."""
        path = tmp_path / "settings.json"
        path.write_text(payload, encoding="utf-8")

        loaded = SettingsStore(path).load()

        assert loaded == Settings()
        assert "Ignoring" in caplog.text

    @pytest.mark.parametrize(
        ("section", "field", "value"),
        [
            ("capture", "open_editor", "yes"),
            ("appearance", "theme", "purple"),
            ("capture", "show_magnifier", 1),
            ("startup", "run_at_login", 1),
            ("hotkeys", "region", 1),
            ("output", "directory", 1),
            ("output", "image_format", "unknown"),
            ("output", "jpeg_quality", True),
            ("output", "jpeg_quality", 0),
            ("output", "jpeg_quality", 101),
            ("editor", "stroke_color", "bad"),
            ("editor", "stroke_width", True),
            ("editor", "font_size", True),
            ("editor", "font_family", False),
            ("editor", "stroke_width", 0),
            ("editor", "font_size", -1),
        ],
        ids=[
            "action",
            "theme",
            "bool-capture",
            "bool-startup",
            "hotkey",
            "directory",
            "format",
            "bool-quality",
            "low-quality",
            "high-quality",
            "color",
            "bool-width",
            "bool-font",
            "family",
            "zero-width",
            "negative-font",
        ],
    )
    def test_invalid_field(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        section: str,
        field: str,
        value: JsonValue,
    ) -> None:
        """Each invalid field retains its default and logs its exact name."""
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({section: {field: value}}), encoding="utf-8")

        loaded = SettingsStore(path).load()

        assert loaded == Settings()
        assert f"{section}.{field}" in caplog.text

    def test_round_trip(self, tmp_path: Path) -> None:
        """All supported types survive an atomic save and reload."""
        settings = Settings(
            capture=CaptureSettings(
                open_editor=False, save_to_file=True, show_magnifier=False
            ),
            appearance=AppearanceSettings(theme=Theme.DARK),
            hotkeys=HotkeySettings(region="", window="ctrl+w"),
            output=OutputSettings(
                directory=tmp_path / "images",
                filename_pattern="{title} {counter}",
                image_format=ImageFormat.JPG,
                jpeg_quality=75,
            ),
            editor=EditorSettings(
                stroke_color=Color(1, 2, 3, 4),
                stroke_width=5.5,
                font_family="Arial",
                font_size=24,
            ),
            startup=StartupSettings(run_at_login=True),
        )
        store = SettingsStore(tmp_path / "nested" / "settings.json")

        store.save(settings)

        assert store.load() == settings
        assert not store.path.with_suffix(".tmp").exists()

    def test_save_failure(self, tmp_path: Path) -> None:
        """A blocked parent folder becomes SettingsError."""
        blocker = tmp_path / "file"
        blocker.write_text("blocked", encoding="utf-8")
        store = SettingsStore(blocker / "settings.json")

        with pytest.raises(SettingsError, match="Could not save settings"):
            store.save(Settings())

    def test_unreadable_file(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An unreadable settings path returns defaults with a warning."""
        store = SettingsStore(tmp_path)

        loaded = store.load()

        assert loaded == Settings()
        assert "unreadable" in caplog.text

    @pytest.mark.parametrize(
        "appdata", [None, "", "custom"], ids=["missing", "empty", "configured"]
    )
    def test_default_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, appdata: str | None
    ) -> None:
        """APPDATA controls the default path with a home fallback."""
        if appdata is None:
            monkeypatch.delenv("APPDATA", raising=False)
        else:
            monkeypatch.setenv("APPDATA", str(tmp_path) if appdata else "")

        path = SettingsStore.default_path()

        root = (
            tmp_path
            if appdata is not None and appdata != ""
            else Path.home() / ".config"
        )
        assert path == root / "VerdiClip" / "settings.json"


class TestSettingsCodec:
    """Defaults and partial section decoding."""

    def test_partial_sections(self) -> None:
        """Unknown sections are ignored and valid neighboring fields are kept."""
        raw: dict[str, JsonValue] = {
            "editor": {"stroke_width": 7, "font_size": "bad"},
            "capture": [],
            "unknown": {"value": 1},
        }

        loaded = SettingsCodec.decode(raw)

        assert loaded == replace(
            Settings(), editor=replace(EditorSettings(), stroke_width=7.0)
        )


class TestOutputSettings:
    """JPEG quality constructor bounds."""

    @pytest.mark.parametrize(
        "quality", [0, -1, 101], ids=["zero", "negative", "above-max"]
    )
    def test_invalid_quality(self, quality: int) -> None:
        """JPEG quality outside 1 to 100 is rejected."""
        with pytest.raises(ValueError, match="JPEG quality must be 1-100"):
            OutputSettings(jpeg_quality=quality)


class TestLegacyMigration:
    """Older settings files keep working."""

    @pytest.mark.parametrize(
        ("legacy", "expected"),
        [
            ("editor", (True, False, False)),
            ("clipboard", (False, True, False)),
            ("file", (False, False, True)),
        ],
    )
    def test_single_after_capture_choice_becomes_switches(
        self, tmp_path: Path, legacy: str, expected: tuple[bool, bool, bool]
    ) -> None:
        """A 0.2.0 "after_capture" value maps onto the independent switches."""
        path = tmp_path / "settings.json"
        path.write_text(
            json.dumps({"capture": {"after_capture": legacy}}), encoding="utf-8"
        )

        capture = SettingsStore(path).load().capture

        assert (
            capture.open_editor,
            capture.copy_to_clipboard,
            capture.save_to_file,
        ) == expected
