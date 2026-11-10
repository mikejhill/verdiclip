"""Retain native timing diagnostics without extending verdict trust or scope."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_native_logging_is_enabled_outside_restored_verdicts() -> None:
    """The locked logger records actual child time outside the cache directory."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    configuration = project["tool"]["mutmut"]
    assert configuration["log_to_file"] is True
    assert configuration["log_file_path"] == "mutation-timing.log"
    assert configuration["process_isolation"] == "forkserver"
    assert configuration["forkserver_warmup"] == "collect"
    assert configuration["source_paths"] == ["src/verdiclip/"]
    assert "only_mutate" not in configuration


def test_all_native_rotations_are_uploaded_as_diagnostics() -> None:
    """Current-attempt artifacts retain the bounded logger's three backups."""
    workflow = (ROOT / ".github/workflows/mutation.yml").read_text()
    upload = workflow.split("      - name: Upload results", 1)[1]
    assert "if: always()" in upload
    assert "name: mutation-results-${{ github.run_attempt }}" in upload
    assert "            mutation-timing.log\n" in upload
    assert "            mutation-timing.log.[123]\n" in upload
    assert "mutants/mutation-timing" not in workflow
    assert 'MUTATION_FLOOR: "72"' in workflow
    assert "cancel-in-progress: false" in workflow
