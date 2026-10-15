"""VerdiClip: fast, faithful screenshot capture and annotation for Windows."""

from __future__ import annotations

import json
from importlib.metadata import Distribution, PackageNotFoundError, distribution
from typing import Final

APP_NAME: Final = "VerdiClip"


class _Version:
    """Read the installed version; pyproject.toml is the single source."""

    @staticmethod
    def installed() -> str:
        """Return the package version, or a placeholder when not installed."""
        try:
            return _Version.of(distribution("verdiclip"))
        except PackageNotFoundError:
            return "0.0.0+unknown"

    @staticmethod
    def of(dist: Distribution) -> str:
        """Return the release version, or the next one as a beta for a checkout.

        An editable install means VerdiClip runs from source (``uv sync`` in a
        checkout), which is ahead of the last release in pyproject.toml.
        """
        if not _Version.is_editable(dist):
            return dist.version
        return _Version.next_beta(dist.version)

    @staticmethod
    def is_editable(dist: Distribution) -> bool:
        """Return whether ``dist`` is an editable install of a source tree."""
        text = dist.read_text("direct_url.json")
        if text is None:
            return False
        try:
            info = json.loads(text)
        except json.JSONDecodeError:
            return False
        dir_info = info.get("dir_info") if isinstance(info, dict) else None
        return isinstance(dir_info, dict) and dir_info.get("editable") is True

    @staticmethod
    def next_beta(release: str) -> str:
        """Return the next patch release after ``release``, marked as a beta."""
        major, _, rest = release.partition(".")
        minor, _, patch = rest.partition(".")
        if not (major.isdigit() and minor.isdigit() and patch.isdigit()):
            return f"{release}-beta"
        return f"{major}.{minor}.{int(patch) + 1}-beta"


VERSION: Final = _Version.installed()
