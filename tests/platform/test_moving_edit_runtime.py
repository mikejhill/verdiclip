"""Closed moving profile identities must not silently expand old profiles."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from verdiclip.platform import moving_edit_runtime as runtime
from verdiclip.platform import sequence_runtime


def manifest(tmp_path: Path) -> runtime.MovingEditRuntimeManifest:
    """Construct bounded manifest data without claiming installed provenance."""
    root = str(tmp_path.resolve())
    return runtime.MovingEditRuntimeManifest(
        str(tmp_path / "python.exe"),
        (root,),
        tuple(
            str(tmp_path / (name.replace(".", "/") + ".py"))
            for name in runtime._MODULES
        ),
        ("0.13.3", "10.2.0"),
        (3, 13, 5),
        64,
    )


def test_exact_closed_profile(tmp_path: Path) -> None:
    """Keep the old profile and new fixed origin count distinct."""
    value = manifest(tmp_path)
    assert len(runtime._MODULES) == 23
    assert len(set(runtime._MODULES)) == 23
    assert "verdiclip.capture.moving_edit_frame" in runtime._MODULES
    assert "verdiclip.capture.retained_frame" not in runtime._MODULES
    assert "verdiclip.platform.moving_edit_channel" in runtime._MODULES
    assert runtime.deserialize_manifest(value.serialize()) == value
    assert json.loads(value.serialize())["schema_version"] == 6
    assert sequence_runtime._SCHEMA == 5
    assert len(sequence_runtime._MODULES) == 21


@pytest.mark.parametrize("schema", [True, 4, 5, 7, "6"])
def test_reject_other_schema(tmp_path: Path, schema: object) -> None:
    """Reject scalar coercion and every nonmatching schema."""
    data = json.loads(manifest(tmp_path).serialize())
    data["schema_version"] = schema
    with pytest.raises(ValueError, match=r".+"):
        runtime.deserialize_manifest(json.dumps(data))


def test_origin_count_and_manifest_limit(tmp_path: Path) -> None:
    """Reject incomplete origins and oversized manifests before use."""
    value = manifest(tmp_path)
    with pytest.raises(ValueError, match=r".+"):
        replace(value, origins=value.origins[:-1]).serialize()
    with pytest.raises(ValueError, match=r".+"):
        runtime.deserialize_manifest(" " * 16_385)


def test_loaded_qt_origin_is_required_before_distribution_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An installed Qt distribution cannot substitute for an actually loaded module."""
    distribution = runtime.metadata.distribution("PySide6_Essentials")
    monkeypatch.delitem(runtime.sys.modules, "PySide6.QtCore", raising=False)
    with pytest.raises(ValueError, match="must already be loaded"):
        runtime._qt_origin("PySide6.QtCore", distribution)
