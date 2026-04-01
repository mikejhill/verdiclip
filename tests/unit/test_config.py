"""Tests for verdiclip.config.Config."""

from __future__ import annotations

from typing import TYPE_CHECKING

from verdiclip.config import DEFAULT_CONFIG, Config

if TYPE_CHECKING:
    from pathlib import Path


class TestDefaultConfigValues:
    """Config created with no existing file gets all defaults."""

    def test_default_config_values(self, tmp_path: Path) -> None:
        config = Config(config_path=tmp_path / "config.json")
        assert config.data["capture"]["default_action"] == "editor"
        assert config.data["save"]["default_format"] == "png"
        assert config.data["hotkeys"]["region"] == "print_screen"
        assert config.data["editor"]["default_stroke_width"] == 3
        assert config.data["startup"]["minimize_to_tray"] is True


class TestGetNestedValue:
    """Dot-notation get retrieves nested values."""

    def test_get_nested_value(self, tmp_config: Config) -> None:
        assert tmp_config.get("save.default_format") == "png"
        assert tmp_config.get("capture.include_cursor") is False
        assert tmp_config.get("editor.default_font_size") == 14


class TestGetNonexistentKey:
    """Missing keys return the provided default."""

    def test_get_nonexistent_key(self, tmp_config: Config) -> None:
        assert tmp_config.get("nonexistent.key", "fallback") == "fallback"
        assert tmp_config.get("capture.missing", 42) == 42
        assert tmp_config.get("totally.absent") is None


class TestSetAndPersist:
    """.set() writes to disk and survives reload."""

    def test_set_and_persist(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.json"
        config = Config(config_path=config_path)
        config.set("save.default_format", "jpg")
        assert config.get("save.default_format") == "jpg"

        reloaded = Config(config_path=config_path)
        assert reloaded.get("save.default_format") == "jpg"


class TestMergePreservesExisting:
    """Existing values in config file aren't overwritten by defaults."""

    def test_merge_preserves_existing(self, sample_config_file: Path) -> None:
        config = Config(config_path=sample_config_file)
        assert config.get("capture.default_action") == "clipboard"
        assert config.get("save.default_format") == "jpg"
        assert config.get("save.jpg_quality") == 75
        # Defaults still fill in missing keys
        assert config.get("capture.include_cursor") is False
        assert config.get("editor.default_stroke_width") == 3


class TestInvalidJsonUsesDefaults:
    """Corrupt JSON file falls back to full defaults."""

    def test_invalid_json_uses_defaults(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.json"
        config_path.write_text("{bad json!!!", encoding="utf-8")
        config = Config(config_path=config_path)
        assert config.get("save.default_format") == "png"
        assert config.get("capture.default_action") == DEFAULT_CONFIG["capture"]["default_action"]
