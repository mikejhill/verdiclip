"""Direct-interpreter and pre-native runtime attestation controls."""

import io
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from tests.capture import cursor_state_child as child
from tests.capture import cursor_state_peer as peer_module
from tests.capture import cursor_state_runtime as runtime
from tests.capture.cursor_state_evidence import CheckError, Evidence
from tests.capture.cursor_state_peer import Peer, Wire


def manifest() -> dict[str, object]:
    """Synthetic independent expectations contain no real paths or IDs."""
    return dict.fromkeys(runtime.FIELDS, "expected")


@pytest.mark.parametrize("field", runtime.FIELDS)
def test_each_runtime_identity_field_is_required(field: str) -> None:
    """Venv, interpreter and package mismatches never fall back to another runtime."""
    expected = manifest()
    with pytest.raises(CheckError) as caught:
        runtime.verify_runtime(
            {"pid": 7, "runtime": {**expected, field: "wrong"}}, expected, 7
        )
    assert caught.value.stage == "runtime-" + field.replace("_", "-")


@pytest.mark.parametrize("pid", [None, True, 8, "7"])
def test_exact_direct_process_pid_is_required(pid: object) -> None:
    """Reported identity cannot replace the process actually owned by Popen."""
    with pytest.raises(CheckError, match="runtime-pid"):
        runtime.verify_runtime({"pid": pid, "runtime": manifest()}, manifest(), 7)


def test_launch_environment_is_private_and_explicit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Keep the official venv-launcher pairing private to the child."""
    base, venv = tmp_path / "base.exe", tmp_path / "venv.exe"
    base.touch()
    venv.touch()
    expected = {**manifest(), "base_executable": runtime.canonical(str(base))}
    monkeypatch.setattr(runtime, "measure_runtime", lambda: expected)
    monkeypatch.setattr(runtime.sys, "_base_executable", str(base))
    monkeypatch.setattr(runtime.sys, "executable", str(venv))
    monkeypatch.setenv("__PYVENV_LAUNCHER__", "unverified inherited value")
    before = os.environ.copy()
    plan = runtime.build_launch_plan()
    assert plan.executable == str(base)
    assert plan.environment["__PYVENV_LAUNCHER__"] == str(venv)
    assert os.environ.copy() == before
    assert plan.expected is expected


@pytest.mark.parametrize("fault", ["missing", "relative", "venv", "image"])
def test_unverified_runtime_never_gets_a_launch_plan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fault: str
) -> None:
    """Missing base executable or failed venv/image proof is a refusal, not fallback."""
    base = tmp_path / "base.exe"
    venv = tmp_path / "venv.exe"
    base.touch()
    venv.touch()
    prefix = tmp_path / "venv"
    prefix.mkdir()
    monkeypatch.setattr(runtime.sys, "platform", "win32")
    monkeypatch.setattr(runtime.sys, "executable", str(venv))
    monkeypatch.setattr(
        runtime.sys,
        "_base_executable",
        None
        if fault == "missing"
        else "relative.exe"
        if fault == "relative"
        else str(base),
    )
    monkeypatch.setattr(
        runtime.sys, "prefix", str(tmp_path if fault == "venv" else prefix)
    )
    monkeypatch.setattr(runtime.sys, "base_prefix", str(tmp_path))
    monkeypatch.setattr(runtime, "process_image", lambda: "unverified image")
    with pytest.raises(
        CheckError, match=r"runtime-base-executable|runtime-venv|runtime-image"
    ):
        runtime.build_launch_plan()


@pytest.mark.parametrize("fault", ["pid", "venv", "deadline"])
def test_constructor_never_authorizes_run_after_rejection(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """Attestation and readiness share the original deadline and owned cleanup."""
    events: list[str] = []
    expected = manifest()
    monkeypatch.setattr(
        peer_module,
        "build_launch_plan",
        lambda: runtime.LaunchPlan("direct.exe", {}, expected),
    )
    monkeypatch.setattr(peer_module, "_INITIALIZING", [])
    monkeypatch.setattr(peer_module, "_UNCONFIRMED", [])
    fake = cast(
        subprocess.Popen[str],
        SimpleNamespace(
            pid=7,
            stdin=io.StringIO(),
            stdout=io.StringIO(),
            poll=lambda: None,
            wait=lambda **_kw: None,
            returncode=0,
        ),
    )
    monkeypatch.setattr(peer_module.subprocess, "Popen", lambda *_a, **_kw: fake)

    def wire(self: Wire, _stream: object) -> None:
        self.reader = cast(
            peer_module.threading.Thread,
            SimpleNamespace(join=lambda **_kw: None, is_alive=lambda: False),
        )

    monkeypatch.setattr(Wire, "__init__", wire)
    monkeypatch.setattr(
        Peer,
        "receive",
        lambda *_a, **_kw: {
            "pid": 8 if fault == "pid" else 7,
            "runtime": {**expected, "prefix": "wrong"} if fault == "venv" else expected,
        },
    )
    monkeypatch.setattr(Peer, "command", lambda _self, op: events.append(op))
    moments = iter([0.0, 6.0 if fault == "deadline" else 0.1])
    monkeypatch.setattr(peer_module.time, "monotonic", lambda: next(moments))
    with pytest.raises(
        CheckError, match=r"runtime-pid|runtime-prefix|runtime-startup-deadline"
    ):
        Peer("client", "arrow")
    assert events == ["close"]
    assert peer_module.cleanup_confirmed()


def test_child_does_not_construct_gui_before_run_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected parent handshake cannot create a fixture, broker or cursor owner."""
    events: list[str] = []
    monkeypatch.setattr(child, "require_hosted", lambda _env: None)
    monkeypatch.setenv("CURSOR_STATE_DISPOSABLE", "1")
    monkeypatch.setattr(child.sys, "platform", "win32")
    monkeypatch.setattr(child.sys, "argv", ["module", "client", "arrow", "a" * 32])
    monkeypatch.setattr(child, "measure_runtime", manifest)

    def reject(_op: str) -> None:
        raise CheckError("runtime-run-command")

    commands = SimpleNamespace(
        evidence=Evidence(),
        report=lambda phase, **_kw: events.append(phase),
        expect=reject,
    )
    monkeypatch.setattr(child, "Commands", lambda _nonce: commands)

    def forbidden(_args: object) -> None:
        raise AssertionError("GUI must not be constructed")

    monkeypatch.setattr(child, "QApplication", forbidden)
    with pytest.raises(SystemExit) as caught:
        child.main()
    assert caught.value.code == 1
    assert events == ["runtime", "failed"]


def test_verified_manifest_is_accepted() -> None:
    """The direct child must match all independent expectations exactly."""
    expected = manifest()
    runtime.verify_runtime({"pid": 7, "runtime": expected.copy()}, expected, 7)


@pytest.mark.parametrize("fault", ["missing", "extra", "type"])
def test_manifest_shape_is_strict(fault: str) -> None:
    """Incomplete or extended attestation cannot authorize native operations."""
    expected = manifest()
    value: object = expected.copy()
    if fault == "missing":
        value = {key: item for key, item in expected.items() if key != "prefix"}
    elif fault == "extra":
        value = {**expected, "extra": "unexpected"}
    else:
        value = None
    with pytest.raises(CheckError, match="runtime-manifest"):
        runtime.verify_runtime({"pid": 7, "runtime": value}, expected, 7)
