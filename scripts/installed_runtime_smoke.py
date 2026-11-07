"""Installed-wheel gates for independent capture and exact runtime primitives.

Copy this script outside the checkout, run it using the fresh wheel environment:
python installed_runtime_smoke.py --wheel <exact.whl> --output <receipts>
Only metadata is published. The 16x16 desktop sample never leaves child memory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
import sysconfig
import tempfile
import time
import zipfile
from dataclasses import asdict, dataclass
from email.parser import Parser
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING

import mss

import verdiclip
from verdiclip.platform import helper_bootstrap, helper_runtime, owned_helper
from verdiclip.platform.owned_helper import HelperCommand, HelperStatus, OwnedHelper

if TYPE_CHECKING:
    from collections.abc import Mapping

_DEADLINE = 8.0
_EXPECTED_CHANNEL_BYTES = 16 * 16 * 3
_MAX_PYTHON_FILE_BYTES = 1_048_576
_MAX_PACKAGE_BYTES = 16_777_216
_MAX_METADATA_BYTES = 65_536

# Fixed fixture code. Paths and expected identities are JSON data, never source.
# The import-only mode is used exclusively by portable bootstrap tests. The
# native parent requires capture=true and the exact sample size before passing.
CHILD_BOOTSTRAP = r"""
import ctypes
import importlib
from importlib import metadata
import json
import os
from pathlib import Path
import struct
import sys
import time

expected = json.loads(sys.argv[1])
receipt = Path(sys.argv[2])
capture = sys.argv[3] == "capture"
result = {"passed": False, "capture": False, "pid": os.getpid()}
try:
    if sys.flags.isolated != 1 or sys.flags.no_site != 1:
        raise RuntimeError("Child isolation flags are not active")
    if list(sys.version_info[:3]) != expected["python_version"]:
        raise RuntimeError(
            "Child Python version differs from the installed parent"
        )
    if struct.calcsize("P") * 8 != expected["pointer_bits"]:
        raise RuntimeError(
            "Child architecture differs from the installed parent"
        )
    if (
        Path(sys.executable).resolve()
        != Path(expected["executable"]).resolve()
    ):
        raise RuntimeError(
            "Child executable differs from the selected base Python"
        )
    roots = expected["roots"]
    if not roots or any(
        not Path(root).is_absolute() or not Path(root).is_dir()
        for root in roots
    ):
        raise RuntimeError("Invalid explicit runtime roots")
    sys.path.extend(roots)
    modules = {
        "verdiclip": importlib.import_module("verdiclip"),
        "mss": importlib.import_module("mss"),
        "owned_helper": importlib.import_module(
            "verdiclip.platform.owned_helper"
        ),
    }
    origins = {
        name: str(Path(module.__file__).resolve())
        for name, module in modules.items()
    }
    if origins != expected["origins"]:
        raise RuntimeError(
            "Child imported a different installed package origin"
        )
    versions = {
        name: metadata.version(name) for name in ("verdiclip", "mss")
    }
    if versions != expected["versions"]:
        raise RuntimeError(
            "Child distribution versions differ from the installed parent"
        )
    if modules["verdiclip"].VERSION != versions["verdiclip"]:
        raise RuntimeError(
            "Child VerdiClip version metadata was not available"
        )
    if modules["mss"].__version__ != versions["mss"]:
        raise RuntimeError("Child MSS module/version mismatch")
    if any(
        name == "PySide6" or name.startswith("PySide6.")
        for name in sys.modules
    ):
        raise RuntimeError("Helper imported GUI modules")
    sample_bytes = 0
    if capture:
        if sys.platform != "win32":
            raise RuntimeError("Native capture requires Windows")
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext.restype = ctypes.c_int
        user32.GetThreadDpiAwarenessContext.argtypes = []
        user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user32.AreDpiAwarenessContextsEqual.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        user32.AreDpiAwarenessContextsEqual.restype = ctypes.c_int
        user32.SetProcessDpiAwarenessContext(-4)
        if not user32.AreDpiAwarenessContextsEqual(
            user32.GetThreadDpiAwarenessContext(), -4
        ):
            raise RuntimeError(
                "Per-monitor-v2 awareness was not established"
            )
        with modules["mss"].MSS() as source:
            monitors = source.monitors
            if len(monitors) < 2:
                raise RuntimeError("No native display available")
            monitor = monitors[1]
            if monitor["width"] < 16 or monitor["height"] < 16:
                raise RuntimeError(
                    "Native display is too small for the sample"
                )
            shot = source.grab(
                {
                    "left": monitor["left"],
                    "top": monitor["top"],
                    "width": 16,
                    "height": 16,
                }
            )
            if shot.width != 16 or shot.height != 16:
                raise RuntimeError("Unexpected native sample dimensions")
            sample_bytes = len(shot.rgb)
            if sample_bytes != 16 * 16 * 3:
                raise RuntimeError("Unexpected native sample byte count")
        if not user32.AreDpiAwarenessContextsEqual(
            user32.GetThreadDpiAwarenessContext(), -4
        ):
            raise RuntimeError("MSS changed the established DPI context")
    if any(
        name == "PySide6" or name.startswith("PySide6.")
        for name in sys.modules
    ):
        raise RuntimeError("Native capture imported GUI modules")
    result.update(
        {
            "passed": True,
            "capture": capture,
            "sample_bytes": sample_bytes,
            "executable": sys.executable,
            "origins": origins,
            "versions": versions,
            "python_version": list(sys.version_info[:3]),
            "pointer_bits": struct.calcsize("P") * 8,
            "isolated": sys.flags.isolated,
            "no_site": sys.flags.no_site,
            "qt_imported": False,
        }
    )
