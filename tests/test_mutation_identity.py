"""Cache identities survive formatting, never semantic/ordinal drift."""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from scripts import mutation_identity
from scripts.mutation_identity import candidate_identities, reconcile, verify
from scripts.mutation_identity_probe import preserve_logs


def metadata() -> dict[str, Any]:
    """The locked tool's actual metadata shape, with one populated verdict."""
    return {
        "exit_code_by_key": {"probe.x_f__mutmut_1": 1},
        "hash_by_function_name": {"x_f": "original"},
        "durations_by_key": {},
        "estimated_durations_by_key": {},
        "type_check_error_by_key": {},
    }


@pytest.mark.parametrize("body", ["return 11", "return None", "raise ValueError()"])
def test_same_ordinal_and_original_hash_cannot_reuse_changed_body(body: str) -> None:
    """Reproduce the unchanged-source ordinal bug."""
    old = metadata()
    fresh = metadata()
    fresh["exit_code_by_key"]["probe.x_f__mutmut_1"] = None
    before = candidate_identities("def x_f__mutmut_1():\n return 21\n")
    after = candidate_identities(f"def x_f__mutmut_1():\n {body}\n")
    result, reused = reconcile(old, fresh, before, after)
    assert reused == 0
    assert result["exit_code_by_key"]["probe.x_f__mutmut_1"] is None


def test_reuse_requires_original_and_candidate_identity() -> None:
    """No original or candidate mismatch may retain a verdict."""
    identities = candidate_identities("def x_f__mutmut_1():\n return 21\n")
    assert reconcile(metadata(), metadata(), identities, identities)[1] == 1
    assert reconcile(metadata(), metadata(), {}, identities)[1] == 0
    fresh = metadata()
    fresh["hash_by_function_name"]["x_f"] = "changed"
    assert reconcile(metadata(), fresh, identities, identities)[1] == 0


def test_source_locations_do_not_change_candidate_identity() -> None:
    """Location-only differences do not invalidate a semantic identity."""
    first = "def x_f__mutmut_1():\n return 21\n"
    assert candidate_identities(first) == candidate_identities("\n\n" + first)
    with pytest.raises(ValueError, match="Ambiguous"):
        candidate_identities(first + first)


@pytest.mark.parametrize("drift", ["candidate", "source", "missing"])
def test_postflight_drift_deletes_unqualified_metadata_and_refuses_success(
    tmp_path: Path, drift: str
) -> None:
    """Drift blocks success and cache saving."""
    cache = tmp_path / "mutants/src"
    cache.mkdir(parents=True)
    state = tmp_path / ".test-tmp/mutation-identity"
    state.mkdir(parents=True)
    original = tmp_path / "src/f.py"
    original.parent.mkdir()
    original.write_text("original")
    code = "def x_f__mutmut_1():\n return 21\n"
    generated = cache / "f.py"
    generated.write_text(code)
    meta = cache / "f.py.meta"
    meta.write_text(json.dumps(metadata()))

    expected = {
        "identities": {"src/f.py": candidate_identities(code)},
        "originals": {"src/f.py": hashlib.sha256(b"original").hexdigest()},
    }
    (state / "expected.json").write_text(json.dumps(expected))
    (state / "receipt.json").write_text(json.dumps({"verified": False}))
    if drift == "candidate":
        generated.write_text(code.replace("21", "11"))
    elif drift == "source":
        original.write_text("changed")
    else:
        generated.unlink()
    with pytest.raises(ValueError, match="drifted"):
        verify(tmp_path)
    assert not meta.exists()
    assert json.loads((state / "receipt.json").read_text())["verified"] is False


def test_postflight_accepts_only_identical_vector(tmp_path: Path) -> None:
    """Only a successful postflight can write cache provenance."""
    cache = tmp_path / "mutants"
    cache.mkdir()
    state = tmp_path / ".test-tmp/mutation-identity"
    state.mkdir(parents=True)
    (state / "expected.json").write_text(
        json.dumps({"identities": {}, "originals": {}})
    )
    (state / "receipt.json").write_text(json.dumps({"verified": False}))
    assert verify(tmp_path)["verified"] is True


