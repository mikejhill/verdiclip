"""Expand filename patterns such as ``{date}_{time}_{title}``."""

from __future__ import annotations

import re
import string
from datetime import datetime
from pathlib import Path
from typing import ClassVar, Final

from verdiclip.settings import DEFAULT_FILENAME_PATTERN, ImageFormat

_INVALID_CHARS: Final = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WHITESPACE: Final = re.compile(r"\s+")
# Long browser titles would otherwise push paths past Windows' MAX_PATH
MAX_TITLE_CHARS: Final = 80


class FilenamePattern:
    """A filename template with ``{date}``, ``{time}``, ``{title}``, ``{counter}``."""

    TOKENS: ClassVar[frozenset[str]] = frozenset({"date", "time", "title", "counter"})

    def __init__(self, pattern: str) -> None:
        self._pattern = pattern.strip() or DEFAULT_FILENAME_PATTERN
        self._validate()

    @property
    def pattern(self) -> str:
        """Return the template text."""
        return self._pattern

    def resolve(
        self,
        directory: Path,
        image_format: ImageFormat,
        *,
        moment: datetime,
        title: str = "",
    ) -> Path:
        """Return the first non-existing path in ``directory`` for this pattern.

        ``{counter}`` takes the lowest value that gives an unused name. Without
        it, a `` (2)`` style suffix is added on collision, or ``_2`` when the
        pattern has no spaces.
        """
        suffix = f".{image_format.value}"
        uses_counter = "{counter}" in self._pattern
        for attempt in range(1, 10_000):
            stem = self._expand(moment, title, attempt if uses_counter else 1)
            if not uses_counter and attempt > 1:
                stem = f"{stem} ({attempt})" if self._spaced else f"{stem}_{attempt}"
            candidate = directory / f"{stem}{suffix}"
            if not candidate.exists():
                return candidate
        msg = f"No free filename for pattern {self._pattern!r} in {directory}"
        raise FileExistsError(msg)

    @property
    def _spaced(self) -> bool:
        """True if the pattern itself contains whitespace."""
        return _WHITESPACE.search(self._pattern) is not None

    def _expand(self, moment: datetime, title: str, counter: int) -> str:
        """Substitute tokens and strip characters Windows forbids.

        A pattern without spaces yields a name without spaces: whitespace
        runs in the title become ``_``.
        """
        title = title.strip()[:MAX_TITLE_CHARS].rstrip() or "Screenshot"
        if not self._spaced:
            title = _WHITESPACE.sub("_", title)
        text = self._pattern.format(
            date=moment.strftime("%Y-%m-%d"),
            time=moment.strftime("%H-%M-%S"),
            title=title,
            counter=counter,
        )
        cleaned = _INVALID_CHARS.sub("_", text).strip(" .")
        return cleaned or "Screenshot"

    def _validate(self) -> None:
        """Reject unknown or malformed tokens."""
        try:
            names = {
                name
                for _, name, _, _ in string.Formatter().parse(self._pattern)
                if name is not None
            }
        except ValueError as err:
            msg = f"Malformed filename pattern {self._pattern!r}: {err}"
            raise ValueError(msg) from err
        unknown = names - self.TOKENS
        if unknown:
            allowed = ", ".join(f"{{{t}}}" for t in sorted(self.TOKENS))
            msg = (
                f"Unknown token(s) {sorted(unknown)} in filename pattern; use {allowed}"
            )
            raise ValueError(msg)
