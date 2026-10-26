"""A cancelled process owner cannot be deleted recursively by deferred cleanup."""

from __future__ import annotations

import subprocess
import sys

SCRIPT = r"""
import gc
import sys
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
from verdiclip.capture.scroll.uia_process import UiaAction, UiaProcess

app = QCoreApplication([])
peer = (
    sys.executable, "-c",
    "import json,sys; "
    "print(json.dumps({'ready':True,'protocol':1}),flush=True); "
    "json.loads(sys.stdin.readline()); sys.exit(3)",
)

def discard_before_start():
    owner = UiaProcess(helper_command=peer, qualified=True)
    owner.start(UiaAction.INSPECT, {})
    owner.cancel()

def retry_with_live_owner():
    owner = UiaProcess(helper_command=peer, qualified=True)
    loop = QEventLoop()
    failures = []
    def failed(request, reason):
        failures.append(reason)
        loop.quit()
    owner.failed.connect(failed)
    owner.start(UiaAction.INSPECT, {})
    deadline = QTimer()
    deadline.setSingleShot(True)
    deadline.timeout.connect(loop.quit)
    deadline.start(5000)
    loop.exec()
    assert len(failures) == 1, failures
    assert not owner.busy

for _ in range(5):
    discard_before_start()
    gc.collect()
    retry_with_live_owner()
    gc.collect()
print("cancelled-owner-and-retry-survived")
"""


def test_cancelled_owner_survives_deferred_cleanup_and_retry() -> None:
    """Run the minimal cancel/drop/retry graph without fixtures or test ordering."""
    result = subprocess.run(  # noqa: S603 - fixed local regression script
        [sys.executable, "-X", "faulthandler", "-c", SCRIPT],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "cancelled-owner-and-retry-survived" in result.stdout