def identity_cache(root: Path) -> tuple[Path, Path]:
    """Create a current-looking legacy cache, without claiming its provenance."""
    cache = root / "mutants"
    (cache / "src").mkdir(parents=True)
    code = "def x_f__mutmut_1():\n return 21\n"
    (cache / "src/f.py").write_text(code)
    path = cache / "src/f.py.meta"
    path.write_text(json.dumps(metadata()))
    original = root / "src/f.py"
    original.parent.mkdir()
    original.write_text("def f():\n return 20\n")
    state = root / ".test-tmp/mutation-identity"
    state.mkdir(parents=True)
    (state / "expected.json").write_text(
        json.dumps(
            {
                "identities": {"src/f.py": candidate_identities(code)},
                "originals": {
                    "src/f.py": hashlib.sha256(original.read_bytes()).hexdigest()
                },
            }
        )
    )
    (state / "receipt.json").write_text(json.dumps({"verified": False}))
    return cache, path


def test_legacy_is_cold_and_marker_binds_body_and_metadata(tmp_path: Path) -> None:
    """Co-located legacy body/meta can already contain a misassigned verdict."""
    cache, path = identity_cache(tmp_path)

    def trusted() -> dict[str, dict[str, Any]]:
        return mutation_identity.trusted_metadata(
            cache,
            mutation_identity._metadata(cache),
            mutation_identity._inventory(cache),
        )

    assert trusted() == {}
    assert verify(tmp_path)["verified"]
    assert len(trusted()) == 1
    changed = metadata()
    changed["exit_code_by_key"]["probe.x_f__mutmut_1"] = 0
    path.write_text(json.dumps(changed))
    assert trusted() == {}
    path.write_text(json.dumps(metadata()))
    (cache / "src/f.py").write_text("def x_f__mutmut_1():\n return 11\n")
    assert trusted() == {}


@pytest.mark.parametrize("bad", [None, [], {"exit_code_by_key": []}])
def test_malformed_old_files_are_fresh_and_preserved(
    tmp_path: Path, bad: object
) -> None:
    """Unavailable old identities are cold, not an availability failure."""
    cache, path = identity_cache(tmp_path)
    path.write_text(json.dumps(bad))
    (cache / "src/f.py").write_text("def broken(")
    assert mutation_identity._metadata(cache, strict=False) == {}
    assert mutation_identity._inventory(cache, strict=False) == {}
    assert path.is_file()
    assert (cache / "src/f.py").read_text() == "def broken("


@pytest.mark.parametrize("bad", [True, False, "1", 999])
def test_invalid_cached_verdict_is_never_an_integer_kill(bad: object) -> None:
    """JSON booleans must not alias exit1/killed or exit0/survived."""
    identities = candidate_identities("def x_f__mutmut_1():\n return 21\n")
    old = metadata()
    old["exit_code_by_key"]["probe.x_f__mutmut_1"] = bad
    fresh, reused = reconcile(old, metadata(), identities, identities)
    assert reused == 0
    assert fresh["exit_code_by_key"]["probe.x_f__mutmut_1"] is None


def test_invalid_optional_statistics_are_dropped() -> None:
    """A valid verdict does not authorize poisoned timing/error metadata."""
    identities = candidate_identities("def x_f__mutmut_1():\n return 21\n")
    old = metadata()
    key = "probe.x_f__mutmut_1"
    old["durations_by_key"][key] = True
    old["estimated_durations_by_key"][key] = float("nan")
    old["type_check_error_by_key"][key] = 123
    fresh, reused = reconcile(old, metadata(), identities, identities)
    assert reused == 1
    assert fresh["durations_by_key"] == {}
    assert fresh["estimated_durations_by_key"] == {}
    assert fresh["type_check_error_by_key"] == {}


def test_probe_failure_replaces_success_and_preserves_only_named_logs(
    tmp_path: Path,
) -> None:
    """A failed rerun cannot publish prior success or arbitrary fixture files."""
    root = tmp_path / "fixture"
    root.mkdir()
    (root / "cold-prepare.log").write_text("bounded fixture output")
    (root / "unexpected.log").write_text("must not copy")
    receipt = tmp_path / "mutation-identity-probe.json"
    receipt.write_text(json.dumps({"status": "running"}))
    preserve_logs(root, tmp_path)
    assert json.loads(receipt.read_text())["status"] == "failed"
    assert (
        tmp_path / "mutation-identity-probe-cold-prepare.log"
    ).read_text() == "bounded fixture output"
    assert not (tmp_path / "mutation-identity-probe-unexpected.log").exists()
    receipt.write_text(json.dumps({"status": "success"}))
    preserve_logs(root, tmp_path)
    assert json.loads(receipt.read_text())["status"] == "success"
