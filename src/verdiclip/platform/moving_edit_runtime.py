"""Separate schema6 installed-runtime identity for fixed sequence helpers.

This is consistency validation, not a sandbox against modified installations.
No wheel archive, site hooks, GUI imports or package installation is required.
"""

from __future__ import annotations

import json
import struct
import sys
from dataclasses import dataclass
from importlib import metadata
from importlib.machinery import EXTENSION_SUFFIXES
from pathlib import Path

_PYTHON_VERSION_PARTS = 3
_MAX_VERSION_PART = 1000
_SCHEMA = 6
_MAX_MANIFEST_BYTES = 16_384
_MAX_PATH_CHARS = 2048
_MAX_VERSION_CHARS = 128
_MODULES = (
    "verdiclip",
    "mss",
    "verdiclip.platform.helper_protocol",
    "verdiclip.platform.owned_helper",
    "verdiclip.capture.scroll_stitch",
    "verdiclip.capture.frame_envelope",
    "verdiclip.capture.observe_protocol",
    "verdiclip.capture.observe_native",
    "verdiclip.capture.moving_edit_frame",
    "verdiclip.capture.sequence_credit",
    "verdiclip.platform.moving_edit_protocol",
    "verdiclip.platform.moving_edit_runtime",
    "verdiclip.platform.moving_edit_bootstrap",
    "verdiclip.platform.moving_edit_peer",
    "verdiclip.platform.moving_edit_channel",
    "verdiclip.capture.moving_edit_retention",
    "verdiclip.capture.edit_geometry",
    "verdiclip.capture.edit_native",
    "verdiclip.capture.edit_scroll_contract",
    "verdiclip.capture.edit_scroll_native",
    "verdiclip.capture.edit_samples",
    "verdiclip.capture.edit_scroll_provider",
    "verdiclip.capture.edit_observation",
)

_PACKAGES = ("verdiclip", "mss")


def _text(value: object, limit: int) -> str:
    if type(value) is not str or not value or "\0" in value or len(value) > limit:
        raise ValueError("Invalid bounded runtime string")
    return value


def _path(value: object) -> str:
    result = _text(value, _MAX_PATH_CHARS)
    if not Path(result).is_absolute():
        raise ValueError("Runtime paths must be absolute")
    return result


def _validate_interpreter(version: tuple[int, int, int], bits: int) -> None:
    if (
        type(version) is not tuple
        or len(version) != _PYTHON_VERSION_PARTS
        or any(
            type(part) is not int or not 0 <= part < _MAX_VERSION_PART
            for part in version
        )
    ):
        raise ValueError("Invalid Python version")
    if type(bits) is not int or bits not in (32, 64):
        raise ValueError("Invalid Python architecture")


@dataclass(frozen=True, slots=True)
class MovingEditRuntimeManifest:
    """Fixed-order identities; session data and executable code are excluded."""

    executable: str
    roots: tuple[str, ...]
    origins: tuple[str, ...]
    versions: tuple[str, str]
    python_version: tuple[int, int, int]
    pointer_bits: int
    schema_version: int = _SCHEMA

    def __post_init__(self) -> None:
        """Reject mutable, ambiguous and unbounded inputs before serialization."""
        if type(self.schema_version) is not int or self.schema_version != _SCHEMA:
            raise ValueError("Unsupported runtime manifest schema")
        _path(self.executable)
        if type(self.roots) is not tuple or not 1 <= len(self.roots) <= len(_PACKAGES):
            raise ValueError("Expected one or two explicit distribution roots")
        for root in self.roots:
            _path(root)
        if len(set(self.roots)) != len(self.roots):
            raise ValueError("Duplicate runtime roots")
        if type(self.origins) is not tuple or len(self.origins) != len(_MODULES):
            raise ValueError("Invalid runtime module origins")
        for origin in self.origins:
            _path(origin)
        if type(self.versions) is not tuple or len(self.versions) != len(_PACKAGES):
            raise ValueError("Invalid runtime distribution versions")
        for version in self.versions:
            _text(version, _MAX_VERSION_CHARS)
        _validate_interpreter(self.python_version, self.pointer_bits)

    def serialize(self) -> str:
        """Encode only bounded JSON data, with fixed field names."""
        result = json.dumps(
            {
                "schema_version": self.schema_version,
                "executable": self.executable,
                "roots": self.roots,
                "origins": self.origins,
                "versions": self.versions,
                "python_version": self.python_version,
                "pointer_bits": self.pointer_bits,
            },
            ensure_ascii=True,
            separators=(",", ":"),
        )
        if len(result.encode("utf-8")) > _MAX_MANIFEST_BYTES:
            raise ValueError("Runtime manifest exceeds byte bound")
        return result


