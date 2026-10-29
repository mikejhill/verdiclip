"""Staged tooling contracts retain the same checked workflow inputs."""

from __future__ import annotations

import runpy
import shutil
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import cast


def test_configured_staging_preserves_real_workflow_contracts(tmp_path: Path) -> None:
    """Run unchanged contract functions with files from configured copy roots."""
    root = Path(__file__).resolve().parents[2]
    configuration = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    staged = tmp_path / "mutants"
    staged.mkdir()
    # Use mutmut3.8's directory/file copy semantics. Files do not implicitly
    # create parents; selecting a deep individual YAML file is insufficient.
    for entry in configuration["tool"]["mutmut"]["also_copy"]:
        source = root / entry
        destination = staged / entry
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)
    cases = (
        ("test_mutation_cache.py", "test_workflow_has_no_broad_restore_fallback"),
        (
            "test_mutation_deadline.py",
            "test_baseline_deadline_reserves_failure_evidence",
        ),
    )
    for name, contract in cases:
        relative = Path("tests/portable") / name
        destination = staged / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / relative, destination)
        namespace = runpy.run_path(str(destination))
        cast(Callable[[], None], namespace[contract])()
    workflow = Path(".github/workflows/mutation.yml")
    assert (staged / workflow).read_bytes() == (root / workflow).read_bytes()
