"""Forwarded launch arguments survive in-process decoding without desktop IPC."""

from __future__ import annotations

import json
from typing import override

from PySide6.QtCore import QByteArray
from PySide6.QtNetwork import QLocalSocket
from PySide6.QtWidgets import QApplication

from verdiclip.shell.instance import SingleInstance


class BufferedClient(QLocalSocket):
    """Supply bytes without opening a native socket or registering a server."""

    def __init__(self, payload: bytes) -> None:
        super().__init__()
        self._payload = QByteArray(payload)

    @override
    def readAll(self) -> QByteArray:
        """Consume the same byte-array interface as a disconnected client."""
        data, self._payload = self._payload, QByteArray()
        return data


def test_forwarded_arguments_keep_content_order_duplicates_and_empty_values(
    qapp: QApplication,
) -> None:
    """Preserve opaque launch arguments through decoding and the public signal."""
    assert QApplication.instance() is qapp
    arguments = [
        r"C:\Captures\two words.png",
        "日本語 🖼.png",
        "",
        '--literal="quoted"',
        "duplicate.png",
        "duplicate.png",
    ]
    instance = SingleInstance("payload-unit-only")
    received: list[list[str]] = []
    instance.message_received.connect(received.append)
    client = BufferedClient(json.dumps(arguments, ensure_ascii=False).encode("utf-8"))
    instance._read(client)
    assert received == [arguments]


def test_second_launch_without_files_preserves_empty_argument_list(
    qapp: QApplication,
) -> None:
    """A fileless second launch is one empty request, not no request."""
    assert QApplication.instance() is qapp
    instance = SingleInstance("payload-unit-only")
    received: list[list[str]] = []
    instance.message_received.connect(received.append)
    client = BufferedClient(b"[]")
    instance._read(client)
    assert received == [[]]
