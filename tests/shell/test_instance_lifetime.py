"""A received pipe client must outlive immediate single-instance shutdown."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.ipc_process_source import CHILD_IMPORT_PREFIX

from verdiclip.shell import instance as instance_module

SENDER_SCRIPT = (
    CHILD_IMPORT_PREFIX
    + r"""
from PySide6.QtCore import QCoreApplication
app = QCoreApplication(sys.argv)
from verdiclip.shell import instance as module
assert Path(module.__file__).resolve() == expected_source
sys.exit(0 if module.SingleInstance(sys.argv[2]).forward(['forwarded.png']) else 3)
"""
)

SCRIPT = (
    CHILD_IMPORT_PREFIX
    + r"""
import time
import uuid

import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QProcess
from verdiclip.shell import instance as instance_module

assert Path(instance_module.__file__).resolve() == expected_source

class ObservedInstance(instance_module.SingleInstance):
    def _read(self, socket):
        self.accepted = socket
        super()._read(socket)

app = QCoreApplication([])
received = []

def receive_once():
    primary = ObservedInstance("verdiclip-lifetime-test-" + uuid.uuid4().hex)
    listening = primary.listen()
    assert listening
    primary.message_received.connect(received.append)
    sender = QProcess()
    sender.start(sys.executable, ["-c", sys.argv[2], sys.argv[1], primary.name])
    deadline = time.monotonic() + 5
    while not received and time.monotonic() < deadline:
        app.processEvents()
    assert received == [["forwarded.png"]]
    finished = sender.waitForFinished(5000)
    assert finished or sender.state() == QProcess.ProcessState.NotRunning
    assert sender.exitCode() == 0
    socket = primary.accepted
    server = primary._server
    assert shiboken6.isValid(socket) and socket.parent() is app
    primary.close()
    shiboken6.delete(primary)
    assert not shiboken6.isValid(primary)
    assert not shiboken6.isValid(server)
    assert shiboken6.isValid(socket)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not shiboken6.isValid(socket)

for _ in range(5):
    received.clear()
    receive_once()
print("delivery-and-teardown-completed")
"""
)


def test_received_client_survives_immediate_server_destruction() -> None:
    """Verify client ownership through explicit destruction and deferred deletion."""
    result = subprocess.run(  # noqa: S603 - fixed local regression script
        [
            sys.executable,
            "-X",
            "faulthandler",
            "-c",
            SCRIPT,
            str(Path(instance_module.__file__).resolve()),
            SENDER_SCRIPT,
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "delivery-and-teardown-completed" in result.stdout
