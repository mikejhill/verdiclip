"""Structured evidence rejects incomplete runs without opening real GitHub issues."""

from __future__ import annotations

import ast
import json
import shutil
import tomllib
from pathlib import Path

import pytest
from scripts.mutation_cache import context
from scripts.mutation_result import result


@pytest.fixture
def evidence(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """Exercise evidence qualification without running mutation or Qt."""
    env = {
        "GITHUB_SHA": "a" * 40,
        "GITHUB_RUN_ID": "10",
        "GITHUB_RUN_ATTEMPT": "1",
        "MUTATION_FLOOR": "72",
        "RUNNER_OS": "Linux",
        "RUNNER_ARCH": "X64",
    }
    env.update(
        dict.fromkeys(
            (
                "BASELINE_OUTCOME",
                "PROBE_OUTCOME",
                "MUTATION_OUTCOME",
                "IDENTITY_OUTCOME",
                "CACHE_CONTEXT_OUTCOME",
            ),
            "success",
        )
    )
    (tmp_path / "mutation-run-state.json").write_text(
        json.dumps(
            {"run_completed": True, "source_sha": env["GITHUB_SHA"], "scope": []}
        )
    )
    (tmp_path / "mutation-identity-receipt.json").write_text(
        json.dumps(
            {
                "verified": True,
                "originals_unchanged": True,
                "tool": "3.8.0",
                "candidates": 100,
                "reused": 0,
            }
        )
    )
    (tmp_path / "mutation-cache-context.json").write_text(
        json.dumps(
            {"schema": 1, "context": context(env), "prepared": True, "sealed": True}
        )
    )
    return tmp_path, env


def verdicts(root: Path, killed: int) -> None:
    """Exercise evidence qualification without running mutation or Qt."""
    rows = []
    for index in range(100):
        status = "killed" if index < killed else "survived"
        rows.append(f"verdiclip.geometry.xfoo__mutmut_{index}: {status}")
    (root / "mutation-results.txt").write_text("\n".join(rows))


@pytest.mark.parametrize(
    ("score", "status"), [(71, "findings"), (72, "healthy"), (73, "healthy")]
)
def test_threshold_boundary(
    evidence: tuple[Path, dict[str, str]], score: int, status: str
) -> None:
    """Exercise evidence qualification without running mutation or Qt."""
    root, env = evidence
    verdicts(root, score)
    report = result(root, env)
    assert report["status"] == status
    assert report["complete"] is True
    assert report["fresh"] is True
    assert report["undetected"] == 100 - score


@pytest.mark.parametrize(
    "key", ["BASELINE_OUTCOME", "PROBE_OUTCOME", "MUTATION_OUTCOME", "IDENTITY_OUTCOME"]
)
def test_failed_phase_is_not_a_pass(
    evidence: tuple[Path, dict[str, str]], key: str
) -> None:
    """Exercise evidence qualification without running mutation or Qt."""
    root, env = evidence
    verdicts(root, 100)
    env[key] = "failure"
    assert result(root, env)["status"] == "incomplete"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("verified", False),
        ("originals_unchanged", False),
        ("candidates", 99),
        ("reused", 101),
        ("tool", "3.7.0"),
    ],
)
def test_inconsistent_identity(
    evidence: tuple[Path, dict[str, str]], field: str, value: object
) -> None:
    """Exercise evidence qualification without running mutation or Qt."""
    root, env = evidence
    verdicts(root, 100)
    path = root / "mutation-identity-receipt.json"
    data = json.loads(path.read_text())
    data[field] = value
    path.write_text(json.dumps(data))
    assert result(root, env)["status"] == "incomplete"


def test_historical_verdicts_are_explicit(
    evidence: tuple[Path, dict[str, str]],
) -> None:
    """Exercise evidence qualification without running mutation or Qt."""
    root, env = evidence
    verdicts(root, 80)
    path = root / "mutation-identity-receipt.json"
    data = json.loads(path.read_text())
    data["reused"] = 30
    path.write_text(json.dumps(data))
    report = result(root, env)
    assert report["status"] == "healthy"
    assert report["fresh"] is False
    assert report["historical_verdicts"] == 30


@pytest.mark.parametrize("content", [None, "bad", "{}"])
def test_missing_malformed_partial(
    evidence: tuple[Path, dict[str, str]], content: str | None
) -> None:
    """Exercise evidence qualification without running mutation or Qt."""
    root, env = evidence
    if content is not None:
        (root / "mutation-results.txt").write_text(content)
    assert result(root, env)["status"] == "incomplete"


def test_scope_and_revision_must_match(evidence: tuple[Path, dict[str, str]]) -> None:
    """Exercise evidence qualification without running mutation or Qt."""
    root, env = evidence
    verdicts(root, 100)
    for data in (
        {"run_completed": True, "source_sha": "b" * 40, "scope": []},
        {"run_completed": True, "source_sha": env["GITHUB_SHA"], "scope": ["geometry"]},
    ):
        (root / "mutation-run-state.json").write_text(json.dumps(data))
        assert result(root, env)["status"] == "incomplete"


@pytest.mark.parametrize("floor", ["nan", "inf", "-1", "101", "72.0000001"])
def test_invalid_threshold(evidence: tuple[Path, dict[str, str]], floor: str) -> None:
    """Exercise evidence qualification without running mutation or Qt."""
    root, env = evidence
    env["MUTATION_FLOOR"] = floor
    with pytest.raises(ValueError, match="Invalid mutation threshold"):
        result(root, env)


def test_mutation_staging_copies_tooling_imports(tmp_path: Path) -> None:
    """Mutmut removes the original checkout from imports; staging stays complete."""
    repository = Path(__file__).resolve().parents[2]
    configuration = tomllib.loads((repository / "pyproject.toml").read_text())
    assert "scripts/" in configuration["tool"]["mutmut"]["also_copy"]
    assert "." in configuration["tool"]["pytest"]["ini_options"]["pythonpath"]
    staging = tmp_path / "mutants"
    shutil.copytree(repository / "scripts", staging / "scripts")
    # Every scripts.* import in these tests must exist independently of the repo.
    imported = set()
    for test_file in Path(__file__).parent.glob("test_*.py"):
        for node in ast.walk(ast.parse(test_file.read_text())):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module is not None
                and node.module.startswith("scripts.")
            ):
                imported.add(node.module)
    assert imported
    for module in imported:
        path = staging.joinpath(*module.split(".")).with_suffix(".py")
        assert path.is_file()
        compile(path.read_text(), str(path), "exec")


@pytest.mark.parametrize(
    "change",
    ["missing", "legacy", "cross_commit", "cross_os", "cross_arch", "unsealed"],
)
def test_unqualified_context_is_never_complete(
    evidence: tuple[Path, dict[str, str]], change: str
) -> None:
    """Legacy historical scores remain informational rather than current evidence."""
    root, env = evidence
    verdicts(root, 100)
    path = root / "mutation-cache-context.json"
    data = json.loads(path.read_text())
    if change == "missing":
        path.unlink()
    else:
        if change == "legacy":
            data = {}
        elif change == "cross_commit":
            data["context"]["source_sha"] = "b" * 40
        elif change == "cross_os":
            data["context"]["os"] = "Windows"
        elif change == "cross_arch":
            data["context"]["arch"] = "ARM64"
        elif change == "unsealed":
            data["sealed"] = False
        path.write_text(json.dumps(data))
    assert result(root, env)["status"] == "incomplete"