except Exception as error:
    result["failure"] = str(error)
temporary = receipt.with_suffix(".tmp")
temporary.write_text(json.dumps(result), encoding="utf-8")
temporary.replace(receipt)
if result["passed"] and capture:
    time.sleep(30)
sys.exit(0 if result["passed"] else 1)
"""


@dataclass(frozen=True)
class InstalledRuntime:
    """Explicit, immutable installation evidence for the fixture child."""

    executable: str
    roots: tuple[str, ...]
    origins: tuple[tuple[str, str], ...]
    versions: tuple[tuple[str, str], ...]
    python_version: tuple[int, int, int]
    pointer_bits: int
    wheel_sha256: str

    def payload(self) -> dict[str, object]:
        """Encode paths and expected identities as data for a fixed bootstrap."""
        return {
            "executable": self.executable,
            "roots": list(self.roots),
            "origins": dict(self.origins),
            "versions": dict(self.versions),
            "python_version": list(self.python_version),
            "pointer_bits": self.pointer_bits,
        }


def wheel_package_matches(wheel: Path, root: Path, version: str) -> None:
    """Compare installed Python files to the actual wheel, without extracting it."""
    total = 0
    package = (root / "verdiclip").resolve(strict=True)
    count = 0
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        required = {
            "verdiclip/__init__.py",
            "verdiclip/platform/owned_helper.py",
            "verdiclip/platform/helper_runtime.py",
            "verdiclip/platform/helper_bootstrap.py",
        }
        if not required.issubset(names):
            raise ValueError("Wheel is missing required helper package files")
        metadata_names = [
            name
            for name in names
            if name.startswith("verdiclip-") and name.endswith(".dist-info/METADATA")
        ]
        if (
            len(metadata_names) != 1
            or archive.getinfo(metadata_names[0]).file_size > _MAX_METADATA_BYTES
        ):
            raise ValueError("Wheel distribution metadata is missing or oversized")
        package_metadata = Parser().parsestr(
            archive.read(metadata_names[0]).decode("utf-8")
        )
        if (
            package_metadata["Name"] != "verdiclip"
            or package_metadata["Version"] != version
        ):
            raise ValueError("Wheel version does not match installed VerdiClip")
        for entry in archive.infolist():
            if not entry.filename.startswith(
                "verdiclip/"
            ) or not entry.filename.endswith(".py"):
                continue
            total += entry.file_size
            if entry.file_size > _MAX_PYTHON_FILE_BYTES or total > _MAX_PACKAGE_BYTES:
                raise ValueError("Wheel Python payload exceeds the fixture bound")
            path = (root / entry.filename).resolve(strict=True)
            if not path.is_relative_to(package):
                raise ValueError("Wheel package path escapes the installed package")
            if (
                path.stat().st_size != entry.file_size
                or path.read_bytes() != archive.read(entry)
            ):
                raise ValueError(
                    f"Installed package does not match wheel: {entry.filename}"
                )
            count += 1
    if count == 0:
        raise ValueError("Wheel has no VerdiClip Python package")


def distribution_root(name: str, origin: Path) -> tuple[Path, str]:
    """Reject editable metadata or an origin outside the selected wheel package."""
    dist = metadata.distribution(name)
    direct = dist.read_text("direct_url.json")
    if direct is not None:
        data: object = json.loads(direct)
        if not isinstance(data, dict):
            raise ValueError("Malformed installed distribution metadata")
        info = data.get("dir_info")
        if isinstance(info, dict) and info.get("editable") is True:
            raise ValueError("Editable distributions are not an installed-wheel gate")
    root = Path(str(dist.locate_file(""))).resolve(strict=True)
    expected = (root / name / "__init__.py").resolve(strict=True)
    if origin != expected:
        raise ValueError(
            f"Loaded {name} origin does not match its installed distribution"
        )
    return root, dist.version


def inspect_runtime(wheel: Path) -> InstalledRuntime:
    """Use actual loaded modules and distribution roots, never checkout conventions."""
    app = Path(verdiclip.__file__).resolve(strict=True)
    dependency = Path(mss.__file__).resolve(strict=True)
    helper = Path(owned_helper.__file__).resolve(strict=True)
    app_root, app_version = distribution_root("verdiclip", app)
    dependency_root, dependency_version = distribution_root("mss", dependency)
    if helper != (app_root / "verdiclip/platform/owned_helper.py").resolve(strict=True):
        raise ValueError("Loaded helper origin is not inside the installed wheel")
    for module in (helper_runtime, helper_bootstrap):
        expected = (
            app_root
            / "verdiclip/platform"
            / (module.__name__.rsplit(".", 1)[1] + ".py")
        )
        if Path(module.__file__).resolve(strict=True) != expected.resolve(strict=True):
            raise ValueError(
                "Loaded runtime primitive origin is not inside the installed wheel"
            )
    if app_version != verdiclip.VERSION or mss.__version__ != dependency_version:
        raise ValueError("Loaded module versions disagree with installed metadata")
    executable = getattr(sys, "_base_executable", None)
    if not isinstance(executable, str) or not executable:
        raise ValueError("The explicit base Python interpreter is unavailable")
    wheel_package_matches(wheel, app_root, app_version)
    return InstalledRuntime(
        executable=str(Path(executable).resolve(strict=True)),
        roots=tuple(dict.fromkeys((str(app_root), str(dependency_root)))),
        origins=(
            ("verdiclip", str(app)),
            ("mss", str(dependency)),
            ("owned_helper", str(helper)),
        ),
        versions=(("verdiclip", app_version), ("mss", dependency_version)),
        python_version=(
            sys.version_info.major,
            sys.version_info.minor,
            sys.version_info.micro,
        ),
        pointer_bits=struct.calcsize("P") * 8,
        wheel_sha256=hashlib.sha256(wheel.read_bytes()).hexdigest(),
    )


def poison_paths(root: Path) -> tuple[Path, dict[str, str], tuple[Path, ...]]:
    """Put every shadow module and startup hook under fresh disposable test paths."""
    cwd, pythonpath, userbase = root / "cwd", root / "pythonpath", root / "userbase"
    usersite = Path(
        sysconfig.get_path(
            "purelib", scheme="nt_user", vars={"userbase": str(userbase)}
        )
    )
    for directory in (cwd, pythonpath, usersite):
        directory.mkdir(parents=True)
    markers: list[Path] = []
    for index, directory in enumerate((cwd, pythonpath, usersite)):
        marker = root / f"poison-{index}"
        markers.append(marker)
        payload = (
            f"from pathlib import Path; Path({str(marker)!r}).write_text('executed')\n"
        )
        for filename in ("mss.py", "sitecustomize.py", "usercustomize.py"):
            (directory / filename).write_text(
                payload + "raise RuntimeError('Poison imported')\n", encoding="utf-8"
            )
        package = directory / "verdiclip"
        package.mkdir()
        (package / "__init__.py").write_text(
            payload + "raise RuntimeError('Poison imported')\n", encoding="utf-8"
        )
        (directory / "poison.pth").write_text(
            "import pathlib; "
            + f"pathlib.Path({str(marker)!r}).write_text('hook executed')\n",
            encoding="utf-8",
        )
    return (
        cwd,
        {"PYTHONPATH": str(pythonpath), "PYTHONUSERBASE": str(userbase)},
        tuple(markers),
    )


def start_with_environment(
    helper: OwnedHelper, command: HelperCommand, environment: Mapping[str, str]
) -> None:
    """Temporarily poison only this standalone fixture process's child environment."""
    saved = {key: os.environ.get(key) for key in environment}
    try:
        os.environ.update(environment)
        status = helper.start(command)
        if status.failure is not None:
            raise RuntimeError(f"Installed helper failed to start: {status.failure}")
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def child_receipt(path: Path, helper: OwnedHelper) -> dict[str, object]:
    """Bound readiness and preserve a useful startup failure instead of hanging."""
    deadline = time.monotonic() + _DEADLINE
    while not path.exists() and time.monotonic() < deadline:
        if helper.poll().closed:
            raise RuntimeError("Installed helper exited without a receipt")
        time.sleep(0.01)
    if time.monotonic() >= deadline:
        raise RuntimeError("Installed helper readiness deadline expired")
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if time.monotonic() >= deadline:
        raise RuntimeError("Installed helper readiness deadline expired")
    if not isinstance(value, dict):
        raise TypeError("Malformed installed-helper receipt")
    return {str(key): item for key, item in value.items()}


