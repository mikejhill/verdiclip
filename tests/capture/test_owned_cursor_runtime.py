"""Direct-interpreter and pre-native runtime attestation controls."""

import os
from pathlib import Path

import pytest
from tests.capture import owned_cursor_runtime as runtime
from tests.capture.owned_cursor_runtime import CheckError


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
