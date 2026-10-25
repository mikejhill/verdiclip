"""Tests for the release feed and version comparison."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtNetwork import QHostAddress, QTcpServer
from pytestqt.qtbot import QtBot

import verdiclip.update.feed as feed_module
from verdiclip.update.feed import ReleaseFeed, ReleaseVersion


def pypi_reply(tmp_path: Path, payload: object) -> str:
    """Write a PyPI-shaped JSON reply and return its file URL."""
    path = tmp_path / "pypi.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return QUrl.fromLocalFile(str(path)).toString()


class TestReleaseVersion:
    """Release numbers compare numerically; anything else never updates."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [("0.10.0", (0, 10, 0)), (" 1.2.3\n", (1, 2, 3)), ("10.0.12", (10, 0, 12))],
    )
    def test_parses_releases(self, text: str, expected: tuple[int, ...]) -> None:
        """X.Y.Z parses into its numbers."""
        assert ReleaseVersion.parse(text) == expected

    @pytest.mark.parametrize(
        "text", ["0.10", "1.2.3.4", "0.11.1-beta", "1.0.0rc1", "", "a.b.c", "1..2"]
    )
    def test_rejects_non_releases(self, text: str) -> None:
        """Pre-releases, betas, and malformed numbers are not releases."""
        assert ReleaseVersion.parse(text) is None

    @pytest.mark.parametrize(
        ("candidate", "current", "newer"),
        [
            ("0.10.0", "0.9.1", True),  # Numeric, not text, order
            ("0.10.1", "0.10.0", True),
            ("1.0.0", "0.99.99", True),
            ("0.10.0", "0.10.0", False),
            ("0.9.9", "0.10.0", False),
            ("0.11.0", "0.0.0+unknown", False),  # A local build never updates
            ("0.11.0-beta", "0.10.0", False),
        ],
    )
    def test_is_newer(self, candidate: str, current: str, *, newer: bool) -> None:
        """Only a later release counts as newer."""
        assert ReleaseVersion.is_newer(candidate, current) is newer


class TestVersionOf:
    """Reading ``info.version`` out of PyPI's JSON."""

    def test_reads_version(self) -> None:
        """The version comes from info.version."""
        payload = json.dumps({"info": {"version": "0.11.0"}}).encode()
        assert ReleaseFeed.version_of(payload) == "0.11.0"

    @pytest.mark.parametrize(
        "payload",
        [
            b"not json",
            b"\xff\xfe",
            b"[]",
            b'{"info": []}',
            b'{"info": {}}',
            b'{"info": {"version": 11}}',
            b'{"info": {"version": ""}}',
            b'{"info": {"version": "not-a-release"}}',
        ],
    )
    def test_malformed_replies_have_no_version(self, payload: bytes) -> None:
        """Anything without a version string is no answer."""
        assert ReleaseFeed.version_of(payload) is None


class TestReleaseFeed:
    """Fetching over Qt's network stack (file URLs stand in for PyPI)."""

    def test_stalled_check_times_out_then_can_retry(
        self,
        qtbot: QtBot,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A silent HTTP server must not strand checking state or block retry."""
        server = QTcpServer()
        assert server.listen(QHostAddress.SpecialAddress.LocalHost)
        monkeypatch.setattr(feed_module, "FETCH_TIMEOUT_MS", 30)
        feed = ReleaseFeed(f"http://127.0.0.1:{server.serverPort()}/release")
        with qtbot.waitSignal(feed.failed) as failed:
            feed.fetch()
        assert "timed out" in failed.args[0], (
            "A stalled check needs an actionable error"
        )
        assert not feed.busy, "Timeout must release the request for retry"
        # Use a generous deadline on the retry and serve a complete HTTP reply.
        monkeypatch.setattr(feed_module, "FETCH_TIMEOUT_MS", 2000)
        while server.hasPendingConnections():
            server.nextPendingConnection().close()

        def answer() -> None:
            """Serve the next request from the same release endpoint."""
            socket = server.nextPendingConnection()

            def respond() -> None:
                """Answer after the HTTP request arrives."""
                socket.readAll()
                body = b'{"info":{"version":"0.11.0"}}'
                header = f"HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\n\r\n"
                socket.write(header.encode() + body)
                socket.disconnectFromHost()

            socket.readyRead.connect(respond)

        server.newConnection.connect(answer)
        with qtbot.waitSignal(feed.fetched) as fetched:
            feed.fetch()
        assert fetched.args == ["0.11.0"], (
            "Retry after timeout must deliver the release"
        )
        assert not feed.busy
        server.close()

    def test_fetch_emits_latest(self, qtbot: QtBot, tmp_path: Path) -> None:
        """A good reply emits the latest version, and the feed is idle again."""
        feed = ReleaseFeed(pypi_reply(tmp_path, {"info": {"version": "0.11.0"}}))

        with qtbot.waitSignal(feed.fetched) as signal:
            feed.fetch()
            assert feed.busy

        assert signal.args == ["0.11.0"]
        assert not feed.busy

    def test_unreadable_reply_fails(self, qtbot: QtBot, tmp_path: Path) -> None:
        """A reply without a version reports a failure."""
        feed = ReleaseFeed(pypi_reply(tmp_path, {"releases": {}}))

        with qtbot.waitSignal(feed.failed) as signal:
            feed.fetch()

        assert "understand" in signal.args[0]

    def test_network_error_fails(self, qtbot: QtBot, tmp_path: Path) -> None:
        """A request that errors reports Qt's reason."""
        missing = QUrl.fromLocalFile(str(tmp_path / "missing.json")).toString()
        feed = ReleaseFeed(missing)

        with qtbot.waitSignal(feed.failed) as signal:
            feed.fetch()

        assert signal.args[0]

    def test_fetch_while_busy_is_ignored(self, qtbot: QtBot, tmp_path: Path) -> None:
        """A second fetch during a request doesn't start another."""
        feed = ReleaseFeed(pypi_reply(tmp_path, {"info": {"version": "0.11.0"}}))
        answers: list[str] = []
        feed.fetched.connect(answers.append)

        with qtbot.waitSignal(feed.fetched):
            feed.fetch()
            feed.fetch()
        qtbot.wait(50)

        assert answers == ["0.11.0"]
