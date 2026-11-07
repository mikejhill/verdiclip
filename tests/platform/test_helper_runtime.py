"""Installed runtime contracts; synthetic roots are not native qualification."""

import json
import os
import subprocess
import sys
from importlib.metadata import PathDistribution
from pathlib import Path
from types import SimpleNamespace

import pytest

from verdiclip.platform import helper_bootstrap as bootstrap
from verdiclip.platform import helper_runtime as runtime


@pytest.fixture
def installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> runtime.RuntimeManifest:
    """Build a byte-exact installed package including the actual bootstrap code."""
    root = tmp_path / "runtime café"
    files = {
        "verdiclip/__init__.py": "VERSION='1.2.3'\n",
        "verdiclip/platform/__init__.py": "",
        "mss/__init__.py": "__version__='10.2.0'\n",
        "verdiclip-1.2.3.dist-info/METADATA": "Name: verdiclip\nVersion: 1.2.3\n",
        "mss-10.2.0.dist-info/METADATA": "Name: mss\nVersion: 10.2.0\n",
    }
    for module in (runtime, bootstrap):
        files[f"verdiclip/platform/{Path(module.__file__).name}"] = Path(
            module.__file__
        ).read_text(encoding="utf-8")
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8"))
    modules = {
        "verdiclip": SimpleNamespace(
            __file__=str(root / "verdiclip/__init__.py"), VERSION="1.2.3"
        ),
        "mss": SimpleNamespace(
            __file__=str(root / "mss/__init__.py"), __version__="10.2.0"
        ),
        "verdiclip.platform.helper_bootstrap": SimpleNamespace(
            __file__=str(root / "verdiclip/platform/helper_bootstrap.py")
        ),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(
        runtime.metadata,
        "distribution",
        lambda name: PathDistribution(
            root / f"{name}-{'1.2.3' if name == 'verdiclip' else '10.2.0'}.dist-info"
        ),
    )
    return runtime.inspect_runtime()


def test_manifest_round_trip(installed: runtime.RuntimeManifest) -> None:
    """The fixed schema survives a bounded JSON round trip."""
    assert runtime.deserialize_manifest(installed.serialize()) == installed
    assert len(installed.roots) == 1
    assert installed.versions == ("1.2.3", "10.2.0")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", True),
        ("schema_version", 2),
        ("executable", "relative"),
        ("executable", "x" * 2049),
        ("roots", []),
        ("roots", ["relative"]),
        ("roots", ["/a", "/a"]),
        ("origins", []),
        ("versions", ["x", ""]),
        ("python_version", [3, 13, True]),
        ("python_version", [3, 13, 1.5]),
        ("python_version", [3, 13, 1000]),
        ("pointer_bits", True),
        ("pointer_bits", 128),
        ("extra", "untrusted"),
        ("roots", "not array"),
    ],
)
def test_invalid_manifest(
    installed: runtime.RuntimeManifest, field: str, value: object
) -> None:
    """Malformed manifests cannot become launch arguments."""
    data = json.loads(installed.serialize())
    data[field] = value
    with pytest.raises(ValueError, match=r".+"):
        bootstrap.proof_arguments(json.dumps(data))


def test_duplicate_and_size_bounds(installed: runtime.RuntimeManifest) -> None:
    """Reject duplicate fields and excessive UTF8 before JSON parsing."""
    payload = installed.serialize()
    with pytest.raises(ValueError, match="Duplicate"):
        runtime.deserialize_manifest(payload[:-1] + ',"schema_version":1}')
    for payload in ("x" * 16385, "é" * 9000):
        with pytest.raises(ValueError, match="byte bound"):
            runtime.deserialize_manifest(payload)


