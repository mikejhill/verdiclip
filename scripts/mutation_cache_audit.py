"""Read cached mutation data without executing it or changing trust decisions."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from scripts.mutation_identity import SUPPORTED_MUTMUT, candidate_identities

MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_FILES = 512


def read_owned(root: Path, path: Path) -> bytes:
    """Refuse links, escapes and oversized inputs before reading cache data."""
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Unsafe cached path")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("Oversized cached file")
    return path.read_bytes()


def seconds(value: object) -> float | None:
    """Do not let malformed measurements distort runtime summaries."""
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    ):
        return float(value)
    return None


def measured_durations(
    durations: dict[str, Any], verdicts: dict[str, Any]
) -> tuple[list[float], list[dict[str, Any]]]:
    """Collect only finite timings belonging to completed verdicts."""
    measured = []
    entries = []
    for key, value in durations.items():
        duration = seconds(value)
        if duration is not None and key in verdicts and verdicts[key] is not None:
            measured.append(duration)
            entries.append({"candidate": key, "seconds": duration})
    return measured, entries


def audit(cache: Path) -> dict[str, Any]:
    """Explain exact per-file rejection predicates and retain bounded timings."""
    if cache.is_symlink() or not cache.is_dir():
        raise ValueError("Expected an unlinked cache directory")
    marker = json.loads(read_owned(cache, cache / ".identity-provenance.json"))
    if type(marker.get("format")) is not int or marker["format"] != 1:
        raise ValueError("Unsupported provenance format")
    if marker.get("tool") != SUPPORTED_MUTMUT:
        raise ValueError("Unsupported mutation tool")
    bodies, hashes = marker["identities"], marker["metadata_sha256"]
    if not isinstance(bodies, dict) or not isinstance(hashes, dict):
        raise TypeError("Malformed provenance maps")
    paths = sorted(cache.rglob("*.py.meta"))
    if len(paths) > MAX_FILES:
        raise ValueError("Excessive metadata files")
    files = []
    slowest = []
    for path in paths:
        name = path.relative_to(cache).as_posix()
        source = name.removesuffix(".meta")
        raw = read_owned(cache, path)
        metadata = json.loads(raw)
        identities = candidate_identities(read_owned(cache, cache / source).decode())
        reasons = []
        if not identities:
            reasons.append("no_generated_candidates")
        if identities != bodies.get(source):
            reasons.append("candidate_inventory_mismatch")
        actual_hash = hashlib.sha256(raw).hexdigest()
        if hashes.get(name) != actual_hash:
            reasons.append("metadata_digest_mismatch")
        verdicts = metadata["exit_code_by_key"]
        durations = metadata["durations_by_key"]
        estimates = metadata["estimated_durations_by_key"]
        completed = sum(value is not None for value in verdicts.values())
        measured, entries = measured_durations(durations, verdicts)
        slowest.extend(entries)
        pending_estimates = [
            duration
            for key, value in verdicts.items()
            if value is None and (duration := seconds(estimates.get(key))) is not None
        ]
        files.append(
            {
                "metadata": name,
                "reasons": reasons,
                "generated_candidates": len(identities),
                "verdict_entries": len(verdicts),
                "completed": completed,
                "unchecked": len(verdicts) - completed,
                "expected_sha256": hashes.get(name),
                "actual_sha256": actual_hash,
                "measured_count": len(measured),
                "measured_seconds_sum": sum(measured),
                "unchecked_estimate_count": len(pending_estimates),
                "unchecked_estimated_seconds_sum": sum(pending_estimates),
            }
        )
    return {
        "schema": 1,
        "context": json.loads(read_owned(cache, cache / ".verdict-context.json")),
        "metadata_files": len(files),
        "rejected_files": sum(bool(item["reasons"]) for item in files),
        "files": files,
        "slowest_completed": sorted(
            slowest, key=lambda item: item["seconds"], reverse=True
        )[:40],
        "timing_limit": (
            "Summed candidate durations are not parallel job wall time; "
            "missing estimates remain unknown."
        ),
    }


def main() -> None:
    """Write one data-only receipt; never import cached Python or load pickle."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cache", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(json.dumps(audit(args.cache), indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
