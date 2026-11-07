"""Portable wheel-manifest/bootstrap tests, never native desktop qualification."""

import json
import os
import subprocess
import sys
import zipfile
from dataclasses import replace
from importlib.metadata import PathDistribution
from pathlib import Path
from typing import cast

import pytest
from scripts import installed_runtime_smoke as smoke

from verdiclip.platform.owned_helper import HelperCommand, HelperStatus


@pytest.fixture
def installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, smoke.InstalledRuntime]:
    """Build tiny real package/metadata files and an exact matching test wheel."""
    root = tmp_path / "installed packages å"
    files = {
        "verdiclip/__init__.py": (
            "from importlib.metadata import version\nVERSION=version('verdiclip')\n"
        ),
        "verdiclip/platform/__init__.py": "",
        "verdiclip/platform/owned_helper.py": "# Fixture.\n",
        "mss/__init__.py": "__version__='10.2.0'\n",
        "verdiclip-1.2.3.dist-info/METADATA": "Name: verdiclip\nVersion: 1.2.3\n",
        "mss-10.2.0.dist-info/METADATA": "Name: mss\nVersion: 10.2.0\n",
    }
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        # A wheel installs bytes verbatim; text-mode writes translate LF on Windows.
        path.write_bytes(content.encode("utf-8"))
    wheel = tmp_path / "verdiclip-1.2.3-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, content in files.items():
            if name.startswith(("verdiclip/", "verdiclip-")):
                archive.writestr(name, content)
    monkeypatch.setattr(
        smoke.verdiclip, "__file__", str(root / "verdiclip/__init__.py")
    )
    monkeypatch.setattr(smoke.verdiclip, "VERSION", "1.2.3")
    monkeypatch.setattr(smoke.mss, "__file__", str(root / "mss/__init__.py"))
    monkeypatch.setattr(smoke.mss, "__version__", "10.2.0")
    monkeypatch.setattr(
        smoke.owned_helper, "__file__", str(root / "verdiclip/platform/owned_helper.py")
    )

    def distribution(name: str) -> PathDistribution:
        version = "1.2.3" if name == "verdiclip" else "10.2.0"
        return PathDistribution(root / f"{name}-{version}.dist-info")

    monkeypatch.setattr(smoke.metadata, "distribution", distribution)
    runtime = smoke.inspect_runtime(wheel)
    return root, wheel, runtime


def test_manifest_uses_actual_installed_origins(
    installed: tuple[Path, Path, smoke.InstalledRuntime],
) -> None:
    """The fixture records explicit import and metadata roots, not checkout guesses."""
    root, _, runtime = installed
    assert runtime.roots == (str(root),)
    assert dict(runtime.versions) == {"verdiclip": "1.2.3", "mss": "10.2.0"}
    assert dict(runtime.origins)["owned_helper"] == str(
        root / "verdiclip/platform/owned_helper.py"
    )
    assert runtime.executable == str(
        Path(str(getattr(sys, "_base_executable", None))).resolve()
    )
    assert len(runtime.wheel_sha256) == 64
    assert runtime.payload()["pointer_bits"] in (32, 64)


def test_wheel_rejects_newline_only_installed_mutation(
    installed: tuple[Path, Path, smoke.InstalledRuntime],
) -> None:
    """Windows newline translation is a real byte mismatch, not an allowed edit."""
    root, wheel, _ = installed
    original = root / "verdiclip/__init__.py"
    with zipfile.ZipFile(wheel) as archive:
        payload = archive.read("verdiclip/__init__.py")
    assert b"\n" in payload
    assert b"\r\n" not in payload
    assert original.read_bytes() == payload
    original.write_bytes(payload.replace(b"\n", b"\r\n"))
    with pytest.raises(ValueError, match="does not match wheel"):
        smoke.inspect_runtime(wheel)
    original.write_bytes(payload)
    smoke.inspect_runtime(wheel)


