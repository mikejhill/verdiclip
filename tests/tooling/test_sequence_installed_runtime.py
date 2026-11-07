"""Portable independent provenance checks; no helper or native socket launches."""

import hashlib
import json
import sys
import zipfile
from importlib.metadata import PathDistribution
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest
from scripts import sequence_installed_runtime as proof


@pytest.fixture
def installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Synthetic distributions replace verifier inputs, never production globals."""
    root = tmp_path / "installed"
    modules = []
    for original in proof._PARENT_MODULES:
        module = ModuleType(original.__name__)
        path = root / proof._relative(module)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"# synthetic installed bytes\n")
        module.__file__ = str(path)
        modules.append(module)
    monkeypatch.setattr(proof, "_PARENT_MODULES", tuple(modules))
    monkeypatch.setattr(proof, "verdiclip", modules[0])
    monkeypatch.setattr(proof, "mss", modules[1])
    modules[0].__dict__["VERSION"] = "1.2.3"
    modules[1].__dict__["__version__"] = "10.2.0"
    records: dict[str, list[str]] = {"PySide6-Essentials": [], "shiboken6": []}
    for name in proof._QT_NAMES:
        module = ModuleType(name)
        module.__dict__["__version__"] = "6.11.3"
        relative = (
            "shiboken6/__init__.py"
            if name == "shiboken6"
            else name.replace(".", "/") + proof.EXTENSION_SUFFIXES[0]
        )
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic dependency bytes")
        module.__file__ = str(path)
        monkeypatch.setitem(sys.modules, name, module)
        records["shiboken6" if name == "shiboken6" else "PySide6-Essentials"].append(
            relative
        )
    versions = {
        "verdiclip": "1.2.3",
        "mss": "10.2.0",
        "PySide6-Essentials": "6.11.3",
        "shiboken6": "6.11.3",
    }
    distributions = {}
    for name, version in versions.items():
        directory = root / f"{name}-{version}.dist-info"
        directory.mkdir()
        (directory / "METADATA").write_text(f"Name: {name}\nVersion: {version}\n")
        (directory / "RECORD").write_text(
            "".join(f"{item},,\n" for item in records.get(name, []))
        )
        distributions[name] = PathDistribution(directory)
    monkeypatch.setattr(proof, "distribution", distributions.__getitem__)
    wheel = tmp_path / "exact.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for path in root.rglob("*"):
            relative = path.relative_to(root).as_posix()
            if path.is_file() and relative.startswith(("verdiclip/", "verdiclip-")):
                archive.write(path, relative)
    return root, wheel


def test_independent_schema_and_separate_qt(installed: tuple[Path, Path]) -> None:
    """Wheel digest and dependency hashes describe different artifacts."""
    _, wheel = installed
    result = proof.verify_wheel(wheel)
    assert result["schema_version"] == 5
    assert len(cast("tuple[str, ...]", result["origins"])) == 21
    assert result["wheel_sha256"] == hashlib.sha256(wheel.read_bytes()).hexdigest()
    qt = cast("dict[str, dict[str, str]]", result["parent_qt"])
    assert set(qt) == set(proof._QT_NAMES)
    for name, record in qt.items():
        assert record["distribution"] == (
            "shiboken6" if name == "shiboken6" else "PySide6-Essentials"
        )
        assert record["version"] == "6.11.3"
        assert (
            record["sha256"]
            == hashlib.sha256(Path(record["origin"]).read_bytes()).hexdigest()
        )


@pytest.mark.parametrize("index", range(14, 21))
def test_each_sequence_wheel_entry_required(
    installed: tuple[Path, Path], index: int
) -> None:
    """Extra installed files cannot make an old/incomplete wheel qualify."""
    _, wheel = installed
    with zipfile.ZipFile(wheel) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    del entries[proof._relative(proof._PARENT_MODULES[index])]
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    with pytest.raises(ValueError, match="missing fixed"):
        proof.verify_wheel(wheel)


@pytest.mark.parametrize("index", range(21))
def test_every_loaded_origin_checked(installed: tuple[Path, Path], index: int) -> None:
    """An identically named and byte-identical extra copy is still rejected."""
    root, wheel = installed
    module = proof._PARENT_MODULES[index]
    copy = root.parent / "extra.py"
    copy.write_bytes(Path(module.__file__).read_bytes())
    module.__file__ = str(copy)  # Only a synthetic verifier fixture module.
    with pytest.raises(ValueError, match="Loaded module origin"):
        proof.verify_wheel(wheel)


@pytest.mark.parametrize("index", range(14, 21))
def test_sequence_byte_changes_rejected(
    installed: tuple[Path, Path], index: int
) -> None:
    """Same-size modifications fail the exact byte comparison."""
    _, wheel = installed
    path = Path(proof._PARENT_MODULES[index].__file__)
    path.write_bytes(path.read_bytes().replace(b"synthetic", b"different"))
    with pytest.raises(ValueError, match="Python bytes differ"):
        proof.verify_wheel(wheel)


@pytest.mark.parametrize(
    "name", ["verdiclip", "mss", "PySide6-Essentials", "shiboken6"]
)
def test_editable_rejected(installed: tuple[Path, Path], name: str) -> None:
    """Dependencies cannot silently resolve through editable metadata either."""
    _, wheel = installed
    dist = proof.distribution(name)
    directory = Path(str(dist.locate_file(""))) / f"{name}-{dist.version}.dist-info"
    (directory / "direct_url.json").write_text(
        json.dumps({"dir_info": {"editable": True}})
    )
    with pytest.raises(ValueError, match="Editable"):
        proof.verify_wheel(wheel)


@pytest.mark.parametrize("name", proof._QT_NAMES)
@pytest.mark.parametrize("fault", ["missing", "origin", "inventory", "version"])
def test_qt_independently_verified(
    installed: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, name: str, fault: str
) -> None:
    """Actual loaded parent dependencies must match distribution identities."""
    root, wheel = installed
    module = sys.modules[name]
    if fault == "missing":
        monkeypatch.delitem(sys.modules, name)
    elif fault == "origin":
        path = root.parent / "alien.dll"
        path.write_bytes(b"synthetic dependency bytes")
        module.__file__ = str(path)
    elif fault == "version":
        # QtNetwork has no version: its authoritative version is Essentials.
        versioned = (
            sys.modules["PySide6.QtCore"] if name == "PySide6.QtNetwork" else module
        )
        versioned.__dict__["__version__"] = "0.0.0"
    else:
        package = "shiboken6" if name == "shiboken6" else "PySide6-Essentials"
        (root / f"{package}-6.11.3.dist-info/RECORD").write_text("")
    with pytest.raises(ValueError, match=r"Qt|Loaded module origin"):
        proof.verify_wheel(wheel)


@pytest.mark.parametrize(
    "fault", ["duplicate", "version", "size", "metadata", "traversal"]
)
def test_invalid_wheel_rejected(installed: tuple[Path, Path], fault: str) -> None:
    """Ambiguous archives, wrong identity and traversal never qualify."""
    root, wheel = installed
    with zipfile.ZipFile(wheel) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    if fault == "duplicate":
        with (
            zipfile.ZipFile(wheel, "a") as archive,
            pytest.warns(UserWarning, match="Duplicate"),
        ):
            archive.writestr("verdiclip/__init__.py", entries["verdiclip/__init__.py"])
    else:
        if fault == "version":
            entries["verdiclip-1.2.3.dist-info/METADATA"] = (
                b"Name: verdiclip\nVersion: 0\n"
            )
        elif fault == "metadata":
            del entries["verdiclip-1.2.3.dist-info/METADATA"]
        elif fault == "size":
            entries["verdiclip/large.py"] = b"x" * (proof._MAX_FILE + 1)
        else:
            (root / "outside.py").write_bytes(b"# outside\n")
            entries["verdiclip/../outside.py"] = b"# outside\n"
        with zipfile.ZipFile(wheel, "w") as archive:
            for name, value in entries.items():
                archive.writestr(name, value)
    with pytest.raises(ValueError, match=r"wheel|Wheel"):
        proof.verify_wheel(wheel)


def test_canonical_distribution_path_required(installed: tuple[Path, Path]) -> None:
    """A distribution file symlink cannot claim another directory's bytes."""
    root, wheel = installed
    path = root / "verdiclip/platform/sequence_channel.py"
    other = root.parent / "redirect.py"
    other.write_bytes(path.read_bytes())
    path.unlink()
    try:
        path.symlink_to(other)
    except OSError:
        pytest.skip("Platform does not permit test symlinks")
    with pytest.raises(ValueError, match="canonical regular"):
        proof.verify_wheel(wheel)
