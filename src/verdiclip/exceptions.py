"""Exception hierarchy for VerdiClip."""

from __future__ import annotations


class AppError(Exception):
    """Base class for all expected VerdiClip errors."""


class SettingsError(AppError):
    """Settings file is unreadable or holds invalid values."""


class CaptureError(AppError):
    """A screen capture could not be produced."""


class HotkeyError(AppError):
    """A hotkey string is invalid or could not be registered."""


class DeliveryError(AppError):
    """An image could not be saved, copied, or printed."""


class CodecError(AppError):
    """Serialized annotation data is malformed."""


class PlatformError(AppError):
    """A Windows API call failed."""