def admit_capture(receipt: dict[str, object], runtime: InstalledRuntime) -> None:
    """Import-only checks or a forged identity are never native capture evidence."""
    if receipt.get("passed") is not True:
        raise ValueError(f"Installed child failed: {receipt.get('failure')}")
    expected = runtime.payload()
    for name in ("origins", "versions", "python_version", "pointer_bits"):
        if receipt.get(name) != expected[name]:
            raise ValueError(f"Installed child changed {name}")
    if (
        receipt.get("passed") is not True
        or receipt.get("capture") is not True
        or receipt.get("sample_bytes") != _EXPECTED_CHANNEL_BYTES
        or receipt.get("qt_imported") is not False
        or receipt.get("isolated") != 1
        or receipt.get("no_site") != 1
    ):
        raise ValueError(f"Installed native capture failed: {receipt.get('failure')}")


def close_with_deadline(helper: OwnedHelper) -> HelperStatus:
    """Require cleanup evidence observed strictly before the harness deadline."""
    deadline = time.monotonic() + _DEADLINE
    helper.close()
    status = helper.poll()
    while not status.closed and time.monotonic() < deadline:
        time.sleep(0.01)
        status = helper.poll()
    if time.monotonic() >= deadline:
        raise RuntimeError("Installed helper cleanup deadline expired")
    if not status.cleanup_confirmed:
        raise RuntimeError("Installed helper cleanup was not confirmed")
    return status


