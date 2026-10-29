"""Bind verdict caches to the complete commit and runner OS/architecture."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Final, cast

CONTEXT_POLICY: Final = "same-commit-os-arch-v1"
STATE: Final = Path(".test-tmp/mutation-cache-context.json")
CONTEXT_FILE: Final = ".verdict-context.json"
LIMIT: Final = 65536


def context(environment: dict[str, str]) -> dict[str, object]:
    """Reject missing execution identity rather than restoring broad caches."""
    source = environment.get("GITHUB_SHA", "")
    system = environment.get("RUNNER_OS", "")
    architecture = environment.get("RUNNER_ARCH", "")
    if not re.fullmatch(r"[0-9a-f]{40}", source) or not all(
        re.fullmatch(r"[A-Za-z0-9_]+", value) for value in (system, architecture)
    ):
        raise ValueError("Missing or invalid commit/OS/architecture verdict context")
    return {
        "schema": 1,
        "policy": CONTEXT_POLICY,
        "source_sha": source,
        "os": system,
        "arch": architecture,
    }


def prefix(environment: dict[str, str], fingerprint: str) -> str:
    """Only same-commit, same-platform executions may resume verdicts."""
    identity = context(environment)
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise ValueError("Invalid mutation configuration fingerprint")
    return (
        f"mutmut-v5-{identity['os']}-{identity['arch']}-"
        f"{identity['source_sha']}-{fingerprint}-"
    )


def matches(value: object, expected: dict[str, object]) -> bool:
    """Do not let JSON booleans impersonate a context schema number."""
    return (
        isinstance(value, dict)
        and type(value.get("schema")) is int
        and value == expected
    )


def _object(path: Path) -> dict[str, object] | None:
    if path.is_symlink():
        return None
    try:
        if path.stat().st_size > LIMIT:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return cast("dict[str, object]", value) if isinstance(value, dict) else None


def _owned(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or resolved == root:
        raise ValueError("Mutation cache path escaped the owned workspace")
    return path


def prepare(root: Path, environment: dict[str, str]) -> dict[str, object]:
    """Quarantine legacy/mismatched metadata before candidate reconciliation."""
    root = root.resolve()
    identity = context(environment)
    cache = _owned(root, root / "mutants")
    state_path = _owned(root, root / STATE)
    lease = _owned(root, root / ".test-tmp/mutation-identity")
    if lease.exists():
        raise ValueError("Previous identity lease exists; retain it for diagnosis")
    if cache.is_symlink() or (cache.exists() and not cache.is_dir()):
        raise ValueError("Unsafe mutation cache directory")
    restored = cache.exists() and matches(_object(cache / CONTEXT_FILE), identity)
    receipt: dict[str, object] = {
        "schema": 1,
        "context": identity,
        "prepared": True,
        "sealed": False,
        "restored": restored,
        "discarded": False,
    }
    if cache.exists() and not restored:
        archive = _owned(
            root, root / ".test-tmp/unqualified-mutation-cache" / uuid.uuid4().hex
        )
        archive.parent.mkdir(parents=True, exist_ok=True)
        # Both resolved paths were checked inside the exact owned workspace.
        cache.rename(archive)
        receipt.update(discarded=True, quarantine=str(archive.relative_to(root)))
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return receipt


def seal(root: Path, environment: dict[str, str]) -> dict[str, object]:
    """Mark only identity-verified verdicts as eligible for same-context resume."""
    root = root.resolve()
    expected = context(environment)
    state_path = _owned(root, root / STATE)
    receipt = _object(state_path)
    identity = _object(_owned(root, root / ".test-tmp/mutation-identity/receipt.json"))
    cache = _owned(root, root / "mutants")
    if (
        receipt is None
        or type(receipt.get("schema")) is not int
        or receipt.get("schema") != 1
        or not matches(receipt.get("context"), expected)
        or receipt.get("prepared") is not True
        or identity is None
        or identity.get("verified") is not True
        or identity.get("originals_unchanged") is not True
        or cache.is_symlink()
        or not cache.is_dir()
    ):
        raise ValueError("Unqualified verdict context cannot be sealed")
    marker = _owned(root, cache / CONTEXT_FILE)
    if marker.is_symlink():
        raise ValueError("Unsafe mutation verdict context output")
    marker.write_text(json.dumps(expected, indent=2), encoding="utf-8")
    receipt["sealed"] = True
    state_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return receipt


def main() -> None:
    """Expose fixed workflow operations without executable cache inputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prefix", "prepare", "seal"))
    args = parser.parse_args()
    environment = dict(os.environ)
    if args.action == "prefix":
        output = prefix(environment, environment.get("CACHE_FINGERPRINT", ""))
    else:
        operation = prepare if args.action == "prepare" else seal
        output = json.dumps(operation(Path.cwd(), environment))
    sys.stdout.write(output + "\n")


if __name__ == "__main__":
    main()
