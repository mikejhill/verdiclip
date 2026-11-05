"""CI scope changes preserve tests, configuration and compatible verdicts."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
from scripts.mutation_ci import (
    MIGRATION_INPUTS,
    apply_scope,
    digest,
    legacy_cache_key,
    restore_scope,
    scoped_files,
)


def project(root: Path, *, newline: str = "\n") -> bytes:
    """Keep a nonmutation policy alongside every execution-sensitive field."""
    content = newline.join(
        (
            "[project]",
            'name = "fixture"',
            "[tool.pytest.ini_options]",
            'addopts = ["--strict-markers"]',
            "[tool.mutmut]",
            'source_paths = ["src/verdiclip/"]',
            'process_isolation = "forkserver"',
            "timeout_multiplier = 15.0",
            'pytest_add_cli_args = ["-p", "no:randomly"]',
            "",
        )
    ).encode()
    (root / "pyproject.toml").write_bytes(content)
    for relative in ("src/verdiclip/alpha.py", "src/verdiclip/nested/__init__.py"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def value(): return 1\n", encoding="utf-8")
    return content


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_scope_changes_only_generation_selection_and_restores_bytes(
    tmp_path: Path, newline: str
) -> None:
    """Scope remains generation-only; restore preserves the complete original file."""
    original = project(tmp_path, newline=newline)
    expected = tomllib.loads(original.decode())
    files = apply_scope(
        tmp_path, ["verdiclip.alpha.*", "verdiclip.nested.x*", "verdiclip.alpha.*"]
    )
    assert files == ["src/verdiclip/alpha.py", "src/verdiclip/nested/__init__.py"]
    scoped = tomllib.loads((tmp_path / "pyproject.toml").read_text(encoding="utf-8"))
    assert scoped["tool"]["mutmut"].pop("only_mutate") == files
    assert scoped == expected
    assert restore_scope(tmp_path)
    assert (tmp_path / "pyproject.toml").read_bytes() == original
    assert not restore_scope(tmp_path)


@pytest.mark.parametrize(
    "patterns",
    [
        [],
        ["other.alpha.*"],
        ["verdiclip../alpha.*"],
        ["verdiclip.alpha.**"],
        ["verdiclip.missing.*"],
    ],
)
def test_invalid_or_empty_scope_cannot_become_entire_package(
    tmp_path: Path, patterns: list[str]
) -> None:
    """An invalid namespace never falls back to unrestricted mutation."""
    original = project(tmp_path)
    with pytest.raises(ValueError, match=r"\S"):
        scoped_files(tmp_path, patterns)
    assert (tmp_path / "pyproject.toml").read_bytes() == original


def test_restore_refuses_to_overwrite_later_configuration_edit(tmp_path: Path) -> None:
    """A failed run cannot erase a configuration edit made after scope leasing."""
    original = project(tmp_path)
    apply_scope(tmp_path, ["verdiclip.alpha.*"])
    path = tmp_path / "pyproject.toml"
    changed = path.read_bytes() + b"# Later edit must survive\n"
    path.write_bytes(changed)
    with pytest.raises(ValueError, match="Configuration changed"):
        restore_scope(tmp_path)
    assert path.read_bytes() == changed
    assert (
        tmp_path / ".test-tmp/mutation-scope/original.toml"
    ).read_bytes() == original


def test_existing_lease_or_scope_policy_requires_explicit_recovery(
    tmp_path: Path,
) -> None:
    """Preexisting scope or recovery state cannot be silently replaced."""
    project(tmp_path)
    apply_scope(tmp_path, ["verdiclip.alpha.*"])
    with pytest.raises(ValueError, match=r"\S"):
        apply_scope(tmp_path, ["verdiclip.alpha.*"])
    assert restore_scope(tmp_path)
    path = tmp_path / "pyproject.toml"
    path.write_bytes(path.read_bytes() + b'only_mutate = ["src/verdiclip/alpha.py"]\n')
    with pytest.raises(ValueError, match="existing only_mutate"):
        apply_scope(tmp_path, ["verdiclip.alpha.*"])


def test_wrong_table_layout_is_rejected_before_modification(tmp_path: Path) -> None:
    """A future TOML layout change fails closed instead of editing another tool."""
    original = project(tmp_path) + b"[tool.other]\nvalue = 1\n"
    (tmp_path / "pyproject.toml").write_bytes(original)
    with pytest.raises(ValueError, match="final table"):
        apply_scope(tmp_path, ["verdiclip.alpha.*"])
    assert (tmp_path / "pyproject.toml").read_bytes() == original


def test_legacy_migration_requires_every_approved_runtime_input(tmp_path: Path) -> None:
    """Any unqualified setup change disables the historical cache fallback."""
    files = {}
    for name in MIGRATION_INPUTS:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"Approved configuration\n")
        files[name] = digest(path.read_bytes())
    key = "mutmut-v1-" + "a" * 64 + "-"
    approval = tmp_path / "scripts/mutation-cache-compatibility.json"
    approval.write_text(
        json.dumps({"files": files, "restore_key": key}), encoding="utf-8"
    )
    assert legacy_cache_key(tmp_path) == key
    for name in MIGRATION_INPUTS:
        path = tmp_path / name
        path.write_bytes(b"Changed execution input\n")
        assert legacy_cache_key(tmp_path) == ""
        path.write_bytes(b"Approved configuration\n")
    files["../outside"] = "ignored"
    approval.write_text(
        json.dumps({"files": files, "restore_key": key}), encoding="utf-8"
    )
    assert legacy_cache_key(tmp_path) == ""
