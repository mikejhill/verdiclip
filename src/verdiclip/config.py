"""Configuration manager for VerdiClip."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_CONFIG: dict[str, Any] = {
    "capture": {
        "default_action": "editor",
        "include_cursor": False,
        "region_magnifier": True,
        "window_decorations": True,
    },
    "save": {
        "default_directory": str(Path.home() / "Pictures" / "VerdiClip"),
        "default_format": "png",
        "jpg_quality": 90,
        "auto_save_enabled": False,
        "filename_pattern": "Screenshot_{datetime}",
    },
    "hotkeys": {
        "region": "print_screen",
        "fullscreen": "ctrl+print_screen",
        "window": "alt+print_screen",
        "window_pick": "ctrl+shift+print_screen",
        "repeat": "shift+print_screen",
    },
    "editor": {
        "default_stroke_color": "#FF0000",
        "default_fill_color": "#00000000",
        "default_stroke_width": 3,
        "default_font_family": "Arial",
        "default_font_size": 14,
    },
    "startup": {
        "run_at_login": False,
        "minimize_to_tray": True,
    },
}


class Config:
    """Manages application configuration with JSON persistence."""

    def __init__(self, config_path: Path | None = None) -> None:
        self._path = config_path or (
            Path.home() / "AppData" / "Roaming" / "VerdiClip" / "config.json"
        )
        self._data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        """Load config from disk, falling back to defaults."""
        if self._path.exists():
            try:
                with self._path.open("r", encoding="utf-8") as f:
                    self._data = json.load(f)
                logger.info("Loaded config from %s", self._path)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load config, using defaults: %s", e)
                self._data = {}
        else:
            logger.info("No config file found, using defaults.")
            self._data = {}
        self._merge_defaults(self._data, DEFAULT_CONFIG)

    def _merge_defaults(self, target: dict, defaults: dict) -> None:
        """Recursively merge defaults into target without overwriting existing values."""
        for key, default_value in defaults.items():
            if key not in target:
                target[key] = (
                    default_value.copy() if isinstance(default_value, dict) else default_value
                )
            elif isinstance(default_value, dict) and isinstance(target[key], dict):
                self._merge_defaults(target[key], default_value)

    def save(self) -> None:
        """Persist current configuration to disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)
        logger.debug("Config saved to %s", self._path)

    def get(self, dotted_key: str, default: Any = None) -> Any:
        """Get a config value using dot notation (e.g., 'save.default_format')."""
        keys = dotted_key.split(".")
        current: Any = self._data
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return default
        return current

    def set(self, dotted_key: str, value: Any) -> None:
        """Set a config value using dot notation and auto-save."""
        keys = dotted_key.split(".")
        current = self._data
        for key in keys[:-1]:
            if key not in current or not isinstance(current[key], dict):
                current[key] = {}
            current = current[key]
        current[keys[-1]] = value
        self.save()

    @property
    def data(self) -> dict[str, Any]:
        """Return the full configuration dictionary."""
        return self._data
