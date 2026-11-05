"""The read-only audit distinguishes empty files, tampering and measured costs."""

import hashlib
import json
from pathlib import Path

import pytest
from scripts.mutation_cache_audit import audit, read_owned, seconds
from scripts.mutation_identity import candidate_identities


def cache_fixture(root: Path) -> Path:
    """Verify the bounded data-only audit contract."""
    cache = root / "mutants"
    cache.mkdir()
    source = "def x_f__mutmut_1():\n return 2\n"
    (cache / "example.py").write_text(source)
    (cache / "empty.py").write_text("VALUE = 1\n")
    metadata = {
        "exit_code_by_key": {"example.x_f__mutmut_1": 1, "example.x_f__mutmut_2": None},
        "durations_by_key": {"example.x_f__mutmut_1": 2.5},
        "estimated_durations_by_key": {"example.x_f__mutmut_2": 4},
    }
    (cache / "example.py.meta").write_text(json.dumps(metadata))
    (cache / "empty.py.meta").write_text(json.dumps({name: {} for name in metadata}))
    marker = {
        "format": 1,
        "tool": "3.8.0",
        "identities": {"example.py": candidate_identities(source)},
        "metadata_sha256": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in cache.glob("*.meta")
        },
    }
    (cache / ".identity-provenance.json").write_text(json.dumps(marker))
    (cache / ".verdict-context.json").write_text("{}")
    return cache


def test_empty_metadata_is_distinct_from_modified_verdicts(tmp_path: Path) -> None:
    """Verify the bounded data-only audit contract."""
    cache = cache_fixture(tmp_path)
    before = {path.name: path.read_bytes() for path in cache.iterdir()}
    report = audit(cache)
    assert {path.name: path.read_bytes() for path in cache.iterdir()} == before
    empty, example = report["files"]
    assert empty["reasons"] == [
        "no_generated_candidates",
        "candidate_inventory_mismatch",
    ]
    assert empty["completed"] == 0
    assert example["reasons"] == []
    assert example["measured_seconds_sum"] == 2.5
    assert example["unchecked_estimated_seconds_sum"] == 4
    path = cache / "example.py.meta"
    path.write_text(path.read_text() + " ")
    assert audit(cache)["files"][1]["reasons"] == ["metadata_digest_mismatch"]


def test_changed_candidate_body_is_not_trusted(tmp_path: Path) -> None:
    """Verify the bounded data-only audit contract."""
    cache = cache_fixture(tmp_path)
    (cache / "example.py").write_text("def x_f__mutmut_1():\n return 99\n")
    assert audit(cache)["files"][1]["reasons"] == ["candidate_inventory_mismatch"]


@pytest.mark.parametrize("value", [True, -1, float("nan"), float("inf"), "3"])
def test_invalid_durations_are_not_measurements(value: object) -> None:
    """Verify the bounded data-only audit contract."""
    assert seconds(value) is None


def test_escape_is_rejected(tmp_path: Path) -> None:
    """Verify the bounded data-only audit contract."""
    root = tmp_path / "cache"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("not cache data")
    with pytest.raises(ValueError, match="Unsafe"):
        read_owned(root, outside)
