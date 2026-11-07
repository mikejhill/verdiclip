"""Installed runtime contracts; synthetic roots are not native qualification."""

import json
import os
import subprocess
import sys
from dataclasses import replace
from importlib import import_module
from importlib.metadata import PathDistribution
from pathlib import Path
from types import SimpleNamespace

import pytest

from verdiclip.platform import helper_runtime
from verdiclip.platform import sequence_bootstrap as bootstrap
from verdiclip.platform import sequence_peer as helper_peer
from verdiclip.platform import sequence_runtime as runtime


@pytest.fixture
def installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> runtime.SequenceRuntimeManifest:
    """Build a byte-exact installed package including the actual bootstrap code."""
    root = tmp_path / "runtime café"
    files = {
        "verdiclip/__init__.py": "VERSION='1.2.3'\n",
        "verdiclip/platform/__init__.py": "",
        "mss/__init__.py": "__version__='10.2.0'\n",
        "mss/base.py": "class MSSBase: pass\n",
        "verdiclip/capture/__init__.py": "",
        "verdiclip-1.2.3.dist-info/METADATA": "Name: verdiclip\nVersion: 1.2.3\n",
        "mss-10.2.0.dist-info/METADATA": "Name: mss\nVersion: 10.2.0\n",
    }
    fixed = tuple(import_module(name) for name in runtime._MODULES[2:])
    for module in (runtime, *fixed):
        assert module.__file__ is not None
        files[module.__name__.replace(".", "/") + ".py"] = Path(
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
    for module in fixed:
        name = module.__name__
        modules[name] = SimpleNamespace(
            __file__=str(root / (name.replace(".", "/") + ".py"))
        )
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    installed_distribution = runtime.metadata.distribution
    monkeypatch.setattr(
        runtime.metadata,
        "distribution",
        lambda name: (
            PathDistribution(
                root
                / f"{name}-{'1.2.3' if name == 'verdiclip' else '10.2.0'}.dist-info"
            )
            if name in ("verdiclip", "mss")
            else installed_distribution(name)
        ),
    )
    return runtime.inspect_runtime()


def test_manifest_round_trip(installed: runtime.SequenceRuntimeManifest) -> None:
    """The fixed schema survives a bounded JSON round trip."""
    assert runtime.deserialize_manifest(installed.serialize()) == installed
    assert len(installed.roots) == 1
    assert installed.versions == ("1.2.3", "10.2.0")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", True),
        ("schema_version", 1),
        ("schema_version", 3),
        ("schema_version", 4),
        ("schema_version", 5.0),
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
    installed: runtime.SequenceRuntimeManifest, field: str, value: object
) -> None:
    """Malformed manifests cannot become launch arguments."""
    data = json.loads(installed.serialize())
    data[field] = value
    with pytest.raises(ValueError, match=r".+"):
        bootstrap.proof_arguments(json.dumps(data))


def test_duplicate_and_size_bounds(installed: runtime.SequenceRuntimeManifest) -> None:
    """Reject duplicate fields and excessive UTF8 before JSON parsing."""
    payload = installed.serialize()
    with pytest.raises(ValueError, match="Duplicate"):
        runtime.deserialize_manifest(payload[:-1] + ',"schema_version":1}')
    for payload in ("x" * 16385, "é" * 9000):
        with pytest.raises(ValueError, match="byte bound"):
            runtime.deserialize_manifest(payload)


