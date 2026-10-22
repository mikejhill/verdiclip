"""Ask PyPI for the latest release and compare release numbers."""

from __future__ import annotations

import json
import logging
from typing import Final

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from verdiclip import APP_NAME, VERSION

logger = logging.getLogger(__name__)

# Every release reaches PyPI; a git tag alone is not a release
PYPI_URL: Final = "https://pypi.org/pypi/verdiclip/json"
FETCH_TIMEOUT_MS: Final = 15_000


class ReleaseVersion:
    """Compare ``X.Y.Z`` release numbers without third-party packages."""

    @staticmethod
    def parse(text: str) -> tuple[int, ...] | None:
        """Return the numeric parts of ``text``, or None if it isn't ``X.Y.Z``.

        Pre-releases, betas, and local versions are not releases.
        """
        parts = text.strip().split(".")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):  # noqa: PLR2004 - X.Y.Z
            return None
        return tuple(int(p) for p in parts)

    @classmethod
    def is_newer(cls, candidate: str, current: str) -> bool:
        """Return whether release ``candidate`` comes after ``current``.

        An unparseable ``current`` (a local build) never asks for an update.
        """
        new, old = cls.parse(candidate), cls.parse(current)
        return new is not None and old is not None and new > old


class ReleaseFeed(QObject):
    """Fetch the latest released version from PyPI's JSON API."""

    fetched = Signal(str)  # the latest release
    failed = Signal(str)  # why there is no answer

    def __init__(self, url: str = PYPI_URL, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._url = QUrl(url)
        self._network: QNetworkAccessManager | None = None  # Made on first fetch
        self._reply: QNetworkReply | None = None

    @property
    def busy(self) -> bool:
        """Return whether a request is in flight."""
        return self._reply is not None

    def fetch(self) -> None:
        """Start a request; ``fetched`` or ``failed`` follows. Ignored while busy."""
        if self._reply is not None:
            return
        request = QNetworkRequest(self._url)
        request.setTransferTimeout(FETCH_TIMEOUT_MS)
        request.setHeader(
            QNetworkRequest.KnownHeaders.UserAgentHeader, f"{APP_NAME}/{VERSION}"
        )
        if self._network is None:
            self._network = QNetworkAccessManager(self)
        reply = self._network.get(request)
        reply.finished.connect(self._on_finished)
        self._reply = reply

    def _on_finished(self) -> None:
        """Emit the version from the finished reply."""
        reply, self._reply = self._reply, None
        if reply is None:  # pragma: no cover - finished only follows a fetch
            return
        reply.deleteLater()
        if reply.error() != QNetworkReply.NetworkError.NoError:
            self.failed.emit(reply.errorString())
            return
        payload = bytes(reply.readAll().data())
        version = self.version_of(payload)
        if version is None:
            self.failed.emit("PyPI sent a response VerdiClip doesn't understand")
            return
        self.fetched.emit(version)

    @staticmethod
    def version_of(payload: bytes) -> str | None:
        """Return ``info.version`` from a PyPI JSON response, if present."""
        try:
            data: object = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        info = data.get("info") if isinstance(data, dict) else None
        version = info.get("version") if isinstance(info, dict) else None
        return version if isinstance(version, str) and version else None
