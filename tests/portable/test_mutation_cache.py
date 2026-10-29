"""Whole-commit verdict contexts prevent reuse through changed dependencies."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from scripts.mutation_cache import CONTEXT_FILE, STATE, context, prefix, prepare, seal
from scripts.mutation_identity import candidate_identities


@pytest.fixture
def environment() -> dict[str, str]:
    """A fixed runner identity with no credentials or desktop dependencies."""
    return {"GITHUB_SHA": "a" * 40, "RUNNER_OS": "Linux", "RUNNER_ARCH": "X64"}


def cached(root: Path, environment: dict[str, str]) -> Path:
    """Write an intentionally tiny informational prior verdict inventory."""
    cache = root / "mutants"
    cache.mkdir()
    (cache / CONTEXT_FILE).write_text(json.dumps(context(environment)))
    (cache / "prior.meta").write_text("old killed verdict")
    return cache


def test_same_commit_and_platform_resume_is_preserved(
    tmp_path: Path, environment: dict[str, str]
) -> None:
    """Exact-context partial progress remains available to the identity guard."""
    cache = cached(tmp_path, environment)
    receipt = prepare(tmp_path, environment)
    assert receipt["restored"] is True
    assert receipt["discarded"] is False
    assert (cache / "prior.meta").read_text() == "old killed verdict"
    assert prefix(environment, "f" * 64) == prefix(dict(environment), "f" * 64)


@pytest.mark.parametrize(
    ("field", "value"),
    [("GITHUB_SHA", "b" * 40), ("RUNNER_OS", "Windows"), ("RUNNER_ARCH", "ARM64")],
)
def test_commit_os_or_arch_change_quarantines_old_results(
    tmp_path: Path, environment: dict[str, str], field: str, value: str
) -> None:
    """A matching mutant body cannot broaden the outer execution context."""
    cached(tmp_path, environment)
    changed = {**environment, field: value}
    assert prefix(environment, "f" * 64) != prefix(changed, "f" * 64)
    receipt = prepare(tmp_path, changed)
    assert receipt["restored"] is False
    assert receipt["discarded"] is True
    assert not (tmp_path / "mutants").exists()
    archive = tmp_path / str(receipt["quarantine"])
    assert (archive / "prior.meta").read_text() == "old killed verdict"


def test_changed_helper_cannot_reuse_an_unchanged_candidate(
    tmp_path: Path, environment: dict[str, str]
) -> None:
    """The parent reproduction changes semantics without changing candidate AST."""
    old = "def helper():\n return 1\ndef x_f__mutmut_1():\n return helper() * 1\n"
    changed = old.replace(" return 1\n", " return 0\n", 1)
    assert candidate_identities(old) == candidate_identities(changed)

    def original(helper: Callable[[], int]) -> int:
        return helper() * 0

    def mutant(helper: Callable[[], int]) -> int:
        return helper() * 1

    def old_helper() -> int:
        return 1

    def new_helper() -> int:
        return 0

    assert original(old_helper) == original(new_helper) == 0
    assert mutant(old_helper) != 0
    assert mutant(new_helper) == 0
    cached(tmp_path, environment)
    receipt = prepare(tmp_path, {**environment, "GITHUB_SHA": "b" * 40})
    assert receipt["discarded"] is True
    assert not (tmp_path / "mutants/prior.meta").exists()


@pytest.mark.parametrize("content", [None, "bad", "{}", '{"schema":true}'])
def test_missing_or_legacy_context_is_cold_not_current(
    tmp_path: Path, environment: dict[str, str], content: str | None
) -> None:
    """Unsupported cache formats remain informational in quarantine."""
    cache = cached(tmp_path, environment)
    path = cache / CONTEXT_FILE
    if content is None:
        path.unlink()
    else:
        path.write_text(content)
    receipt = prepare(tmp_path, environment)
    assert receipt["discarded"] is True
    assert receipt["restored"] is False


def test_only_verified_identity_can_seal_context(
    tmp_path: Path, environment: dict[str, str]
) -> None:
    """Saving partial progress still requires valid original/candidate identity."""
    prepare(tmp_path, environment)
    (tmp_path / "mutants").mkdir()
    with pytest.raises(ValueError, match="Unqualified"):
        seal(tmp_path, environment)
    receipt = tmp_path / ".test-tmp/mutation-identity/receipt.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({"verified": True, "originals_unchanged": True}))
    sealed = seal(tmp_path, environment)
    assert sealed["sealed"] is True
    assert json.loads((tmp_path / "mutants" / CONTEXT_FILE).read_text()) == context(
        environment
    )
    assert json.loads((tmp_path / STATE).read_text())["sealed"] is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("GITHUB_SHA", ""),
        ("GITHUB_SHA", "x" * 40),
        ("RUNNER_OS", "../Linux"),
        ("RUNNER_ARCH", ""),
    ],
)
def test_missing_or_malformed_execution_identity_fails_closed(
    environment: dict[str, str], field: str, value: str
) -> None:
    """No fallback prefix can cross an unknown execution boundary."""
    with pytest.raises(ValueError, match="verdict context"):
        prefix({**environment, field: value}, "f" * 64)


def test_workflow_has_no_broad_restore_fallback() -> None:
    """Production restore/save share only the exact-context generated prefix."""
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/mutation.yml").read_text(encoding="utf-8")
    assert "python scripts/mutation_cache.py prefix" in workflow
    assert "python scripts/mutation_cache.py prepare" in workflow
    assert "python scripts/mutation_cache.py seal" in workflow
    assert "mutmut-v4-" not in workflow
    restore = workflow.split("      - name: Restore earlier verdicts", 1)[1].split(
        "      - name:", 1
    )[0]
    lines = restore.split("restore-keys: |", 1)[1].strip().splitlines()
    assert lines == ["${{ steps.cache_plan.outputs.prefix }}"]
    assert (
        "key: ${{ steps.cache_plan.outputs.prefix }}"
        "${{ github.run_id }}-${{ github.run_attempt }}" in restore
    )


def test_seal_refuses_a_context_marker_symlink_without_native_link_creation(
    tmp_path: Path, environment: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A qualified cache directory does not authorize a redirected output file."""
    prepare(tmp_path, environment)
    cache = tmp_path / "mutants"
    cache.mkdir()
    identity = tmp_path / ".test-tmp/mutation-identity/receipt.json"
    identity.parent.mkdir(parents=True)
    identity.write_text(json.dumps({"verified": True, "originals_unchanged": True}))
    marker = cache / CONTEXT_FILE
    original = Path.is_symlink

    def is_symlink(path: Path) -> bool:
        return path == marker or original(path)

    monkeypatch.setattr(Path, "is_symlink", is_symlink)
    with pytest.raises(ValueError, match="Unsafe mutation verdict context output"):
        seal(tmp_path, environment)
    assert not marker.exists()
    assert json.loads((tmp_path / STATE).read_text())["sealed"] is False


def test_prepare_cannot_relabel_a_previous_verified_identity_lease(
    tmp_path: Path, environment: dict[str, str]
) -> None:
    """Context preparation must precede a new identity generation/verification."""
    lease = tmp_path / ".test-tmp/mutation-identity"
    lease.mkdir(parents=True)
    (lease / "receipt.json").write_text(
        json.dumps({"verified": True, "originals_unchanged": True})
    )
    with pytest.raises(ValueError, match="Previous identity lease"):
        prepare(tmp_path, environment)
    assert not (tmp_path / STATE).exists()
    assert (lease / "receipt.json").is_file()
