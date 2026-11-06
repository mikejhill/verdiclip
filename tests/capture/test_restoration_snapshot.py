"""Correlated fixture observations never retry until restoration looks successful."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from tests.capture.scroll_peer import ScrollPeer
from tests.capture.test_scroll_metric_policy import run_policy_probe


@pytest.fixture
def peer(tmp_path: Path) -> ScrollPeer:
    """A live admitted fake owner, without any child process or UI."""
    instance = ScrollPeer(tmp_path)
    instance.identity = (123, 456, 789)
    instance.process = cast(
        subprocess.Popen[bytes], SimpleNamespace(pid=123, poll=lambda: None)
    )
    instance.state = {"sequence": 1, "offset": 840, "end_seen": True}
    return instance


def ack(peer: ScrollPeer, **changes: object) -> Path:
    """Publish a synthetic independently correlated observation."""
    value = {
        "nonce": peer.nonce,
        "request_id": peer.snapshot_request,
        "pid": 123,
        "started_ticks": 456,
        "hwnd": 789,
        "offset": 0,
        "end_seen": True,
    }
    value.update(changes)
    path = peer.directory / "fixture-snapshot-ack.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_request_does_not_reuse_periodic_state(peer: ScrollPeer) -> None:
    """Require one new request and matching ack instead of old periodic state."""
    peer.request_snapshot()
    request = json.loads((peer.directory / "fixture-snapshot-request.json").read_text())
    assert request == {
        "nonce": peer.nonce,
        "operation": "snapshot",
        "request_id": peer.snapshot_request,
    }
    assert not peer.poll_snapshot()
    assert peer.state["offset"] == 840
    ack(peer)
    assert peer.poll_snapshot()
    peer.assert_snapshot_restored()
    assert peer.state["offset"] == 840
    with pytest.raises(RuntimeError, match="one live"):
        peer.request_snapshot()


def test_first_fresh_nonzero_ack_is_permanent_failure(peer: ScrollPeer) -> None:
    """A later zero can never replace the first nonzero response."""
    peer.request_snapshot()
    ack(peer, offset=840)
    assert peer.poll_snapshot()
    with pytest.raises(AssertionError, match="Fresh fixture offset"):
        peer.assert_snapshot_restored()
    ack(peer, offset=0)
    assert peer.poll_snapshot()
    with pytest.raises(AssertionError, match="Fresh fixture offset"):
        peer.assert_snapshot_restored()


@pytest.mark.parametrize(
    "changes",
    [
        {"nonce": "0" * 32},
        {"request_id": "0" * 32},
        {"pid": 124},
        {"started_ticks": 457},
        {"hwnd": 790},
        {"pid": True},
        {"offset": True},
        {"offset": float("nan")},
        {"offset": -1},
        {"end_seen": 1},
        {"extra": "ignored"},
    ],
)
def test_wrong_ack_is_rejected_immediately(
    peer: ScrollPeer, changes: dict[str, object]
) -> None:
    """Ownership/schema errors raise rather than waiting for a different response."""
    peer.request_snapshot()
    ack(peer, **changes)
    with pytest.raises(ValueError, match=r"Snapshot|schema"):
        peer.poll_snapshot()
    assert peer.snapshot_ack is None


def test_preexisting_ack_and_duplicate_fields_are_rejected(peer: ScrollPeer) -> None:
    """Old files and JSON field shadowing cannot impersonate a new observation."""
    path = ack(peer)
    with pytest.raises(ValueError, match="Preexisting"):
        peer.request_snapshot()
    path.unlink()
    peer.request_snapshot()
    path.write_text('{"offset":840,"offset":0}', encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        peer.poll_snapshot()


def test_exit_before_ack_is_failure(peer: ScrollPeer) -> None:
    """No exited owner can attest a current observation."""
    peer.request_snapshot()
    ack(peer)
    peer.process = cast(
        subprocess.Popen[bytes], SimpleNamespace(pid=123, poll=lambda: 0)
    )
    with pytest.raises(RuntimeError, match="owner unavailable"):
        peer.poll_snapshot()


@pytest.mark.skipif(sys.platform != "win32", reason="Installed Windows PowerShell")
def test_real_read_only_fixture_protocol(tmp_path: Path) -> None:
    """Execute the real shared protocol without constructing WPF or WinForms UI."""
    helper = Path(__file__).with_name("fixture_snapshot.ps1")
    probe = tmp_path / "snapshot-probe.ps1"
    probe.write_text(
        r"""
