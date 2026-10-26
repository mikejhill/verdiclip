"""Guard locked mutmut's ordinal cache with actual generated candidate identities.

No dependency code is patched. A narrow, version-checked preparation adapter
calls the same generator used by the CLI, without running any mutants. Original
copies are restored for the real CLI's independent coverage collection. A final
inventory mismatch is a failure and prevents both reporting and cache saving.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import json
import math
import os
import re
import shutil
import sys
from importlib.metadata import version
from pathlib import Path
from typing import Any

SUPPORTED_MUTMUT = "3.8.0"
CANDIDATE = re.compile(r"__mutmut_\d+$")
REUSABLE_VERDICTS = frozenset({0, 1, 3, 36, 37, -24, 24, 152, 255, -11, -9})


def candidate_identities(source: str) -> dict[str, str]:
    """Keep every AST semantic field, excluding only source-location attributes."""
    identities: dict[str, str] = {}
    for node in ast.walk(ast.parse(source, type_comments=True)):
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef)
        ) and CANDIDATE.search(node.name):
            if node.name in identities:
                raise ValueError("Ambiguous generated candidate identity")
            identities[node.name] = hashlib.sha256(
                ast.dump(node, include_attributes=False).encode()
            ).hexdigest()
    return identities


def reconcile(
    old: dict[str, Any],
    fresh: dict[str, Any],
    before: dict[str, str],
    after: dict[str, str],
) -> tuple[dict[str, Any], int]:
    """Unknown bodies, changed originals and shifted same-name bodies are fresh."""
    reused = 0
    for key in fresh["exit_code_by_key"]:
        leaf = key.rsplit(".", 1)[-1]
        function = leaf.rsplit("__mutmut_", 1)[0]
        verdict = old.get("exit_code_by_key", {}).get(key)
        fresh["exit_code_by_key"][key] = None
        same_original = (
            function in fresh.get("hash_by_function_name", {})
            and old.get("hash_by_function_name", {}).get(function)
            == fresh["hash_by_function_name"][function]
        )
        if (
            type(verdict) is int
            and verdict in REUSABLE_VERDICTS
            and same_original
            and leaf in before
            and before[leaf] == after.get(leaf)
        ):
            fresh["exit_code_by_key"][key] = verdict
            for field in (
                "durations_by_key",
                "estimated_durations_by_key",
                "type_check_error_by_key",
            ):
                value = old.get(field, {}).get(key)
                if field == "type_check_error_by_key":
                    if value is None or isinstance(value, str):
                        fresh[field][key] = value
                elif (
                    type(value) in (int, float) and math.isfinite(value) and value >= 0
                ):
                    fresh[field][key] = value
            reused += 1
    return fresh, reused


def _cache(root: Path) -> Path:
    path = root / "mutants"
    if path.is_symlink() or path.resolve().parent != root.resolve():
        raise ValueError("Mutation staging must be a direct, unlinked checkout child")
    return path


def _remove_staging(root: Path) -> None:
    """Delete only checked generated staging, never source or retained receipts."""
    path = _cache(root)
    if path.exists():
        shutil.rmtree(path)
    path.mkdir()


def _inventory(cache: Path, *, strict: bool = True) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for path in cache.rglob("*.py"):
        if not path.resolve().is_relative_to(cache.resolve()):
            if strict:
                raise ValueError("Generated source escaped mutation staging")
            continue
        try:
            identities = candidate_identities(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeError, ValueError):
            if strict:
                raise
            continue
        if identities:
            result[path.relative_to(cache).as_posix()] = identities
    return result


def _metadata(cache: Path, *, strict: bool = True) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in cache.rglob("*.py.meta"):
        if not path.resolve().is_relative_to(cache.resolve()):
            if strict:
                raise ValueError("Cached metadata escaped mutation staging")
            continue
        try:
            value = _decode_metadata(path.read_text(encoding="utf-8"))
            result[path.relative_to(cache).as_posix()] = value
        except (OSError, UnicodeError, ValueError, TypeError):
            if strict:
                raise
    return result


def _decode_metadata(text: str) -> dict[str, Any]:
    value = json.loads(text)
    if not isinstance(value, dict) or not all(
        isinstance(value.get(k), dict)
        for k in (
            "exit_code_by_key",
            "hash_by_function_name",
            "durations_by_key",
            "estimated_durations_by_key",
            "type_check_error_by_key",
        )
    ):
        raise TypeError("Malformed mutation metadata")
    return value


def trusted_metadata(
    cache: Path,
    metadata: dict[str, dict[str, Any]],
    inventory: dict[str, dict[str, str]],
) -> dict[str, dict[str, Any]]:
    """Legacy body/meta co-location cannot establish historical verdict identity."""
    if (
        not (cache / ".identity-provenance.json")
        .resolve()
        .is_relative_to(cache.resolve())
    ):
        return {}
    try:
        marker = json.loads(
            (cache / ".identity-provenance.json").read_text(encoding="utf-8")
        )
        if (
            type(marker.get("format")) is not int
            or marker["format"] != 1
            or marker.get("tool") != SUPPORTED_MUTMUT
        ):
            return {}
        hashes = marker["metadata_sha256"]
        bodies = marker["identities"]
        if not isinstance(hashes, dict) or not isinstance(bodies, dict):
            return {}
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError):
        return {}
    result = {}
    for name, value in metadata.items():
        source = name.removesuffix(".meta")
        try:
            digest = hashlib.sha256((cache / name).read_bytes()).hexdigest()
        except OSError:
            continue
        if (
            source in inventory
            and inventory[source] == bodies.get(source)
            and hashes.get(name) == digest
        ):
            result[name] = value
    return result


def check_runtime() -> None:
    """Reject unsupported adapters before changing any generated staging."""
    if os.name != "posix" or version("mutmut") != SUPPORTED_MUTMUT:
        raise ValueError("Identity preparation requires POSIX and locked mutmut3.8.0")


def _generate(children: int) -> None:
    """Use the locked tool's unchanged generator; no hooks or monkeypatches."""
    check_runtime()
    api = importlib.import_module("mutmut.__main__")
    for name in (
        "set_mutant_under_test",
        "copy_src_dir",
        "copy_also_copy_files",
        "setup_source_paths",
        "store_lines_covered_by_tests",
        "create_mutants",
    ):
        if not callable(getattr(api, name, None)):
            raise TypeError("Locked mutation generator API changed; do not reuse cache")
    api.set_mutant_under_test("mutant_generation")
    api.copy_src_dir()
    api.copy_also_copy_files()
    api.setup_source_paths()
    api.store_lines_covered_by_tests()
    api.create_mutants(children)