def test_reject_editable_and_frozen(
    installed: runtime.RuntimeManifest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unsupported runtime variants fail explicitly rather than using a relay."""
    direct = Path(installed.roots[0]) / "verdiclip-1.2.3.dist-info/direct_url.json"
    direct.write_bytes(b'{"dir_info":{"editable":true}}')
    with pytest.raises(ValueError, match="Editable"):
        runtime.inspect_runtime()
    direct.unlink()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    with pytest.raises(ValueError, match="Frozen"):
        runtime.inspect_runtime()


def test_bootstrap_ignores_poison(
    installed: runtime.RuntimeManifest, tmp_path: Path
) -> None:
    """Real isolated child ignores CWD, PYTHONPATH and user-site startup hooks."""
    poison = tmp_path / "poison"
    poison.mkdir()
    marker = tmp_path / "poison-ran"
    source = (
        f"from pathlib import Path; Path({str(marker)!r}).touch(); "
        "raise RuntimeError('poison')"
    )
    for name in ("mss.py", "sitecustomize.py", "usercustomize.py"):
        (poison / name).write_bytes(source.encode())
    result = subprocess.run(  # noqa: S603 - fixed authored proof, local fixture data
        [installed.executable, *bootstrap.proof_arguments(installed.serialize())],
        cwd=poison,
        env={**os.environ, "PYTHONPATH": str(poison), "PYTHONUSERBASE": str(poison)},
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode()
    assert not marker.exists()


@pytest.mark.parametrize(
    "field", ["origins", "versions", "python_version", "pointer_bits", "executable"]
)
def test_child_rejects_identity_mismatch(
    installed: runtime.RuntimeManifest, tmp_path: Path, field: str
) -> None:
    """A syntactically valid manifest cannot attest different installed code."""
    data = json.loads(installed.serialize())
    if field == "origins":
        data[field][0] = str(tmp_path / "other.py")
    elif field == "versions":
        data[field][0] = "9.9.9"
    elif field == "python_version":
        data[field][2] += 1
    elif field == "pointer_bits":
        data[field] = 32 if installed.pointer_bits == 64 else 64
    else:
        data[field] = str(tmp_path / "other-python")
    result = subprocess.run(  # noqa: S603 - fixed authored proof, local fixture data
        [installed.executable, *bootstrap.proof_arguments(json.dumps(data))],
        cwd=tmp_path,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode != 0


@pytest.mark.parametrize(
    "change", ["missing", "origin", "version", "helper", "base", "metadata"]
)
def test_inspection_rejects_unqualified_install(
    installed: runtime.RuntimeManifest, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """Discovery uses only loaded identities and rejects inconsistent installations."""
    if change == "missing":
        monkeypatch.delitem(sys.modules, "mss")
    elif change == "origin":
        monkeypatch.setattr(sys.modules["mss"], "__file__", installed.origins[0])
    elif change == "version":
        monkeypatch.setattr(sys.modules["mss"], "__version__", "0.0.0")
    elif change == "helper":
        monkeypatch.setattr(
            sys.modules["verdiclip.platform.helper_bootstrap"],
            "__file__",
            installed.origins[0],
        )
    elif change == "base":
        monkeypatch.delattr(sys, "_base_executable")
    else:
        direct = Path(installed.roots[0]) / "verdiclip-1.2.3.dist-info/direct_url.json"
        direct.write_bytes(b"[]")
    with pytest.raises(ValueError, match=r".+"):
        runtime.inspect_runtime()


def test_gui_import_rejected(
    installed: runtime.RuntimeManifest, tmp_path: Path
) -> None:
    """A package side effect loading Qt makes the proof fail before provider entry."""
    source = Path(installed.origins[1])
    source.write_bytes(
        b"import sys\nsys.modules['PySide6']=object()\n__version__='10.2.0'\n"
    )
    result = subprocess.run(  # noqa: S603 - fixed authored proof, local fixture data
        [installed.executable, *bootstrap.proof_arguments(installed.serialize())],
        cwd=tmp_path,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode != 0
    assert b"GUI imported" in result.stderr


def test_proof_requires_isolation(installed: runtime.RuntimeManifest) -> None:
    """Calling verification in an ordinary parent cannot prove child isolation."""
    with pytest.raises(RuntimeError, match="Isolated"):
        bootstrap.verify_runtime(installed.serialize())
