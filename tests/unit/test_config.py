"""Tests for verdiclip.config.Config."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from verdiclip.config import Config

if TYPE_CHECKING:
    from pathlib import Path


class TestDefaultConfigValues:
    """Config created with no existing file gets all defaults."""

    def test_default_config_values(self, tmp_path: Path) -> None:
        config = Config(config_path=tmp_path / "config.json")
        assert config.get("capture.default_action") == "editor", (
            f"Expected default_action 'editor', got {config.get('capture.default_action')}"
        )
        assert config.get("save.default_format") == "png", (
            f"Expected save.default_format to be 'png', got {config.get('save.default_format')}"
        )
        assert config.get("hotkeys.region") == "print_screen", (
            f"Expected hotkeys.region to be 'print_screen', got {config.get('hotkeys.region')}"
        )
        assert config.get("editor.default_stroke_width") == 3, (
            f"Expected default_stroke_width 3, got {config.get('editor.default_stroke_width')}"
        )
        assert config.get("startup.minimize_to_tray") is True, (
            f"Expected minimize_to_tray True, got {config.get('startup.minimize_to_tray')}"
        )


class TestGetNestedValue:
    """Dot-notation get retrieves nested values."""

    def test_get_nested_value(self, tmp_config: Config) -> None:
        assert tmp_config.get("save.default_format") == "png", (
            f"Expected save.default_format to be 'png', got {tmp_config.get('save.default_format')}"
        )
        assert tmp_config.get("capture.include_cursor") is False, (
            f"Expected include_cursor False, got {tmp_config.get('capture.include_cursor')}"
        )
        assert tmp_config.get("editor.default_font_size") == 14, (
            f"Expected default_font_size 14, got {tmp_config.get('editor.default_font_size')}"
        )


class TestGetNonexistentKey:
    """Missing keys return the provided default."""

    def test_get_nonexistent_key(self, tmp_config: Config) -> None:
        assert tmp_config.get("nonexistent.key", "fallback") == "fallback", (
            f"Expected 'fallback' default, got {tmp_config.get('nonexistent.key', 'fallback')}"
        )
        assert tmp_config.get("capture.missing", 42) == 42, (
            f"Expected missing key to return 42, got {tmp_config.get('capture.missing', 42)}"
        )
        assert tmp_config.get("totally.absent") is None, (
            f"Expected missing key to return None, got {tmp_config.get('totally.absent')}"
        )


class TestSetAndPersist:
    """.set() writes to disk and survives reload."""

    def test_set_and_persist(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.json"
        config = Config(config_path=config_path)
        config.set("save.default_format", "jpg")
        assert config.get("save.default_format") == "jpg", (
            f"Expected default_format 'jpg' after set, got {config.get('save.default_format')}"
        )

        reloaded = Config(config_path=config_path)
        assert reloaded.get("save.default_format") == "jpg", (
            f"Expected default_format 'jpg' after reload, got {reloaded.get('save.default_format')}"
        )


class TestMergePreservesExisting:
    """Existing values in config file aren't overwritten by defaults; missing keys get defaults."""

    def test_merge_preserves_existing_and_fills_missing(self, sample_config_file: Path) -> None:
        config = Config(config_path=sample_config_file)
        assert config.get("capture.default_action") == "clipboard", (
            "Existing 'clipboard' value should be preserved, not overwritten by default"
        )
        assert config.get("save.default_format") == "jpg", (
            "Existing 'jpg' value should be preserved, not overwritten by 'png' default"
        )
        assert config.get("save.jpg_quality") == 75, "Existing jpg_quality=75 should be preserved"
        assert config.get("capture.include_cursor") is False, (
            "Missing key should be filled with default value"
        )
        assert config.get("editor.default_stroke_width") == 3, (
            "Missing key should be filled with default value"
        )


class TestInvalidJsonUsesDefaults:
    """Corrupt JSON file falls back to full defaults."""

    def test_invalid_json_uses_defaults(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.json"
        config_path.write_text("{bad json!!!", encoding="utf-8")
        config = Config(config_path=config_path)
        assert config.get("save.default_format") == "png", (
            f"Expected 'png' after invalid JSON, got {config.get('save.default_format')}"
        )
        assert config.get("capture.default_action") == "editor", (
            f"Expected default after invalid JSON, got {config.get('capture.default_action')}"
        )


class TestReset:
    """reset() restores all values to defaults."""

    def test_reset_restores_defaults(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.json"
        config = Config(config_path=config_path)
        config.set("save.default_format", "webp")
        config.set("capture.default_action", "clipboard")
        config.reset()
        assert config.get("save.default_format") == "png", (
            f"Expected format 'png' after reset, got {config.get('save.default_format')}"
        )
        assert config.get("capture.default_action") == "editor", (
            f"Expected action 'editor' after reset, got {config.get('capture.default_action')}"
        )


class TestConfigPathProperty:
    """config_path returns the path passed at construction."""

    def test_returns_construction_path(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.json"
        config = Config(config_path=config_path)
        assert config.config_path == config_path, (
            f"Expected config_path to be {config_path}, got {config.config_path}"
        )


class TestSetNewTopLevelSection:
    """set() with a new top-level section creates it."""

    def test_creates_new_section(self, tmp_config: Config) -> None:
        tmp_config.set("newsection.key", "value")
        assert tmp_config.get("newsection.key") == "value", (
            f"Expected newsection.key to be 'value', got {tmp_config.get('newsection.key')}"
        )

    def test_new_section_persists(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.json"
        config = Config(config_path=config_path)
        config.set("newsection.key", "value")
        reloaded = Config(config_path=config_path)
        assert reloaded.get("newsection.key") == "value", (
            f"Expected newsection.key to persist as 'value', got {reloaded.get('newsection.key')}"
        )


class TestSaveAndLoad:
    """JSON file on disk matches in-memory data after set()."""

    def test_disk_matches_memory_after_set(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.json"
        config = Config(config_path=config_path)
        config.set("save.default_format", "tiff")

        with config_path.open("r", encoding="utf-8") as f:
            on_disk = json.load(f)

        assert on_disk == config.data, (
            f"Expected disk data to match in-memory data, got disk={on_disk}"
        )
