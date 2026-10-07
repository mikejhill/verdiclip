"""Typed user settings and their JSON persistence."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, fields, replace
from enum import StrEnum
from pathlib import Path
from typing import Final

from verdiclip.document.annotations import JsonObject, JsonValue
from verdiclip.document.style import RED, Color
from verdiclip.exceptions import SettingsError

logger = logging.getLogger(__name__)

SETTINGS_VERSION: Final = 1


class Theme(StrEnum):
    """Window color scheme."""

    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"


class ImageFormat(StrEnum):
    """Image file formats VerdiClip can write."""

    PNG = "png"
    JPG = "jpg"
    BMP = "bmp"
    GIF = "gif"
    TIFF = "tiff"
    WEBP = "webp"


@dataclass(frozen=True, slots=True)
class CaptureSettings:
    """Capture behavior; the three after-capture actions combine freely."""

    open_editor: bool = True
    copy_to_clipboard: bool = False
    save_to_file: bool = False
    show_magnifier: bool = True

    @property
    def has_action(self) -> bool:
        """True if at least one after-capture action is on."""
        return self.open_editor or self.copy_to_clipboard or self.save_to_file


@dataclass(frozen=True, slots=True)
class HotkeySettings:
    """Global hotkey bindings as canonical strings; empty disables a binding."""

    region: str = "print_screen"
    window: str = "alt+print_screen"
    fullscreen: str = "ctrl+print_screen"
    repeat: str = "shift+print_screen"


@dataclass(frozen=True, slots=True)
class OutputSettings:
    """Where and how images are saved."""

    directory: Path = field(
        default_factory=lambda: Path.home() / "Pictures" / "VerdiClip"
    )
    filename_pattern: str = "Screenshot {date} {time}"
    image_format: ImageFormat = ImageFormat.PNG
    jpeg_quality: int = 90

    def __post_init__(self) -> None:
        """Validate the JPEG quality range."""
        if not 1 <= self.jpeg_quality <= 100:  # noqa: PLR2004 — quality range
            msg = f"JPEG quality must be 1-100, got {self.jpeg_quality}"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class EditorSettings:
    """Starting style for new annotations."""

    stroke_color: Color = RED
    stroke_width: float = 3.0
    font_family: str = "Segoe UI"
    font_size: int = 18

    def __post_init__(self) -> None:
        """Reject sizes that cannot construct a valid annotation style."""
        if self.stroke_width <= 0 or self.font_size <= 0:
            msg = "Editor stroke width and font size must be positive"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class AppearanceSettings:
    """How the app looks."""

    theme: Theme = Theme.SYSTEM


@dataclass(frozen=True, slots=True)
class StartupSettings:
    """Process startup behavior."""

    run_at_login: bool = False


@dataclass(frozen=True, slots=True)
class Settings:
    """All user settings."""

    capture: CaptureSettings = field(default_factory=CaptureSettings)
    hotkeys: HotkeySettings = field(default_factory=HotkeySettings)
    output: OutputSettings = field(default_factory=OutputSettings)
    editor: EditorSettings = field(default_factory=EditorSettings)
    appearance: AppearanceSettings = field(default_factory=AppearanceSettings)
    startup: StartupSettings = field(default_factory=StartupSettings)


class SettingsStore:
    """Load and save ``Settings`` as JSON, tolerating missing or bad fields."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @classmethod
    def default_path(cls) -> Path:
        """Return ``%APPDATA%/VerdiClip/settings.json`` (home fallback)."""
        base = os.environ.get("APPDATA")
        root = Path(base) if base is not None and base else Path.home() / ".config"
        return root / "VerdiClip" / "settings.json"

    @property
    def path(self) -> Path:
        """Return the settings file path."""
        return self._path

    def load(self) -> Settings:
        """Return stored settings; defaults for anything missing or invalid."""
        if not self._path.exists():
            return Settings()
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as err:
            logger.warning("Ignoring unreadable settings file %s: %s", self._path, err)
            return Settings()
        if not isinstance(raw, dict):
            logger.warning("Ignoring settings file %s: not an object", self._path)
            return Settings()
        return SettingsCodec.decode(raw)

    def save(self, settings: Settings) -> None:
        """Write ``settings`` atomically.

        Raises:
            SettingsError: If the file cannot be written.
        """
        payload = json.dumps(SettingsCodec.encode(settings), indent=2)
        temp = self._path.with_suffix(".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temp.write_text(payload, encoding="utf-8")
            temp.replace(self._path)
        except OSError as err:
            msg = f"Could not save settings to {self._path}: {err}"
            raise SettingsError(msg) from err


type SettingsSection = (
    CaptureSettings
    | HotkeySettings
    | OutputSettings
    | EditorSettings
    | AppearanceSettings
    | StartupSettings
)

# Pre-0.2.1 files stored one choice; map it onto the independent switches
_LEGACY_AFTER_CAPTURE: dict[str, dict[str, JsonValue]] = {
    "editor": {"open_editor": True, "copy_to_clipboard": False, "save_to_file": False},
    "clipboard": {
        "open_editor": False,
        "copy_to_clipboard": True,
        "save_to_file": False,
    },
    "file": {"open_editor": False, "copy_to_clipboard": False, "save_to_file": True},
}


class SettingsCodec:
    """Convert ``Settings`` to and from JSON-compatible dictionaries."""

    @classmethod
    def encode(cls, settings: Settings) -> JsonObject:
        """Return a JSON-compatible dictionary."""
        result: JsonObject = {"version": SETTINGS_VERSION}
        for section_field in fields(settings):
            section = getattr(settings, section_field.name)
            result[section_field.name] = {
                f.name: cls._encode_value(getattr(section, f.name))
                for f in fields(section)
            }
        return result

    @classmethod
    def decode(cls, raw: dict[str, JsonValue]) -> Settings:
        """Return settings parsed from ``raw``, keeping defaults for bad values."""
        cls._migrate(raw)
        settings = Settings()
        for section_field in fields(settings):
            section_raw = raw.get(section_field.name)
            if not isinstance(section_raw, dict):
                continue
            section = getattr(settings, section_field.name)
            parsed = cls._decode_section(section, section_raw, section_field.name)
            settings = replace(settings, **{section_field.name: parsed})
        return settings

    @staticmethod
    def _migrate(raw: dict[str, JsonValue]) -> None:
        """Upgrade older settings layouts in place."""
        capture = raw.get("capture")
        if not isinstance(capture, dict):
            return
        legacy = capture.pop("after_capture", None)
        if isinstance(legacy, str) and legacy in _LEGACY_AFTER_CAPTURE:
            for key, value in _LEGACY_AFTER_CAPTURE[legacy].items():
                capture.setdefault(key, value)

    @classmethod
    def _decode_section(
        cls, section: SettingsSection, raw: dict[str, JsonValue], name: str
    ) -> SettingsSection:
        """Apply each valid value in ``raw`` onto ``section``."""
        for f in fields(section):
            if f.name not in raw:
                continue
            current = getattr(section, f.name)
            try:
                value = cls._decode_value(current, raw[f.name])
                section = replace(section, **{f.name: value})
            except (TypeError, ValueError) as err:
                logger.warning("Ignoring invalid setting %s.%s: %s", name, f.name, err)
        return section

    @staticmethod
    def _encode_value(value: object) -> JsonValue:
        """Encode one setting value."""
        match value:
            case Color():
                return value.hex
            case Path():
                return str(value)
            case StrEnum():
                return value.value
            case bool() | int() | float() | str():
                return value
            case _:
                msg = f"Cannot encode setting of type {type(value).__name__}"
                raise TypeError(msg)

    @classmethod
    def _decode_value(cls, current: object, raw: JsonValue) -> object:
        """Decode ``raw`` into the type of ``current``.

        Raises:
            TypeError: If ``raw`` has the wrong JSON type.
            ValueError: If ``raw`` has the right type but an invalid value.
        """
        if isinstance(current, bool):
            if isinstance(raw, bool):
                return raw
        elif isinstance(current, int):
            if isinstance(raw, int) and not isinstance(raw, bool):
                return raw
        elif isinstance(current, float):
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                return float(raw)
        elif isinstance(raw, str):
            return cls._decode_text(current, raw)
        msg = f"expected {type(current).__name__}, got {raw!r}"
        raise TypeError(msg)

    @staticmethod
    def _decode_text(current: object, raw: str) -> object:
        """Decode a string setting into the type of ``current``."""
        if isinstance(current, Color):
            return Color.from_hex(raw)
        if isinstance(current, Path):
            return Path(raw)
        if isinstance(current, StrEnum):
            return type(current)(raw)
        if isinstance(current, str):
            return raw
        msg = f"expected {type(current).__name__}, got {raw!r}"
        raise TypeError(msg)
