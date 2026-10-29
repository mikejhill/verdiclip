"""Scope incremental mutation runs without forcing cached verdicts to rerun."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tomllib
from pathlib import Path, PurePosixPath
from typing import Final

MIGRATION_INPUTS: Final = frozenset(
    {
        "uv.lock",
        "pyproject.toml",
        "tests/mutation_support.py",
        ".github/workflows/mutation.yml",
        "scripts/mutation_ci.py",
    }
)


def digest(data: bytes) -> str:
    """Compare checkout content independently of Git's line-ending conversion."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def scoped_files(root: Path, patterns: list[str]) -> list[str]:
    """Translate the workflow's existing module patterns into exact source files."""
    files: set[str] = set()
    for pattern in patterns:
        if pattern.endswith(".x*"):
            parts = [*pattern[:-3].split("."), "__init__"]
        elif pattern.endswith(".*"):
            parts = pattern[:-2].split(".")
        else:
            raise ValueError("Mutation scope requires generated module patterns")
        if parts[0] != "verdiclip" or not all(part.isidentifier() for part in parts):
            raise ValueError("Mutation scope must name a VerdiClip Python module")
        relative = PurePosixPath("src", *parts).with_suffix(".py")
        path = root.joinpath(*relative.parts)
        if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(
                f"Mutation source is missing or outside checkout: {relative}"
            )
        files.add(str(relative))
    if not files:
        raise ValueError("Empty file scope must not silently mean the entire package")
    return sorted(files)


def apply_scope(root: Path, patterns: list[str]) -> list[str]:
    """Lease only_mutate; keep every test, timeout and assertion policy intact."""
    project = root / "pyproject.toml"
    if not project.resolve().is_relative_to(root.resolve()):
        raise ValueError("Mutation configuration must remain inside the checkout")
    original = project.read_bytes()
    before = tomllib.loads(original.decode("utf-8"))
    if "only_mutate" in before["tool"]["mutmut"]:
        raise ValueError(
            "Review the existing only_mutate policy before changing CI scope"
        )
    files = scoped_files(root, patterns)
    candidate = (
        original
        + ("\nonly_mutate = " + json.dumps(files, ensure_ascii=False) + "\n").encode()
    )
    after = tomllib.loads(candidate.decode("utf-8"))
    if after["tool"]["mutmut"].pop("only_mutate", None) != files or after != before:
        raise ValueError(
            "Keep tool.mutmut as the final table; no other configuration may change"
        )
    state = root / ".test-tmp" / "mutation-scope"
    state.mkdir(parents=True, exist_ok=True)
    backup, lease = state / "original.toml", state / "lease.json"
    if backup.exists() or lease.exists():
        raise ValueError("An existing configuration lease needs recovery first")
    backup.write_bytes(original)
    lease.write_text(
        json.dumps(
            {
                "original": hashlib.sha256(original).hexdigest(),
                "scoped": hashlib.sha256(candidate).hexdigest(),
                "mode": stat.S_IMODE(project.stat().st_mode),
            }
        ),
        encoding="utf-8",
    )
    temporary = state / "scoped.toml"
    temporary.write_bytes(candidate)
    temporary.chmod(stat.S_IMODE(project.stat().st_mode))
    temporary.replace(project)
    return files


def restore_scope(root: Path) -> bool:
    """Restore byte-identically, refusing to overwrite a later configuration edit."""
    state = root / ".test-tmp" / "mutation-scope"
    backup, lease = state / "original.toml", state / "lease.json"
    if not backup.exists() and not lease.exists():
        return False
    info = json.loads(lease.read_text(encoding="utf-8"))
    original = backup.read_bytes()
    if hashlib.sha256(original).hexdigest() != info["original"]:
        raise ValueError(
            "Mutation configuration backup changed; retain it for recovery"
        )
    project = root / "pyproject.toml"
    if not project.resolve().is_relative_to(root.resolve()):
        raise ValueError(
            "Mutation configuration moved outside checkout; backup retained"
        )
    current = hashlib.sha256(project.read_bytes()).hexdigest()
    if current not in (info["original"], info["scoped"]):
        raise ValueError(
            "Configuration changed during mutation; original backup retained"
        )
    if current != info["original"]:
        temporary = state / "restored.toml"
        temporary.write_bytes(original)
        temporary.chmod(info["mode"])
        temporary.replace(project)
    backup.unlink()
    lease.unlink()
    return True


def legacy_cache_key(root: Path) -> str:
    """Allow the one-time compatible cache migration only for approved input bytes."""
    try:
        approval = json.loads(
            (root / "scripts/mutation-cache-compatibility.json").read_text(
                encoding="utf-8"
            )
        )
        files = approval["files"]
        key = approval["restore_key"]
        if (
            not isinstance(files, dict)
            or not isinstance(key, str)
            or set(files) != MIGRATION_INPUTS
            or not re.fullmatch(r"mutmut-v1-[0-9a-f]{64}-", key)
        ):
            return ""
        if any(
            not (root / name).resolve().is_relative_to(root.resolve())
            or digest((root / name).read_bytes()) != expected
            for name, expected in files.items()
        ):
            return ""
    except (OSError, ValueError, KeyError, TypeError):
        return ""
    return key


def main() -> None:
    """Use public TOML scope; mutmut run must receive no explicit mutant names."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("configure", "restore", "legacy-key", "record")
    )
    parser.add_argument("patterns", nargs="*")
    args = parser.parse_args()
    root = Path.cwd()
    if args.action == "configure":
        output = json.dumps({"only_mutate": apply_scope(root, args.patterns)})
    elif args.action == "restore":
        output = json.dumps({"restored": restore_scope(root)})
    elif args.action == "record":
        outcome = os.environ.get("MUTATION_OUTCOME", "not_run")
        output = json.dumps(
            {
                "run_completed": outcome == "success",
                "outcome": outcome,
                "source_sha": os.environ.get("SOURCE_SHA", "unknown"),
                "scope": os.environ.get("PATTERNS", "").split(),
            }
        )
    else:
        output = legacy_cache_key(root)
    sys.stdout.write(output + "\n")


if __name__ == "__main__":
    main()