def inspect_runtime() -> MovingEditRuntimeManifest:
    """Require every fixed module to be actually loaded in the parent."""
    manifest = _inspect_runtime(child=False)
    _verify_parent_qt()
    return manifest


def _qt_origin(name: str, dist: metadata.Distribution) -> None:
    """Match one loaded fixed Qt origin to its actual distribution inventory."""
    module = sys.modules.get(name)
    if module is None:
        raise ValueError("Required Qt modules must already be loaded")
    root = Path(str(dist.locate_file(""))).resolve(strict=True)
    relative = name.replace(".", "/")
    expected_names = (
        ("shiboken6/__init__.py",)
        if name == "shiboken6"
        else tuple(relative + suffix for suffix in EXTENSION_SUFFIXES)
    )
    matches = tuple(
        str(item) for item in (dist.files or ()) if str(item) in expected_names
    )
    if len(matches) != 1:
        raise ValueError("Qt origin is missing or ambiguous in installed distribution")
    expected = root / matches[0]
    if not expected.is_file() or expected.resolve(strict=True) != expected:
        raise ValueError("Qt origin is not a regular distribution file")
    loaded = Path(_path(getattr(module, "__file__", None))).resolve(strict=True)
    if loaded != expected:
        raise ValueError("Loaded Qt origin differs from its installed distribution")


def _verify_parent_qt() -> None:
    """Validate already-loaded parent bindings without changing the child schema.

    QtCore and QtNetwork belong to PySide6-Essentials. QtNetwork does not expose
    its own version; its distribution identity and QtCore's binding version are
    checked together. Installed qualification records these parent paths, versions
    and hashes separately from the fixed 23-origin VerdiClip child proof.
    """
    names = ("PySide6.QtCore", "PySide6.QtNetwork", "shiboken6")
    if any(sys.modules.get(name) is None for name in names):
        raise ValueError("Required Qt modules must already be loaded")
    essentials = metadata.distribution("PySide6-Essentials")
    shiboken = metadata.distribution("shiboken6")
    qt_version = _text(essentials.version, _MAX_VERSION_CHARS)
    shiboken_version = _text(shiboken.version, _MAX_VERSION_CHARS)
    if (
        qt_version != shiboken_version
        or getattr(sys.modules[names[0]], "__version__", None) != qt_version
        or getattr(sys.modules[names[2]], "__version__", None) != shiboken_version
    ):
        raise ValueError("Loaded Qt versions differ from installed distributions")
    _qt_origin(names[0], essentials)
    _qt_origin(names[1], essentials)
    _qt_origin(names[2], shiboken)


def inspect_child_runtime() -> MovingEditRuntimeManifest:
    """Bind the fixed Qt channel file without importing it into the child.

    This one origin is a distribution file in the child, not a loaded module.
    Parent inspection still requires its actual loaded origin. Independent wheel
    byte verification belongs to installed qualification, not this path proof.
    """
    if not sys.flags.isolated or not sys.flags.no_site:
        raise RuntimeError("Isolated no-site interpreter required")
    if any(name == "PySide6" or name.startswith("PySide6.") for name in sys.modules):
        raise RuntimeError("GUI imported into helper runtime")
    if "verdiclip.platform.moving_edit_channel" in sys.modules:
        raise RuntimeError("Parent-only sequence channel loaded in child")
    return _inspect_runtime(child=True)