def run_native(runtime: InstalledRuntime) -> dict[str, object]:
    """Run one native installed-runtime sample and prove normal owned cleanup."""
    if sys.platform != "win32":
        raise RuntimeError("Installed native runtime proof requires Windows")
    helper = OwnedHelper()
    with tempfile.TemporaryDirectory(
        prefix="installed helper å-", ignore_cleanup_errors=True
    ) as temporary:
        root = Path(temporary)
        cwd, environment, markers = poison_paths(root)
        receipt_path = root / "ready.json"
        command = HelperCommand(
            runtime.executable,
            (
                "-I",
                "-S",
                "-c",
                CHILD_BOOTSTRAP,
                json.dumps(runtime.payload()),
                str(receipt_path),
                "capture",
            ),
            str(cwd),
        )
        try:
            start_with_environment(helper, command, environment)
            receipt = child_receipt(receipt_path, helper)
            admit_capture(receipt, runtime)
            pid, executable = receipt.get("pid"), receipt.get("executable")
            if type(pid) is not int or not isinstance(executable, str):
                raise ValueError("Installed child omitted its native producer identity")
            if not helper.verify_ready(pid, executable).producer_verified:
                raise RuntimeError("Installed child is not the direct owned producer")
            if any(marker.exists() for marker in markers):
                raise RuntimeError("A poisoned startup hook or module executed")
            status = close_with_deadline(helper)
            return {
                "passed": True,
                "runtime": asdict(runtime),
                "child": receipt,
                "poison_controls_passed": True,
                "cleanup": asdict(status),
            }
        finally:
            helper.release_unconfirmed("Installed-runtime fixture cleanup deadline")


