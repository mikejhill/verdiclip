"""Direct Windows venv launch with independently measured private attestation."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import ntpath
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import PySide6
from PySide6 import QtCore
from tests.capture.cursor_state_evidence import CheckError

FIELDS = (
    "executable",
    "base_executable",
    "prefix",
    "base_prefix",
    "image",
    "python_version",
    "implementation",
    "pointer_bits",
    "pyside_version",
    "qt_version",
    "pyside_module",
    "qt_module",
)


def canonical(value: str) -> str:
    """Normalize an existing trusted runtime path without publishing it."""
    return ntpath.normcase(str(Path(value).resolve(strict=True)))


def process_image() -> str:
    """Read the actual executable image, not the venv-facing sys.executable."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetModuleFileNameW.argtypes = [w.HMODULE, w.LPWSTR, w.DWORD]
    kernel.GetModuleFileNameW.restype = w.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    length = kernel.GetModuleFileNameW(None, buffer, len(buffer))
    if not 0 < length < len(buffer):
        raise CheckError("runtime-image")
    return canonical(buffer.value)


def measure_runtime() -> dict[str, object]:
    """Both processes measure themselves; the child cannot define expectations."""
    if sys.platform != "win32" or sys.implementation.name != "cpython":
        raise CheckError("runtime-platform")
    base = getattr(sys, "_base_executable", None)
    if not isinstance(base, str) or not base or not sys.executable:
        raise CheckError("runtime-base-executable")
    for value in (base, sys.executable):
        if (
            not Path(value).is_absolute()
            or not Path(value).is_file()
            or Path(value).suffix.lower() != ".exe"
        ):
            raise CheckError("runtime-base-executable")
    if canonical(sys.prefix) == canonical(sys.base_prefix):
        raise CheckError("runtime-venv")
    image = process_image()
    if image != canonical(base):
        raise CheckError("runtime-image")
    return {
        "executable": canonical(sys.executable),
        "base_executable": canonical(base),
        "prefix": canonical(sys.prefix),
        "base_prefix": canonical(sys.base_prefix),
        "image": image,
        "python_version": list(sys.version_info[:3]),
        "implementation": sys.implementation.name,
        "pointer_bits": ctypes.sizeof(ctypes.c_void_p) * 8,
        "pyside_version": PySide6.__version__,
        "qt_version": QtCore.qVersion(),
        "pyside_module": canonical(PySide6.__file__),
        "qt_module": canonical(QtCore.__file__),
    }


@dataclass(frozen=True)
class LaunchPlan:
    """One child-specific environment and the parent's independent expectations."""

    executable: str
    environment: dict[str, str]
    expected: dict[str, object]


def build_launch_plan() -> LaunchPlan:
    """Use CPython's Windows multiprocessing venv strategy, never a fallback."""
    expected = measure_runtime()
    base = getattr(sys, "_base_executable", None)
    if not isinstance(base, str) or canonical(base) != expected["base_executable"]:
        raise CheckError("runtime-base-executable")
    environment = os.environ.copy()
    environment["__PYVENV_LAUNCHER__"] = sys.executable
    return LaunchPlan(base, environment, expected)


def verify_runtime(
    receipt: dict[str, object], expected: dict[str, object], pid: int
) -> None:
    """Require the directly owned process and every independently measured field."""
    child_pid = receipt.get("pid")
    if type(child_pid) is not int or child_pid != pid:
        raise CheckError("runtime-pid")
    manifest = receipt.get("runtime")
    if not isinstance(manifest, dict):
        raise CheckError("runtime-manifest")
    if set(manifest) != set(FIELDS) or set(expected) != set(FIELDS):
        raise CheckError("runtime-manifest")
    for field in FIELDS:
        if (
            type(manifest[field]) is not type(expected[field])
            or manifest[field] != expected[field]
        ):
            raise CheckError("runtime-" + field.replace("_", "-"))
