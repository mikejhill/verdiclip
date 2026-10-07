"""Test deterministic filename expansion and collision handling."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from verdiclip.output.naming import FilenamePattern
from verdiclip.settings import ImageFormat


class TestFilenamePattern:
    """Filename tokens, sanitation, and collisions."""

    @pytest.mark.parametrize(
        ("pattern", "title", "expected"),
        [
            (
                "{date} {time} {title} {counter}",
                "Report",
                "2026-01-02 03-04-05 Report 1.png",
            ),
            ("{title}", 'A/B:*?<>|"', "A_B_______.png"),
            ("...", "", "Screenshot.png"),
            ("{title}", "", "Screenshot.png"),
            (" ", "", "Screenshot 2026-01-02 03-04-05.png"),
        ],
        ids=["tokens", "invalid-chars", "empty-stem", "empty-title", "empty-pattern"],
    )
    def test_expand(
        self, tmp_path: Path, pattern: str, title: str, expected: str
    ) -> None:
        """Tokens expand deterministically and forbidden characters are removed."""
        naming = FilenamePattern(pattern)
        moment = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

        path = naming.resolve(tmp_path, ImageFormat.PNG, moment=moment, title=title)

        assert path == tmp_path / expected
        assert naming.pattern

    @pytest.mark.parametrize(
        ("pattern", "first", "second"),
        [
            ("shot", "shot.png", "shot (2).png"),
            ("{counter}", "1.png", "2.png"),
            ("{title} {counter}", "Screenshot 1.png", "Screenshot 2.png"),
        ],
        ids=["suffix", "counter", "title-counter"],
    )
    def test_collision(
        self, tmp_path: Path, pattern: str, first: str, second: str
    ) -> None:
        """Collisions choose the lowest unused name without overwriting."""
        (tmp_path / first).write_text("existing", encoding="utf-8")
        moment = datetime(2026, 1, 2, tzinfo=UTC)

        path = FilenamePattern(pattern).resolve(
            tmp_path, ImageFormat.PNG, moment=moment
        )

        assert path == tmp_path / second
        assert (tmp_path / first).read_text(encoding="utf-8") == "existing"

    @pytest.mark.parametrize(
        ("pattern", "message"),
        [
            ("{unknown}", "Unknown token"),
            ("{date", "Malformed"),
            ("{}", "Unknown token"),
        ],
        ids=["unknown", "unclosed", "positional"],
    )
    def test_invalid_pattern(self, pattern: str, message: str) -> None:
        """Malformed and unknown filename tokens fail before use."""
        with pytest.raises(ValueError, match=message):
            FilenamePattern(pattern)

    def test_exhausted_names(self, tmp_path: Path) -> None:
        """Exhausting all counter candidates raises a contextual file error."""
        for counter in range(1, 10_000):
            (tmp_path / f"{counter}.png").touch()
        pattern = FilenamePattern("{counter}")
        moment = datetime(2026, 1, 2, tzinfo=UTC)

        with pytest.raises(FileExistsError, match="No free filename"):
            pattern.resolve(tmp_path, ImageFormat.PNG, moment=moment)