def admit_primitive_manifest(
    manifest: helper_runtime.RuntimeManifest, runtime: InstalledRuntime
) -> None:
    """Cross-check production inspection against the independent wheel check."""
    origins = dict(runtime.origins)
    if (
        manifest.executable != runtime.executable
        or manifest.roots != runtime.roots
        or manifest.origins[:2] != (origins["verdiclip"], origins["mss"])
        or manifest.versions != tuple(value for _, value in runtime.versions)
        or manifest.python_version != runtime.python_version
        or manifest.pointer_bits != runtime.pointer_bits
    ):
        raise ValueError(
            "Production manifest differs from independent wheel inspection"
        )


# Fixture-only suffix runs only after the exact production bootstrap returns.
# It supplies a ready handshake so OwnedHelper does not admit a pre-ready exit.
_PRIMITIVE_READY = r"""
import os
import time
receipt = Path(sys.argv[2])
pending = receipt.with_suffix(".tmp")
pending.write_text(json.dumps({"passed": True, "pid": os.getpid(),
    "executable": sys.executable}), encoding="utf-8")
pending.replace(receipt)
time.sleep(30)
"""


def run_primitives_native(runtime: InstalledRuntime) -> dict[str, object]:
    """Exercise the exact production bootstrap with a fixture-only ready suffix."""
    if sys.platform != "win32":
        raise RuntimeError("Production runtime proof requires Windows")
    manifest = helper_runtime.inspect_runtime()
    admit_primitive_manifest(manifest, runtime)
    helper = OwnedHelper()
    with tempfile.TemporaryDirectory(
        prefix="production runtime å-", ignore_cleanup_errors=True
    ) as temporary:
        cwd, environment, markers = poison_paths(Path(temporary))
        arguments = helper_bootstrap.proof_arguments(manifest.serialize())
        receipt_path = Path(temporary) / "production-ready.json"
        command = HelperCommand(
            manifest.executable,
            (
                *arguments[:3],
                arguments[3] + _PRIMITIVE_READY,
                *arguments[4:],
                str(receipt_path),
            ),
            str(cwd),
        )
        try:
            start_with_environment(helper, command, environment)
            receipt = child_receipt(receipt_path, helper)
            pid, executable = receipt.get("pid"), receipt.get("executable")
            if (
                receipt.get("passed") is not True
                or type(pid) is not int
                or not isinstance(executable, str)
            ):
                raise ValueError("Production proof omitted its ready identity")
            if not helper.verify_ready(pid, executable).producer_verified:
                raise RuntimeError("Production proof is not the direct owned producer")
            status = close_with_deadline(helper)
            if any(marker.exists() for marker in markers):
                raise RuntimeError("Production proof executed a poisoned startup hook")
            return {
                "passed": True,
                "manifest": json.loads(manifest.serialize()),
                "bootstrap_sha256": hashlib.sha256(
                    helper_bootstrap.BOOTSTRAP_SOURCE.encode("utf-8")
                ).hexdigest(),
                "exact_bootstrap_prefix": True,
                "fixture_ready_suffix": True,
                "child": receipt,
                "poison_controls_passed": True,
                "cleanup": asdict(status),
            }
        finally:
            helper.release_unconfirmed("Production runtime proof cleanup deadline")


def main() -> int:
    """Require an actual wheel installation and write a metadata-only receipt."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = args.output
    if not isinstance(output, Path):
        raise TypeError("Output must be an explicit path")
    output.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {
        "passed": False,
        "parent_executable": sys.executable,
        "parent_prefix": sys.prefix,
        "fixture_script": str(Path(__file__).resolve()),
    }
    passed = False
    try:
        runtime = inspect_runtime(args.wheel)
        result = run_native(runtime)
        report.update(result)
        report["production_primitives"] = {"passed": False}
        primitives = run_primitives_native(runtime)
        report["production_primitives"] = primitives
        passed = result.get("passed") is True and primitives.get("passed") is True
    except Exception as error:  # noqa: BLE001 - retain native failure evidence
        report["passed"] = False
        report["failure"] = str(error)
    (output / "installed-runtime.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
