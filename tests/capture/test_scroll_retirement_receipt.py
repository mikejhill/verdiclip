"""Strict default and opt-in fixture retirement without starting a process."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from tests.capture.scroll_peer import ScrollPeer


@pytest.mark.parametrize("opt_in", [False, True])
@pytest.mark.parametrize(
    "fault", ["none", "nonce", "pid", "closed", "cursor", "extra", "exit"]
)
def test_close_requires_exact_mode_identity_and_successful_exit(
    tmp_path: Path,
    *,
    opt_in: bool,
    fault: str,
) -> None:
    """Neither mode accepts another owner's or incomplete retirement receipt."""
    peer = ScrollPeer(tmp_path, provider="winforms", native_pointer_cursor=opt_in)
    waits: list[int] = []
    peer.process = cast(
        subprocess.Popen[bytes],
        SimpleNamespace(
            pid=123,
            returncode=1 if fault == "exit" else 0,
            wait=lambda *, timeout: waits.append(timeout),
        ),
    )
    receipt: dict[str, object] = {"nonce": peer.nonce, "pid": 123, "closed": True}
    if opt_in:
        receipt["cursor_lifetime"] = "process-exit"
    if fault == "nonce":
        receipt["nonce"] = "other"
    elif fault == "pid":
        receipt["pid"] = 124
    elif fault == "closed":
        receipt["closed"] = 1
    elif fault == "cursor":
        receipt["cursor_lifetime"] = "not-created"
    elif fault == "extra":
        receipt["unrequested"] = True
    (tmp_path / "fixture-retired.json").write_text(json.dumps(receipt))
    if fault == "none":
        peer.close()
        assert peer._closed
    else:
        with pytest.raises(AssertionError):
            peer.close()
        assert not peer._closed
    assert waits == [5]
    assert peer._exit_confirmed
