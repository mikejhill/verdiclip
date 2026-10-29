"""A received pipe client must outlive immediate single-instance shutdown."""

from __future__ import annotations

import subprocess
import sys

SCRIPT = r"""import gc
import sys
import time
import uuid

from PySide6.QtCore import QCoreApplication, QProcess
from verdiclip.shell.instance import SingleInstance

app = QCoreApplication([])
received = []

def receive_once():
    primary = SingleInstance("verdiclip-lifetime-test-" + uuid.uuid4().hex)
    listening = primary.listen()
    assert listening
    primary.message_received.connect(received.append)
    sender = QProcess()
    script = (
        "import sys; from PySide6.QtCore import QCoreApplication; "
        "app = QCoreApplication(sys.argv); "
        "from verdiclip.shell.instance import SingleInstance; "
        "sys.exit(0 if SingleInstance(sys.argv[1]).forward(['forwarded.png']) else 3)"
    )
    sender.start(sys.executable, ["-c", script, primary.name])
    deadline = time.monotonic() + 5
    while not received and time.monotonic() < deadline:
        app.processEvents()
    assert received == [["forwarded.png"]]
    finished = sender.waitForFinished(5000)
    assert finished or sender.state() == QProcess.ProcessState.NotRunning
    assert sender.exitCode() == 0
    primary.close()

for _ in range(5):
    received.clear()
    receive_once()
    # Both owners are gone before deferred client deletion is drained.
    gc.collect()
    for _ in range(20):
        app.processEvents()
print("delivery-and-teardown-completed")
"""


def test_received_client_survives_immediate_server_destruction() -> None:
    """Reproduce the Windows access violation in a child with no editor/desktop."""
    result = subprocess.run(  # noqa: S603 - fixed local regression script
        [sys.executable, "-X", "faulthandler", "-c", SCRIPT],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "delivery-and-teardown-completed" in result.stdout