param([string]$Helper,[string]$Directory)
$ErrorActionPreference='Stop'
. $Helper
$nonce='0123456789abcdef0123456789abcdef'
$request='{"nonce":"'+$nonce+'","operation":"snapshot","request_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}'
$requestPath=Join-Path $Directory 'fixture-snapshot-request.json'
$ackPath=Join-Path $Directory 'fixture-snapshot-ack.json'
$commandPath=Join-Path $Directory 'fixture-command.json'
[IO.File]::WriteAllText($commandPath,'unchanged')
$script:observations=0
$observe={ $script:observations++; @{handle=[long]789;offset=840;end_seen=$true} }
Write-RequestedFixtureSnapshot $Directory $nonce 456 $observe
if($observations -ne 0) { throw 'Offset read before request exists' }
[IO.File]::WriteAllText($requestPath,$request)
Write-RequestedFixtureSnapshot $Directory $nonce 456 $observe
if($observations -ne 1) { throw 'First observation not requested exactly once' }
$first=[IO.File]::ReadAllText($ackPath)
$ack=$first | ConvertFrom-Json
if($ack.offset -ne 840 -or $ack.pid -ne $PID -or $ack.started_ticks -ne 456 -or
  $ack.hwnd -ne 789 -or $ack.nonce -cne $nonce -or
  $ack.request_id -cne 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa') { throw 'Invalid first ack' }
Write-RequestedFixtureSnapshot $Directory $nonce 456 $observe
if([IO.File]::ReadAllText($ackPath) -cne $first -or $observations -ne 1) {
  throw 'First ack reread or replaced'
}
if([IO.File]::ReadAllText($commandPath) -cne 'unchanged') {
  throw 'Mutation command changed'
}
[IO.File]::Delete($ackPath)
foreach($bad in @($request.Replace($nonce,('b'*32)),
  $request.Replace('snapshot','scroll-percent'),$request.Replace('}',',"extra":0}'))) {
  [IO.File]::WriteAllText($requestPath,$bad)
  $refused=$false
  try { Write-RequestedFixtureSnapshot $Directory $nonce 456 $observe }
  catch { $refused=$true }
  if(-not $refused -or [IO.File]::Exists($ackPath) -or $observations -ne 1) {
    throw 'Bad request accepted or offset read before admission'
  }
}
[Console]::Out.WriteLine('SNAPSHOT_PROTOCOL_OK')
""",
        encoding="utf-8",
    )
    host = (
        Path(os.environ["SYSTEMROOT"])
        / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    result = run_policy_probe(
        [
            str(host),
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(probe),
            "-Helper",
            str(helper),
            "-Directory",
            str(tmp_path),
        ]
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SNAPSHOT_PROTOCOL_OK" in result.stdout


@pytest.mark.parametrize("failure", [KeyboardInterrupt(), ValueError("invalid ack")])
def test_snapshot_failure_preserves_control_and_owned_cleanup(
    peer: ScrollPeer, monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    """Snapshot observation cannot weaken the existing finally-driven retirement."""
    peer.request_snapshot()
    ack(peer)
    peer.process = cast(
        subprocess.Popen[bytes],
        SimpleNamespace(
            pid=123, poll=lambda: None, wait=lambda **_: None, returncode=0
        ),
    )
    (peer.directory / "fixture-retired.json").write_text(
        json.dumps({"nonce": peer.nonce, "pid": 123, "closed": True}), encoding="utf-8"
    )
    original = Path.read_text

    def read(path: Path, *_args: object, **_kwargs: object) -> str:
        if path.name == "fixture-snapshot-ack.json":
            raise failure
        return original(path, encoding="utf-8")

    monkeypatch.setattr(Path, "read_text", read)

    def observe_and_retire() -> None:
        try:
            peer.poll_snapshot()
        finally:
            peer.close()

    with pytest.raises(type(failure)) as caught:
        observe_and_retire()
    assert caught.value is failure
    assert peer._closed
    assert (peer.directory / "fixture-stop.json").exists()


@pytest.mark.parametrize(
    "fixture", ["provider_fixture.ps1", "provider_winforms_fixture.ps1"]
)
def test_snapshot_getters_are_deferred_until_request_admission(fixture: str) -> None:
    """No caller eagerly reads offset while constructing the snapshot command."""
    source = Path(__file__).with_name(fixture).read_text()
    helper = Path(__file__).with_name("fixture_snapshot.ps1").read_text()
    invocation = source.split("Write-RequestedFixtureSnapshot", 1)[1].split(
        "Write-FixtureState", 1
    )[0]
    assert "-Observe {" in invocation
    assert "-Offset" not in invocation
    assert helper.index("$match.Groups[1].Value -cne $Nonce") < helper.index(
        "$observation=& $Observe"
    )