def test_reject_editable_and_frozen(
    installed: runtime.SequenceRuntimeManifest, monkeypatch: pytest.MonkeyPatch
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
    installed: runtime.SequenceRuntimeManifest, tmp_path: Path
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
    installed: runtime.SequenceRuntimeManifest, tmp_path: Path, field: str
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
    installed: runtime.SequenceRuntimeManifest,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
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
    installed: runtime.SequenceRuntimeManifest, tmp_path: Path
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


def test_proof_requires_isolation(installed: runtime.SequenceRuntimeManifest) -> None:
    """Calling verification in an ordinary parent cannot prove child isolation."""
    with pytest.raises(RuntimeError, match="Isolated"):
        bootstrap.verify_runtime(installed.serialize())


def test_fixed_peer_arguments_are_data_after_attestation(
    installed: runtime.SequenceRuntimeManifest,
) -> None:
    """Only the fixed GUI-free sequence peer follows runtime verification."""
    launch = helper_peer.SequenceLaunch("VerdiClip-sequence-" + "a" * 32, "b" * 32, 7)
    arguments = bootstrap.sequence_arguments(installed.serialize(), launch.serialize())
    assert arguments[:3] == ("-I", "-S", "-c")
    assert arguments[3].startswith(bootstrap.BOOTSTRAP_SOURCE)
    assert arguments[4:] == (installed.serialize(), launch.serialize())
    assert "from verdiclip.platform.sequence_peer import run" in arguments[3]
    assert installed.schema_version == 5
    assert len(installed.origins) == 21


@pytest.mark.parametrize("index", range(2, 21))
def test_fixed_peer_module_origin_mismatch_fails(
    installed: runtime.SequenceRuntimeManifest,
    monkeypatch: pytest.MonkeyPatch,
    index: int,
) -> None:
    """Every fixed module origin is checked, not merely added as an import root."""
    name = runtime._MODULES[index]
    monkeypatch.setattr(sys.modules[name], "__file__", installed.origins[0])
    with pytest.raises(ValueError, match="Fixed helper origin"):
        runtime.inspect_runtime()


@pytest.mark.parametrize("field", ["roots", "versions"])
def test_direct_manifest_rejects_duplicate_roots_or_wrong_version_count(
    installed: runtime.SequenceRuntimeManifest, field: str
) -> None:
    """The immutable constructor enforces invariants independently of JSON parsing."""
    if field == "roots":
        with pytest.raises(ValueError, match="Duplicate runtime roots"):
            replace(installed, roots=(installed.roots[0], installed.roots[0]))
    else:
        with pytest.raises(ValueError, match="Invalid runtime distribution versions"):
            replace(installed, versions=("1",))


def test_manifest_serialized_byte_budget_includes_json_escaping(
    installed: runtime.SequenceRuntimeManifest,
) -> None:
    """Individually bounded Unicode paths can still overflow the aggregate envelope."""
    path = str(Path(installed.roots[0]).anchor) + "é" * 1500
    value = replace(installed, origins=(path,) * 21)
    with pytest.raises(ValueError, match="byte bound"):
        value.serialize()


@pytest.mark.parametrize("failure", [None, "protocol", "executable", "runtime", "gui"])
def test_bootstrap_in_process_verification_gates(
    installed: runtime.SequenceRuntimeManifest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: str | None,
) -> None:
    """Exercise child verification branches directly, not only in untraced children."""
    modules = {"PySide6.QtCore": object()} if failure == "gui" else {}
    monkeypatch.setattr(
        bootstrap,
        "sys",
        SimpleNamespace(
            flags=SimpleNamespace(isolated=True, no_site=True),
            executable=installed.executable,
            modules=modules,
        ),
    )
    monkeypatch.setattr(bootstrap, "inspect_child_runtime", runtime.inspect_runtime)
    expected = installed
    if failure == "protocol":
        monkeypatch.setattr(bootstrap.sequence_protocol, "VERSION", 1)
    elif failure == "executable":
        expected = replace(installed, executable=str(tmp_path / "other-python"))
    elif failure == "runtime":
        expected = replace(installed, versions=("9.9.9", installed.versions[1]))
    if failure is None:
        assert bootstrap.verify_runtime(expected.serialize()) is None
    else:
        messages = {
            "protocol": "Unsupported fixed peer protocol",
            "executable": "Child executable differs",
            "runtime": "Child runtime differs",
            "gui": "GUI imported",
        }
        with pytest.raises(RuntimeError, match=messages[failure]):
            bootstrap.verify_runtime(expected.serialize())


@pytest.mark.parametrize("schema", [4, 5.0, True])
def test_bootstrap_prefix_rejects_old_or_inexact_schema(
    installed: runtime.SequenceRuntimeManifest, tmp_path: Path, schema: object
) -> None:
    """The isolated prefix rejects incompatible schemas before importing packages."""
    data = json.loads(installed.serialize())
    data["schema_version"] = schema
    result = subprocess.run(  # noqa: S603 - fixed authored prefix, local fixture data
        [
            installed.executable,
            "-I",
            "-S",
            "-c",
            bootstrap.BOOTSTRAP_SOURCE,
            json.dumps(data),
        ],
        cwd=tmp_path,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode != 0
    assert b"Unexpected isolated interpreter identity" in result.stderr


def test_credit_module_must_be_loaded(
    installed: runtime.SequenceRuntimeManifest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An installed credit file alone cannot attest an absent loaded primitive."""
    assert installed.schema_version == 5
    monkeypatch.delitem(sys.modules, "verdiclip.capture.frame_credit")
    with pytest.raises(ValueError, match="must already be loaded"):
        runtime.inspect_runtime()


def test_fixed_origin_order_is_separate_from_single_frame() -> None:
    """The new proof extends, rather than reinterprets, the old runtime contract."""
    assert (
        *helper_runtime._MODULES,
        "verdiclip.platform.helper_runtime",
        "verdiclip.platform.owned_helper",
        "verdiclip.capture.retained_frame",
        "verdiclip.platform.sequence_protocol",
        "verdiclip.platform.sequence_runtime",
        "verdiclip.platform.sequence_bootstrap",
        "verdiclip.platform.sequence_peer",
        "verdiclip.platform.sequence_channel",
        "verdiclip.capture.sequence_credit",
        "verdiclip.capture.sequence_retention",
    ) == runtime._MODULES
    assert helper_runtime._SCHEMA == 4
    assert runtime._SCHEMA == 5


@pytest.mark.parametrize("name", runtime._MODULES)
def test_every_origin_must_be_loaded(
    installed: runtime.SequenceRuntimeManifest,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    """Parent inspection cannot claim any missing installed origin is loaded."""
    assert len(installed.origins) == 21
    monkeypatch.delitem(sys.modules, name)
    with pytest.raises(ValueError, match="must already be loaded"):
        runtime.inspect_runtime()


@pytest.mark.parametrize(
    "failure", [None, "origin", "duplicate", "bound", "schema", "gui"]
)
def test_failed_verification_never_reaches_fixed_entrypoint(
    installed: runtime.SequenceRuntimeManifest, tmp_path: Path, failure: str | None
) -> None:
    """A child failure cannot pass the actual fixed-source dispatch barrier."""
    marker = tmp_path / "entrypoint-ran"
    payload = installed.serialize()
    if failure == "origin":
        payload = replace(
            installed, origins=(installed.origins[1], *installed.origins[1:])
        ).serialize()
    elif failure == "duplicate":
        payload = payload[:-1] + ',"schema_version":5}'
    elif failure == "bound":
        payload = " " * 16385
    elif failure == "schema":
        payload = payload.replace('"schema_version":5', '"schema_version":4')
    elif failure == "gui":
        Path(installed.origins[1]).write_bytes(
            b"import sys\nsys.modules['PySide6']=object()\n__version__='10.2.0'\n"
        )
    # Instrument only the child fixture's entrypoint. Its verifier and every
    # other module remain the authored bytes, not a surrogate verification path.
    peer_path = Path(
        installed.origins[runtime._MODULES.index("verdiclip.platform.sequence_peer")]
    )
    peer_path.write_text(
        peer_path.read_text(encoding="utf-8")
        + "\ndef run(payload):\n    from pathlib import Path\n"
        + f"    Path({str(marker)!r}).touch()\n",
        encoding="utf-8",
    )
    launch = helper_peer.SequenceLaunch("VerdiClip-sequence-" + "a" * 32, "b" * 32, 7)
    result = subprocess.run(  # noqa: S603 - fixed bootstrap and local fixtures
        [
            installed.executable,
            "-I",
            "-S",
            "-c",
            bootstrap.SEQUENCE_BOOTSTRAP_SOURCE,
            payload,
            launch.serialize(),
        ],
        cwd=tmp_path,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert (result.returncode == 0) is (failure is None), result.stderr.decode()
    assert marker.exists() is (failure is None)


def test_child_binds_channel_file_without_importing_qt(
    installed: runtime.SequenceRuntimeManifest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the child binds the fixed channel file instead of a loaded origin."""
    child_modules: dict[str, object] = dict(sys.modules)
    child_modules.pop("verdiclip.platform.sequence_channel")
    child_modules = {
        name: value
        for name, value in child_modules.items()
        if name != "PySide6" and not name.startswith("PySide6.")
    }
    child_sys = SimpleNamespace(**vars(sys))
    child_sys.modules = child_modules
    child_sys.flags = SimpleNamespace(isolated=True, no_site=True)
    monkeypatch.setattr(runtime, "sys", child_sys)
    assert runtime.inspect_child_runtime() == installed
    with pytest.raises(ValueError, match="must already be loaded"):
        runtime.inspect_runtime()
    child_modules["PySide6.QtCore"] = object()
    with pytest.raises(RuntimeError, match="GUI imported"):
        runtime.inspect_child_runtime()
    child_modules.pop("PySide6.QtCore")
    child_modules["verdiclip.platform.sequence_channel"] = object()
    with pytest.raises(RuntimeError, match="Parent-only"):
        runtime.inspect_child_runtime()


def test_child_inspection_requires_isolation() -> None:
    """The file-only exception is never a parent attestation shortcut."""
    with pytest.raises(RuntimeError, match="Isolated"):
        runtime.inspect_child_runtime()


@pytest.mark.parametrize("change", ["missing", "directory", "symlink"])
def test_channel_file_is_fixed_regular_distribution_file(
    installed: runtime.SequenceRuntimeManifest, tmp_path: Path, change: str
) -> None:
    """Neither a missing file nor an alternate-root link attests the Qt facade."""
    path = Path(installed.origins[18])
    source = path.read_bytes()
    path.unlink()
    if change == "directory":
        path.mkdir()
    elif change == "symlink":
        alternate = tmp_path / "alternate-channel.py"
        alternate.write_bytes(source)
        path.symlink_to(alternate)
    with pytest.raises((ValueError, FileNotFoundError)):
        runtime.inspect_runtime()


def test_noneditable_direct_install_metadata_is_supported(
    installed: runtime.SequenceRuntimeManifest,
) -> None:
    """A regular direct install is valid without weakening the editable ban."""
    direct = Path(installed.roots[0]) / "verdiclip-1.2.3.dist-info/direct_url.json"
    direct.write_bytes(b'{"dir_info":{"editable":false}}')
    assert runtime.inspect_runtime() == installed


@pytest.mark.parametrize("name", ["PySide6.QtCore", "PySide6.QtNetwork", "shiboken6"])
def test_parent_requires_loaded_qt_modules(
    installed: runtime.SequenceRuntimeManifest,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    """The parent cannot manufacture Qt origin claims by importing missing code."""
    assert installed.schema_version == 5
    monkeypatch.delitem(sys.modules, name)
    with pytest.raises(ValueError, match=r"Qt.*loaded"):
        runtime.inspect_runtime()
    assert name not in sys.modules


@pytest.mark.parametrize("name", ["PySide6.QtCore", "PySide6.QtNetwork", "shiboken6"])
def test_parent_rejects_mixed_qt_origins(
    installed: runtime.SequenceRuntimeManifest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    name: str,
) -> None:
    """A module from an alternate root cannot borrow the installed version."""
    assert installed.schema_version == 5
    origin = sys.modules[name].__file__
    assert origin is not None
    alternate = tmp_path / Path(origin).name
    alternate.write_bytes(b"not-loaded-test-module")
    monkeypatch.setattr(sys.modules[name], "__file__", str(alternate))
    with pytest.raises(ValueError, match=r"Qt.*origin"):
        runtime.inspect_runtime()


@pytest.mark.parametrize("name", ["PySide6.QtCore", "shiboken6"])
def test_parent_rejects_mixed_qt_versions(
    installed: runtime.SequenceRuntimeManifest,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    """Loaded binding versions must match their own distribution metadata."""
    assert installed.schema_version == 5
    monkeypatch.setattr(sys.modules[name], "__version__", "0.0.0")
    with pytest.raises(ValueError, match=r"Qt.*version"):
        runtime.inspect_runtime()


@pytest.mark.parametrize(
    "failure", ["missing-record", "duplicate-record", "symlink", "mixed-version"]
)
def test_parent_requires_unambiguous_qt_distribution_inventory(
    installed: runtime.SequenceRuntimeManifest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: str,
) -> None:
    """Qt inventory and ABI version checks cannot borrow another installation."""
    assert installed.schema_version == 5
    package = "shiboken6" if failure == "mixed-version" else "PySide6-Essentials"
    name = "shiboken6" if failure == "mixed-version" else "PySide6.QtCore"
    version = "0.0.0" if failure == "mixed-version" else sys.modules[name].__version__
    root = tmp_path / "alternate-qt"
    info = root / "fixture.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(
        f"Name: {package}\nVersion: {version}\n", encoding="utf-8"
    )
    origin = sys.modules[name].__file__
    assert origin is not None
    relative = "PySide6/" + Path(origin).name
    record = relative + ",,\n"
    if failure == "missing-record":
        record = ""
    elif failure == "duplicate-record":
        record *= 2
    elif failure == "symlink":
        path = root / relative
        path.parent.mkdir()
        path.symlink_to(origin)
    else:
        monkeypatch.setattr(sys.modules[name], "__version__", version)
    (info / "RECORD").write_text(record, encoding="utf-8")
    original = runtime.metadata.distribution
    monkeypatch.setattr(
        runtime.metadata,
        "distribution",
        lambda value: PathDistribution(info) if value == package else original(value),
    )
    with pytest.raises(ValueError, match=r"Qt.*origin|Qt.*version"):
        runtime.inspect_runtime()
