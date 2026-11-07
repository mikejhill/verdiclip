"""Independent installed-wheel provenance, without launching a sequence helper.

Copy outside the checkout for qualification. Qt dependencies are recorded
separately: their binaries are not claimed to be in the VerdiClip wheel.
The fixed imports deliberately bootstrap the parent, including its Qt channel.
This proves installed identity, not native execution or prior application use.
"""

from __future__ import annotations

import hashlib
import json
import struct
import sys
import zipfile
from email.parser import Parser
from importlib.machinery import EXTENSION_SUFFIXES
from importlib.metadata import Distribution, distribution
from pathlib import Path
from types import ModuleType

import mss

import verdiclip
from verdiclip.capture import (
    frame_credit,
    frame_envelope,
    observe_native,
    observe_protocol,
    observe_provider,
    retained_frame,
    scroll_stitch,
    sequence_credit,
    sequence_retention,
)
from verdiclip.platform import (
    helper_bootstrap,
    helper_peer,
    helper_protocol,
    helper_runtime,
    owned_helper,
    sequence_bootstrap,
    sequence_channel,
    sequence_peer,
    sequence_protocol,
    sequence_runtime,
)

# Independent fixed schema5 order, deliberately not the production registry.
_PARENT_MODULES = (
    verdiclip,
    mss,
    helper_bootstrap,
    helper_peer,
    helper_protocol,
    scroll_stitch,
    frame_envelope,
    observe_protocol,
    observe_native,
    observe_provider,
    frame_credit,
    helper_runtime,
    owned_helper,
    retained_frame,
    sequence_protocol,
    sequence_runtime,
    sequence_bootstrap,
    sequence_peer,
    sequence_channel,
    sequence_credit,
    sequence_retention,
)
_QT_NAMES = ("PySide6.QtCore", "PySide6.QtNetwork", "shiboken6")
_MAX_FILE = 1_048_576
_MAX_PACKAGE = 16_777_216
_MAX_METADATA = 65_536


def _root(dist: Distribution) -> Path:
    direct = dist.read_text("direct_url.json")
    if direct is not None:
        value = json.loads(direct)
        if not isinstance(value, dict):
            raise ValueError("Malformed direct distribution metadata")
        info = value.get("dir_info", {})
        if not isinstance(info, dict) or info.get("editable", False):
            raise ValueError("Editable installation cannot qualify")
    return Path(str(dist.locate_file(""))).resolve(strict=True)


def _origin(module: ModuleType, root: Path, relative: str) -> Path:
    expected = root / relative
    if (
        not expected.is_file()
        or expected.resolve(strict=True) != expected
        or not expected.is_relative_to(root)
    ):
        raise ValueError("Expected a canonical regular distribution file")
    origin = getattr(module, "__file__", None)
    if not isinstance(origin, str) or Path(origin).resolve(strict=True) != expected:
        raise ValueError("Loaded module origin differs from installed distribution")
    return expected


def _relative(module: ModuleType) -> str:
    if module.__name__ in ("verdiclip", "mss"):
        return module.__name__ + "/__init__.py"
    return module.__name__.replace(".", "/") + ".py"


def _wheel(wheel: Path, root: Path, version: str) -> None:
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate wheel entries")
        required = {
            _relative(module) for module in _PARENT_MODULES if module is not mss
        }
        if not required.issubset(names):
            raise ValueError("Wheel is missing fixed runtime entries")
        manifests = [
            name
            for name in names
            if name.startswith("verdiclip-") and name.endswith(".dist-info/METADATA")
        ]
        if (
            len(manifests) != 1
            or archive.getinfo(manifests[0]).file_size > _MAX_METADATA
        ):
            raise ValueError("Missing or oversized wheel metadata")
        info = Parser().parsestr(archive.read(manifests[0]).decode("utf-8"))
        if info["Name"] != "verdiclip" or info["Version"] != version:
            raise ValueError("Wheel distribution identity mismatch")
        total = 0
        package = root / "verdiclip"
        for entry in archive.infolist():
            if not entry.filename.startswith(
                "verdiclip/"
            ) or not entry.filename.endswith(".py"):
                continue
            total += entry.file_size
            if entry.file_size > _MAX_FILE or total > _MAX_PACKAGE:
                raise ValueError("Wheel Python payload exceeds bound")
            path = root / entry.filename
            if path.resolve(strict=True) != path or not path.is_relative_to(package):
                raise ValueError("Noncanonical wheel package path")
            if (
                path.stat().st_size != entry.file_size
                or path.read_bytes() != archive.read(entry)
            ):
                raise ValueError("Installed Python bytes differ from wheel")


def _qt() -> dict[str, object]:
    modules = tuple(sys.modules.get(name) for name in _QT_NAMES)
    if any(module is None for module in modules):
        raise ValueError("Required parent Qt modules are not loaded")
    essentials, binding = distribution("PySide6-Essentials"), distribution("shiboken6")
    if (
        essentials.version != binding.version
        or getattr(modules[0], "__version__", None) != essentials.version
        or getattr(modules[2], "__version__", None) != binding.version
    ):
        raise ValueError("Loaded Qt versions differ from installed distributions")
    evidence: dict[str, object] = {}
    for module, dist in zip(modules, (essentials, essentials, binding), strict=True):
        if module is None:
            raise ValueError("Required parent Qt module is missing")
        root = _root(dist)
        relative = module.__name__.replace(".", "/")
        candidates = (
            ("shiboken6/__init__.py",)
            if module.__name__ == "shiboken6"
            else tuple(relative + suffix for suffix in EXTENSION_SUFFIXES)
        )
        matches = [str(item) for item in (dist.files or ()) if str(item) in candidates]
        if len(matches) != 1:
            raise ValueError("Qt distribution inventory missing or ambiguous")
        path = _origin(module, root, matches[0])
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        evidence[module.__name__] = {
            "distribution": "shiboken6"
            if module.__name__ == "shiboken6"
            else "PySide6-Essentials",
            "version": dist.version,
            "origin": str(path),
            "sha256": digest,
        }
    return evidence


def verify_wheel(wheel: Path) -> dict[str, object]:
    """Verify actual parent imports and exact wheel bytes, with separate Qt proof."""
    app, dependency = distribution("verdiclip"), distribution("mss")
    app_root, dependency_root = _root(app), _root(dependency)
    if app.version != verdiclip.VERSION or dependency.version != mss.__version__:
        raise ValueError("Loaded package versions differ from distribution metadata")
    origins = tuple(
        str(
            _origin(
                module,
                dependency_root if module is mss else app_root,
                _relative(module),
            )
        )
        for module in _PARENT_MODULES
    )
    _wheel(wheel, app_root, app.version)
    executable = getattr(sys, "_base_executable", None)
    if not isinstance(executable, str) or not executable:
        raise ValueError("Explicit base interpreter unavailable")
    with wheel.open("rb") as source:
        wheel_sha256 = hashlib.file_digest(source, "sha256").hexdigest()
    return {
        "schema_version": 5,
        "executable": str(Path(executable).resolve(strict=True)),
        "roots": tuple(dict.fromkeys((str(app_root), str(dependency_root)))),
        "origins": origins,
        "versions": (app.version, dependency.version),
        "python_version": tuple(sys.version_info[:3]),
        "pointer_bits": struct.calcsize("P") * 8,
        "wheel_sha256": wheel_sha256,
        "parent_qt": _qt(),
    }
