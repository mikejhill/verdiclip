"""Expand filename patterns such as ``Screenshot {date} {time}``."""

from __future__ import annotations

import re
import string
from datetime import datetime
from pathlib import Path
from typing import ClassVar, Final

from verdiclip.settings import ImageFormat

_INVALID_CHARS: Final = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class FilenamePattern:
    """A filename template with ``{date}``, ``{time}``, ``{title}``, ``{counter}``."""

    TOKENS: ClassVar[frozenset[str]] = frozenset({"date", "time", "title", "counter"})

    def __init__(self, pattern: str) -> None:
        self._pattern = pattern.strip() or "Screenshot {date} {time}"
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
        it, a `` (2)`` style suffix is added on collision.
        """
        suffix = f".{image_format.value}"
        uses_counter = "{counter}" in self._pattern
        for attempt in range(1, 10_000):
            stem = self._expand(moment, title, attempt if uses_counter else 1)
            if not uses_counter and attempt > 1:
                stem = f"{stem} ({attempt})"
            candidate = directory / f"{stem}{suffix}"
            if not candidate.exists():
                return candidate
        msg = f"No free filename for pattern {self._pattern!r} in {directory}"
        raise FileExistsError(msg)

    def _expand(self, moment: datetime, title: str, counter: int) -> str:
        """Substitute tokens and strip characters Windows forbids."""
        text = self._pattern.format(
            date=moment.strftime("%Y-%m-%d"),
            time=moment.strftime("%H-%M-%S"),
            title=title or "Screenshot",
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
