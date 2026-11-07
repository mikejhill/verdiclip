"""Installed-runtime identity for isolated helpers, independent of installer kind.

This is consistency validation, not a sandbox against modified installations.
No wheel archive, site hooks, GUI imports or package installation is required.
"""

from __future__ import annotations

import json
import struct
import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

_PYTHON_VERSION_PARTS = 3
_MAX_VERSION_PART = 1000
_SCHEMA = 3
_MAX_MANIFEST_BYTES = 16_384
_MAX_PATH_CHARS = 2048
_MAX_VERSION_CHARS = 128
_MODULES = (
    "verdiclip",
    "mss",
    "verdiclip.platform.helper_bootstrap",
    "verdiclip.platform.helper_peer",
    "verdiclip.platform.helper_protocol",
    "verdiclip.capture.scroll_stitch",
    "verdiclip.capture.frame_envelope",
    "verdiclip.capture.observe_protocol",
    "verdiclip.capture.observe_native",
    "verdiclip.capture.observe_provider",
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
class RuntimeManifest:
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


def inspect_runtime() -> RuntimeManifest:  # noqa: C901 - fixed runtime identity checks
    """Describe normal installed pip, uv tool or uvx roots without guessing paths."""
    if getattr(sys, "frozen", False):
        raise ValueError("Frozen helper runtimes are not supported")
    modules = tuple(sys.modules.get(name) for name in _MODULES)
    if any(module is None for module in modules):
        raise ValueError("Required runtime modules must already be loaded")
    roots: list[str] = []
    versions: list[str] = []
    origins = tuple(
        str(Path(_path(getattr(module, "__file__", None))).resolve(strict=True))
        for module in modules
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
    manifest = RuntimeManifest(
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


def deserialize_manifest(payload: str) -> RuntimeManifest:
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
    return RuntimeManifest(
        value["executable"],
        tuple(value["roots"]),
        tuple(value["origins"]),
        tuple(value["versions"]),
        tuple(value["python_version"]),
        value["pointer_bits"],
        value["schema_version"],
    )
