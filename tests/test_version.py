"""Tests for the reported application version."""

from __future__ import annotations

from importlib.metadata import Distribution, PathDistribution
from pathlib import Path

import pytest

from verdiclip import _Version


def make_dist(tmp_path: Path, version: str, direct_url: str | None) -> Distribution:
    """Write a dist-info folder for ``version`` and return its distribution."""
    info = tmp_path / f"verdiclip-{version}.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.4\nName: verdiclip\nVersion: {version}\n",
        encoding="utf-8",
    )
    if direct_url is not None:
        (info / "direct_url.json").write_text(direct_url, encoding="utf-8")
    return PathDistribution(info)


class TestVersion:
    """Tests for _Version."""

    def test_release_install_reports_release(self, tmp_path: Path) -> None:
        """A PyPI install reports its release unchanged."""
        dist = make_dist(tmp_path, "0.4.2", None)
        assert _Version.of(dist) == "0.4.2"

    def test_non_editable_local_install_reports_release(self, tmp_path: Path) -> None:
        """A built-from-source, non-editable install reports its release."""
        url = '{"url": "file:///src", "dir_info": {}}'
        assert _Version.of(make_dist(tmp_path, "0.4.2", url)) == "0.4.2"

    def test_editable_checkout_reports_next_beta(self, tmp_path: Path) -> None:
        """Running from a checkout reports the next patch as a beta."""
        url = '{"url": "file:///src", "dir_info": {"editable": true}}'
        assert _Version.of(make_dist(tmp_path, "0.4.2", url)) == "0.4.3-beta"

    @pytest.mark.parametrize("text", ["not json", "[]", '{"dir_info": []}'])
    def test_malformed_direct_url_reports_release(
        self, tmp_path: Path, text: str
    ) -> None:
        """Unreadable install records fall back to the release."""
        assert _Version.of(make_dist(tmp_path, "0.4.2", text)) == "0.4.2"

    @pytest.mark.parametrize(
        ("release", "expected"),
        [("1.9.9", "1.9.10-beta"), ("0.5.0", "0.5.1-beta"), ("1.0", "1.0-beta")],
    )
    def test_next_beta(self, release: str, expected: str) -> None:
        """The patch number bumps; odd versions just gain the suffix."""
        assert _Version.next_beta(release) == expected