def prepare(root: Path, children: int) -> dict[str, Any]:
    """Quarantine prior metadata, reconcile bodies, leave only qualified metadata."""
    if children < 1:
        raise ValueError("Mutation generator needs at least one worker")
    root = root.resolve()
    if root != Path.cwd().resolve():
        raise ValueError("Identity preparation must run inside its selected checkout")
    check_runtime()
    cache = _cache(root)
    state = root / ".test-tmp" / "mutation-identity"
    if not state.resolve().is_relative_to(root):
        raise ValueError("Identity receipts must remain inside the checkout")
    if state.exists():
        raise ValueError("Identity lease already exists; retain it for diagnosis")
    state.mkdir(parents=True)
    old_meta, old_inventory = (
        _metadata(cache, strict=False),
        _inventory(cache, strict=False),
    )
    trusted = trusted_metadata(cache, old_meta, old_inventory)
    (state / "cached.json").write_text(
        json.dumps({"metadata": old_meta, "identities": old_inventory}),
        encoding="utf-8",
    )
    # Retain the whole old staging tree, including unexpected local files. Only
    # freshly generated copies are deleted after the preparation pass.
    if cache.exists():
        shutil.move(str(cache), str(state / "quarantine"))
    cache.mkdir()
    _generate(children)
    expected, fresh_meta = _inventory(cache), _metadata(cache)
    reused = 0
    for name, fresh in fresh_meta.items():
        source = name.removesuffix(".meta")
        fresh_meta[name], count = reconcile(
            trusted.get(name, {}),
            fresh,
            old_inventory.get(source, {}),
            expected.get(source, {}),
        )
        reused += count
    originals = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in expected
    }
    receipt = {
        "tool": SUPPORTED_MUTMUT,
        "candidate_files": len(expected),
        "candidates": sum(map(len, expected.values())),
        "reused": reused,
        "untrusted_legacy_files": len(old_meta) - len(trusted),
        "historical_verdicts_not_freshly_requalified": reused > 0,
        "prepared": True,
        "verified": False,
    }
    (state / "expected.json").write_text(
        json.dumps({"identities": expected, "originals": originals}), encoding="utf-8"
    )
    # The actual CLI MUST collect coverage from fresh original copies, not this
    # preflight's trampolines. Retain no copied scripts/resources/tests either.
    _remove_staging(root)
    for name, metadata in fresh_meta.items():
        target = cache / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(metadata), encoding="utf-8")
    (state / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return receipt


def verify(root: Path) -> dict[str, Any]:
    """Fail closed on generation drift before any verdict report/cache upload."""
    root = root.resolve()
    cache = _cache(root)
    state = root / ".test-tmp" / "mutation-identity"
    expected = json.loads((state / "expected.json").read_text(encoding="utf-8"))
    receipt = json.loads((state / "receipt.json").read_text(encoding="utf-8"))
    actual = _inventory(cache)
    unchanged = all(
        (root / name).is_file()
        and hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
        for name, digest in expected["originals"].items()
    )
    valid = actual == expected["identities"] and unchanged
    receipt.update({"verified": valid, "originals_unchanged": unchanged})
    (state / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    if not valid:
        # Never let a later workflow step save ordinal-mismatched metadata.
        for path in cache.rglob("*.py.meta"):
            if not path.resolve().is_relative_to(cache.resolve()):
                raise ValueError("Unverified metadata escaped staging")
            path.unlink()
        raise ValueError("Candidate identities drifted; report/cache saving refused")
    # Bind metadata bytes to the actual final candidate vector. A cache with no
    # marker (or a changed marker/body/meta) is cold on the next preparation.
    metadata = _metadata(cache)
    marker = {
        "format": 1,
        "tool": SUPPORTED_MUTMUT,
        "identities": actual,
        "metadata_sha256": {
            name: hashlib.sha256((cache / name).read_bytes()).hexdigest()
            for name in metadata
        },
    }
    (cache / ".identity-provenance.json").write_text(
        json.dumps(marker), encoding="utf-8"
    )
    return receipt


def main() -> None:
    """Preparation and verification are separate from the unmodified CLI run."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify"))
    parser.add_argument("--max-children", type=int, default=1)
    args = parser.parse_args()
    result = (
        prepare(Path.cwd(), args.max_children)
        if args.action == "prepare"
        else verify(Path.cwd())
    )
    sys.stdout.write(json.dumps(result) + "\n")


if __name__ == "__main__":
    main()