def _inspect_runtime(*, child: bool) -> MovingEditRuntimeManifest:  # noqa: C901
    """Inspect loaded origins, with only the fixed child channel-file exception."""
    if getattr(sys, "frozen", False):
        raise ValueError("Frozen helper runtimes are not supported")
    modules = tuple(sys.modules.get(name) for name in _MODULES)
    if any(
        module is None
        and not (child and name == "verdiclip.platform.moving_edit_channel")
        for name, module in zip(_MODULES, modules, strict=True)
    ):
        raise ValueError("Required runtime modules must already be loaded")
    roots: list[str] = []
    versions: list[str] = []
    origins = tuple(
        ""
        if child and name == "verdiclip.platform.moving_edit_channel"
        else str(Path(_path(getattr(module, "__file__", None))).resolve(strict=True))
        for name, module in zip(_MODULES, modules, strict=True)
    )
    for name, origin in zip(_PACKAGES, origins, strict=False):
        dist = metadata.distribution(name)
        direct = dist.read_text("direct_url.json")
        if direct is not None:
            value = json.loads(direct)
            if not isinstance(value, dict):
                raise ValueError("Malformed installed distribution metadata")
            info = value.get("dir_info", {})
            if not isinstance(info, dict) or info.get("editable", False):
                raise ValueError("Editable helper runtimes are not supported")
        root = Path(str(dist.locate_file(""))).resolve(strict=True)
        if Path(origin) != (root / name / "__init__.py").resolve(strict=True):
            raise ValueError("Loaded package origin differs from its distribution")
        roots.append(str(root))
        versions.append(dist.version)
    channel_path = Path(roots[0]) / "verdiclip/platform/moving_edit_channel.py"
    if not channel_path.is_file() or channel_path.resolve(strict=True) != channel_path:
        raise ValueError("Fixed sequence channel must be a regular distribution file")
    if child:
        origins = tuple(
            str(channel_path)
            if name == "verdiclip.platform.moving_edit_channel"
            else origin
            for name, origin in zip(_MODULES, origins, strict=True)
        )
    for origin, module in zip(origins[2:], _MODULES[2:], strict=True):
        relative = module.replace(".", "/") + ".py"
        if Path(origin) != (Path(roots[0]) / relative).resolve(strict=True):
            raise ValueError("Fixed helper origin differs from installed distribution")
    if (
        versions[0] != getattr(modules[0], "VERSION", None)
        or getattr(modules[1], "__version__", None) != versions[1]
    ):
        raise ValueError("Loaded package versions differ from their distributions")
    executable = _path(getattr(sys, "_base_executable", None))
    manifest = MovingEditRuntimeManifest(
        str(Path(executable).resolve(strict=True)),
        tuple(dict.fromkeys(roots)),
        origins,
        (versions[0], versions[1]),
        (sys.version_info.major, sys.version_info.minor, sys.version_info.micro),
        struct.calcsize("P") * 8,
    )
    manifest.serialize()
    return manifest


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate runtime manifest field")
        result[key] = value
    return result


def deserialize_manifest(payload: str) -> MovingEditRuntimeManifest:
    """Validate a bounded schema, rejecting unknown or duplicate JSON fields."""
    if type(payload) is not str or len(payload) > _MAX_MANIFEST_BYTES:
        raise ValueError("Runtime manifest exceeds byte bound")
    if len(payload.encode("utf-8")) > _MAX_MANIFEST_BYTES:
        raise ValueError("Runtime manifest exceeds byte bound")
    value = json.loads(payload, object_pairs_hook=_unique_object)
    fields = {
        "schema_version",
        "executable",
        "roots",
        "origins",
        "versions",
        "python_version",
        "pointer_bits",
    }
    if not isinstance(value, dict) or value.keys() != fields:
        raise ValueError("Invalid runtime manifest fields")
    for key in ("roots", "origins", "versions", "python_version"):
        if type(value[key]) is not list:
            raise ValueError("Invalid runtime manifest array")
    return MovingEditRuntimeManifest(
        value["executable"],
        tuple(value["roots"]),
        tuple(value["origins"]),
        tuple(value["versions"]),
        tuple(value["python_version"]),
        value["pointer_bits"],
        value["schema_version"],
    )
