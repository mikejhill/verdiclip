"""Fail-closed identity checks for the opt-in source-based physical harness."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

PRODUCTION_BASE = "a0869f762f8fb47009a04c76ec3ab174817db6c1"
HARNESS_FILES = frozenset(
    {
        "scripts/test_capture_broker_native.ps1",
        "scripts/physical_scrollbar_qualification.py",
        "tests/conftest.py",
        "tests/capture/test_broker_native.py",
        "tests/capture/native_scrollbar.py",
        "tests/capture/physical_scrollbar.py",
        "tests/capture/test_physical_scrollbar_qualification.py",
        "docs/design/capture-input-validation.md",
    }
)


def git(root: Path, *arguments: str) -> str:
    """Use read-only Git queries, with no shell or inherited user Git hooks."""
    return subprocess.run(  # noqa: S603 -- fixed read-only subcommands below
        ["git", "-C", str(root), *arguments],  # noqa: S607 -- Git is a prerequisite
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()


@dataclass(frozen=True)
class SourceProvenance:
    """Two immutable source identities, explicitly not installed-wheel evidence."""

    source_head: str
    harness_head: str
    harness_root: str
    package_path: str
    source_dirty: bool = False
    harness_dirty: bool = False
    execution_kind: str = "verified-source-checkout-not-installed-wheel"

    def receipt(self) -> dict[str, object]:
        """Serialize the checked paths and identities, never inferred version labels."""
        return cast("dict[str, object]", asdict(self))


def unreviewed_import(name: str) -> bool:
    """Reject ignored code reachable from pytest's root as well as owned subtrees."""
    path = Path(name)
    first = path.parts[0].casefold()
    if first in {"src", "scripts", "tests"}:
        return True
    # Dot-prefixed evidence/cache/runtime directories are not Python package names.
    return not first.startswith(".") and (
        path.suffix.casefold() in {".py", ".pyc", ".pyd", ".so", ".zip"}
        or ".so." in path.name.casefold()
    )


def require_clean_head(root: Path, expected: str) -> None:
    """Refuse a dirty, moved or indirectly selected worktree before native setup."""
    if Path(git(root, "rev-parse", "--show-toplevel")).resolve() != root:
        raise ValueError("Qualification requires the exact worktree root")
    if git(root, "rev-parse", "HEAD") != expected:
        raise ValueError("Qualification checkout head does not match the pinned head")
    if any(line[:1] != "H" for line in git(root, "ls-files", "-v").splitlines()):
        raise ValueError("Qualification rejects hidden index entries")
    if git(root, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Qualification requires a clean worktree")
    ignored = git(root, "ls-files", "--others", "--ignored", "--exclude-standard")
    if any(unreviewed_import(name) for name in ignored.splitlines()):
        raise ValueError("Qualification import roots contain ignored/unreviewed files")
    for name in git(root, "ls-files", "--", "src", "scripts", "tests").splitlines():
        path = root / name
        if path.resolve() != path:
            raise ValueError("Qualification import roots cannot contain symbolic links")


def verify_provenance(*, harness_root: Path, harness_head: str) -> SourceProvenance:
    """Pin one clean harness with production/dependencies identical to the base."""
    if re.fullmatch(r"[0-9a-f]{40}", harness_head) is None:
        raise ValueError("Pass the full reviewed harness commit")
    if harness_head == PRODUCTION_BASE:
        raise ValueError("The diagnostic harness head must be distinct from production")
    harness_root = harness_root.resolve()
    require_clean_head(harness_root, harness_head)
    if (
        git(harness_root, "merge-base", PRODUCTION_BASE, harness_head)
        != PRODUCTION_BASE
    ):
        raise ValueError("Harness must descend from the verified production base")
    changed = set(
        git(harness_root, "diff", "--name-only", PRODUCTION_BASE, "HEAD").splitlines()
    )
    if not changed or not changed <= HARNESS_FILES:
        raise ValueError("Harness changes exceed the reviewed test-only file scope")
    package = harness_root / "src" / "verdiclip" / "__init__.py"
    if not package.is_file():
        raise ValueError("Pinned product package is missing")
    return SourceProvenance(
        PRODUCTION_BASE, harness_head, str(harness_root), str(package)
    )


def require_product_imports(harness_root: Path) -> None:
    """Verify every already-loaded product module against the pinned source root."""
    package = harness_root.resolve() / "src" / "verdiclip"
    for name, module in tuple(sys.modules.items()):
        if name == "verdiclip" or name.startswith("verdiclip."):
            location = getattr(module, "__file__", None)
            if location is None or not Path(location).resolve().is_relative_to(package):
                raise ValueError("Imported product is not the pinned production source")
    module = sys.modules.get("verdiclip")
    location = getattr(module, "__file__", None)
    if location is None or Path(location).resolve() != package / "__init__.py":
        raise ValueError("Imported product package does not match the pinned source")


def main() -> int:
    """Verify before importing pytest, Qt or product; generate no input or UI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("harness_head")
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not sys.flags.isolated or not sys.flags.dont_write_bytecode:
        raise ValueError("Physical bootstrap requires Python -I -B")
    root = Path(__file__).resolve().parents[1]
    provenance = verify_provenance(harness_root=root, harness_head=args.harness_head)
    sys.path[:0] = [str(root), str(root / "src")]
    # Native helper children inherit only this verified product path.
    os.environ["PYTHONPATH"] = str(root / "src")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.stdout.write(json.dumps(provenance.receipt()) + "\n")
    import pytest  # noqa: PLC0415 -- deliberately after immutable-source admission

    return pytest.main(args.pytest_args)


if __name__ == "__main__":
    raise SystemExit(main())
