"""VerdiClip: fast, faithful screenshot capture and annotation for Windows."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Final

APP_NAME: Final = "VerdiClip"


class _Version:
    """Read the installed version; pyproject.toml is the single source."""

    @staticmethod
    def installed() -> str:
        """Return the package version, or a placeholder when not installed."""
        try:
            return version("verdiclip")
        except PackageNotFoundError:
            return "0.0.0+unknown"


VERSION: Final = _Version.installed()