def test_editable_source_and_wheel_mismatches_are_rejected(
    installed: tuple[Path, Path, smoke.InstalledRuntime],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An editable/source import or altered installed file cannot pass as a wheel."""
    root, wheel, _ = installed
    direct = root / "verdiclip-1.2.3.dist-info/direct_url.json"
    direct.write_text('{"dir_info":{"editable":true}}', encoding="utf-8")
    with pytest.raises(ValueError, match="Editable"):
        smoke.inspect_runtime(wheel)
    direct.unlink()
    original = root / "verdiclip/__init__.py"
    monkeypatch.setattr(smoke.verdiclip, "__file__", str(root / "mss/__init__.py"))
    with pytest.raises(ValueError, match="origin"):
        smoke.inspect_runtime(wheel)
    monkeypatch.setattr(smoke.verdiclip, "__file__", str(original))
    original.write_text("# altered source", encoding="utf-8")
    with pytest.raises(ValueError, match="does not match wheel"):
        smoke.inspect_runtime(wheel)


def test_wheel_version_and_missing_base_are_rejected(
    installed: tuple[Path, Path, smoke.InstalledRuntime],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Matching source bytes alone do not prove the correct wheel or interpreter."""
    root, wheel, _ = installed
    with pytest.raises(ValueError, match="Wheel version"):
        smoke.wheel_package_matches(wheel, root, "1.2.4")
    monkeypatch.delattr(smoke.sys, "_base_executable")
    with pytest.raises(ValueError, match="base Python"):
        smoke.inspect_runtime(wheel)


def test_wheel_payload_bounds(
    installed: tuple[Path, Path, smoke.InstalledRuntime],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Archive validation is bounded before reading installed package contents."""
    root, wheel, _ = installed
    monkeypatch.setattr(smoke, "_MAX_PYTHON_FILE_BYTES", 1)
    with pytest.raises(ValueError, match="payload exceeds"):
        smoke.wheel_package_matches(wheel, root, "1.2.3")


def run_import_child(
    runtime: smoke.InstalledRuntime,
    directory: Path,
    *,
    environment: dict[str, str] | None = None,
) -> dict[str, object]:
    """Execute the actual fixed bootstrap with native capture explicitly disabled."""
    receipt = directory / "receipt.json"
    subprocess.run(  # noqa: S603 - authored portable bootstrap with explicit data
        [
            runtime.executable,
            "-I",
            "-S",
            "-c",
            smoke.CHILD_BOOTSTRAP,
            json.dumps(runtime.payload()),
            str(receipt),
            "imports-only",
        ],
        cwd=directory,
        env=environment,
        check=False,
        timeout=10,
        capture_output=True,
    )
    result: object = json.loads(receipt.read_text(encoding="utf-8"))
    assert isinstance(result, dict)
    return {str(key): value for key, value in result.items()}


def test_isolated_bootstrap_imports_installed_roots_not_poison(
    installed: tuple[Path, Path, smoke.InstalledRuntime], tmp_path: Path
) -> None:
    """Actual -I -S imports ignore poisoned cwd, PYTHONPATH, user site and hooks."""
    _, _, runtime = installed
    cwd, overrides, markers = smoke.poison_paths(tmp_path / "poison")
    result = run_import_child(runtime, cwd, environment={**os.environ, **overrides})
    assert result["passed"] is True
    assert result["capture"] is False
    assert result["origins"] == dict(runtime.origins)
    assert result["versions"] == dict(runtime.versions)
    assert result["qt_imported"] is False
    assert all(not marker.exists() for marker in markers)
    with pytest.raises(ValueError, match="native capture failed"):
        smoke.admit_capture(result, runtime)


@pytest.mark.parametrize("change", ["version", "architecture", "origin", "gui"])
def test_bootstrap_rejects_wrong_runtime(
    installed: tuple[Path, Path, smoke.InstalledRuntime], tmp_path: Path, change: str
) -> None:
    """Reported identity must match before any native capture could run."""
    root, _, runtime = installed
    if change == "version":
        runtime = replace(runtime, versions=(("verdiclip", "9.9.9"), ("mss", "10.2.0")))
    elif change == "architecture":
        runtime = replace(runtime, pointer_bits=0)
    elif change == "origin":
        runtime = replace(runtime, origins=(("verdiclip", "wrong"),))
    else:
        (root / "PySide6").mkdir()
        (root / "PySide6/__init__.py").write_text("", encoding="utf-8")
        (root / "mss/__init__.py").write_text(
            "import PySide6\n__version__='10.2.0'", encoding="utf-8"
        )
    result = run_import_child(runtime, tmp_path)
    assert result["passed"] is False
    assert result["capture"] is False
    assert isinstance(result["failure"], str)


@pytest.mark.parametrize(
    "field",
    [
        "capture",
        "sample_bytes",
        "qt_imported",
        "origins",
        "versions",
        "pointer_bits",
        "python_version",
        "isolated",
        "no_site",
    ],
)
def test_native_admission_rejects_incomplete_receipts(
    installed: tuple[Path, Path, smoke.InstalledRuntime], field: str
) -> None:
    """The native gate checks proof fields rather than trusting a passed boolean."""
    _, _, runtime = installed
    result = {
        **runtime.payload(),
        "passed": True,
        "capture": True,
        "sample_bytes": 768,
        "qt_imported": False,
        "isolated": 1,
        "no_site": 1,
    }
    smoke.admit_capture(result, runtime)
    result.pop(field)
    with pytest.raises(ValueError, match=r"changed|native capture failed"):
        smoke.admit_capture(result, runtime)


class FailedStart:
    """A helper double that fails after observing the inherited poison environment."""

    def start(self, _command: HelperCommand) -> HelperStatus:
        """Return a known startup failure, preserving the parent's environment test."""
        assert os.environ["PYTHONPATH"] == "poison"
        return HelperStatus(
            pid=None,
            producer_verified=False,
            exit_code=None,
            remaining_pids=(),
            termination_requested=True,
            cleanup_confirmed=True,
            closed=True,
            failure="failed",
            cleanup_errors=(),
        )


def test_environment_is_restored_after_failed_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Poison controls never become persistent global environment changes."""
    monkeypatch.setenv("PYTHONPATH", "original")
    monkeypatch.delenv("PYTHONUSERBASE", raising=False)
    command = HelperCommand(r"C:\Python\python.exe", (), "C:\\")
    with pytest.raises(RuntimeError, match="failed to start"):
        smoke.start_with_environment(
            cast("smoke.OwnedHelper", FailedStart()),
            command,
            {"PYTHONPATH": "poison", "PYTHONUSERBASE": "temporary"},
        )
    assert os.environ["PYTHONPATH"] == "original"
    assert "PYTHONUSERBASE" not in os.environ


@pytest.mark.parametrize("fault", ["python", "executable", "roots"])
def test_bootstrap_rejects_unqualified_base_runtime(
    installed: tuple[Path, Path, smoke.InstalledRuntime], tmp_path: Path, fault: str
) -> None:
    """Version, executable and roots are checked before admitting package imports."""
    _, _, runtime = installed
    payload = runtime.payload()
    if fault == "python":
        payload["python_version"] = [0, 0, 0]
    elif fault == "executable":
        payload["executable"] = str(tmp_path / "other-python")
    else:
        payload["roots"] = [str(tmp_path / "missing-package-root")]
    receipt = tmp_path / "failure.json"
    result = subprocess.run(  # noqa: S603 - fixed authored bootstrap and data
        [
            runtime.executable,
            "-I",
            "-S",
            "-c",
            smoke.CHILD_BOOTSTRAP,
            json.dumps(payload),
            str(receipt),
            "imports-only",
        ],
        check=False,
        timeout=10,
        capture_output=True,
    )
    assert result.returncode == 1
    assert json.loads(receipt.read_text(encoding="utf-8"))["passed"] is False


def test_poison_pth_is_an_executable_positive_control(
    installed: tuple[Path, Path, smoke.InstalledRuntime], tmp_path: Path
) -> None:
    """The hook control really executes if an implementation wrongly uses addsitedir."""
    _, _, runtime = installed
    _, _, markers = smoke.poison_paths(tmp_path / "controls")
    hook = next((tmp_path / "controls/userbase").rglob("poison.pth"))
    subprocess.run(  # noqa: S603 - intentionally exercise authored disposable hook
        [
            runtime.executable,
            "-I",
            "-S",
            "-c",
            "import site,sys;site.addsitedir(sys.argv[1])",
            str(hook.parent),
        ],
        check=True,
        timeout=10,
        capture_output=True,
    )
    assert markers[-1].read_text(encoding="utf-8") == "hook executed"
    assert not markers[0].exists()
    assert not markers[1].exists()


def test_loaded_helper_and_versions_must_match_wheel_runtime(
    installed: tuple[Path, Path, smoke.InstalledRuntime],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mixed source helper or metadata/module version disagreement is unsupported."""
    root, wheel, _ = installed
    monkeypatch.setattr(
        smoke.owned_helper, "__file__", str(root / "verdiclip/__init__.py")
    )
    with pytest.raises(ValueError, match="helper origin"):
        smoke.inspect_runtime(wheel)
    monkeypatch.setattr(
        smoke.owned_helper, "__file__", str(root / "verdiclip/platform/owned_helper.py")
    )
    monkeypatch.setattr(smoke.verdiclip, "VERSION", "unknown")
    with pytest.raises(ValueError, match="versions disagree"):
        smoke.inspect_runtime(wheel)


def status(*, closed: bool) -> HelperStatus:
    """Build lifecycle evidence for controlled-clock deadline tests."""
    return HelperStatus(
        pid=101,
        producer_verified=True,
        exit_code=0 if closed else None,
        remaining_pids=() if closed else (101,),
        termination_requested=closed,
        cleanup_confirmed=closed,
        closed=closed,
        failure=None,
        cleanup_errors=(),
    )


@pytest.mark.parametrize("elapsed", [7.9, 8.0, 8.1])
def test_ready_file_after_final_sleep_must_meet_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, elapsed: float
) -> None:
    """A receipt arriving during the final sleep cannot bypass the deadline."""
    path = tmp_path / "receipt.json"
    clock = [0.0]

    class PendingHelper:
        def poll(self) -> HelperStatus:
            """Keep the process alive while its private receipt arrives."""
            return status(closed=False)

    def publish(_seconds: float) -> None:
        path.write_text('{"passed":true}', encoding="utf-8")
        clock[0] = elapsed

    monkeypatch.setattr(smoke.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(smoke.time, "sleep", publish)
    helper = cast("smoke.OwnedHelper", PendingHelper())
    if elapsed < 8:
        assert smoke.child_receipt(path, helper) == {"passed": True}
    else:
        with pytest.raises(RuntimeError, match="readiness deadline expired"):
            smoke.child_receipt(path, helper)


@pytest.mark.parametrize("elapsed", [7.9, 8.0, 8.1])
def test_late_cleanup_confirmation_cannot_pass(
    monkeypatch: pytest.MonkeyPatch, elapsed: float
) -> None:
    """Even a closed/confirmed receipt is inadmissible when observed too late."""
    clock = [0.0]

    class LateCleanup:
        def close(self) -> HelperStatus:
            """Request cleanup without asserting completion."""
            return status(closed=False)

        def poll(self) -> HelperStatus:
            """Deliver confirmation at the controlled late observation time."""
            clock[0] = elapsed
            return status(closed=True)

    monkeypatch.setattr(smoke.time, "monotonic", lambda: clock[0])
    helper = cast("smoke.OwnedHelper", LateCleanup())
    if elapsed < 8:
        assert smoke.close_with_deadline(helper).cleanup_confirmed
    else:
        with pytest.raises(RuntimeError, match="cleanup deadline expired"):
            smoke.close_with_deadline(helper)
