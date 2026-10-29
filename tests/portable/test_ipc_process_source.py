"""Pure source-origin admission; never import Qt or open an IPC endpoint."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from tests.ipc_process_source import CHILD_IMPORT_PREFIX


def candidate(root: Path, marker: str) -> Path:
    """Write a tiny fake package; importing it cannot touch the desktop."""
    package = root / "verdiclip/shell"
    package.mkdir(parents=True)
    (package.parent / "__init__.py").write_text("", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    source = package / "instance.py"
    source.write_text(f"MARKER = {marker!r}\n", encoding="utf-8")
    return source.resolve()


def test_fresh_children_keep_exact_staged_source_and_selector(tmp_path: Path) -> None:
    """Both exec generations override inherited editable-source discovery."""
    original = candidate(tmp_path / "original/src", "original")
    staged = candidate(tmp_path / "mutants/src", "staged")
    checks = r"""
import os
from verdiclip.shell import instance
assert Path(instance.__file__).resolve() == expected_source
assert instance.MARKER == "staged"
assert os.environ["MUTANT_UNDER_TEST"] == "stats"
"""
    check = CHILD_IMPORT_PREFIX + checks
    outer = (
        CHILD_IMPORT_PREFIX
        + r"""
import subprocess
"""
        + checks
        + r"""
child = subprocess.run([sys.executable, "-c", sys.argv[2], sys.argv[1]],
                       capture_output=True, text=True, timeout=5, check=False)
assert child.returncode == 0, child.stdout + child.stderr
"""
    )
    environment = {
        **os.environ,
        "PYTHONPATH": str(original.parents[2]),
        "MUTANT_UNDER_TEST": "stats",
    }
    result = subprocess.run(  # noqa: S603 - fixed pure fake-package scripts
        [sys.executable, "-c", outer, str(staged), check],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("layout", ["missing", "unrelated.py"])
def test_missing_or_unrelated_source_is_rejected(tmp_path: Path, layout: str) -> None:
    """Admission cannot silently fall back to an unrelated original package."""
    source = tmp_path / layout
    if layout != "missing":
        source.write_text("", encoding="utf-8")
    result = subprocess.run(  # noqa: S603 - fixed admission-only Python prefix
        [sys.executable, "-c", CHILD_IMPORT_PREFIX, str(source)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert result.returncode != 0
    assert (
        "FileNotFoundError" in result.stderr
        or "Unexpected IPC candidate" in result.stderr
    )
