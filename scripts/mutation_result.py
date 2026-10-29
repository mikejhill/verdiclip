"""Emit bounded structured default-branch mutation evidence, never a PR gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Final, cast

from scripts.mutation_report import DETECTED, UNDETECTED, MutationReport

POLICY: Final = "verdiclip-mutmut-3.8-full-v1"
MAX_EVIDENCE: Final = 8 * 1024 * 1024
MAX_PERCENT: Final = 100


def read_evidence(path: Path) -> str:
    """Bound metadata and verdict reads before decoding them."""
    if path.is_symlink() or path.stat().st_size > MAX_EVIDENCE:
        raise ValueError("Unsafe or oversized mutation metadata")
    return path.read_text(encoding="utf-8")


def read_object(path: Path) -> dict[str, object]:
    """Read small JSON metadata; missing/malformed evidence remains a failure."""
    value = json.loads(read_evidence(path))
    if not isinstance(value, dict):
        raise TypeError("Mutation metadata must be an object")
    return cast("dict[str, object]", value)


def result(root: Path, environment: dict[str, str]) -> dict[str, object]:
    """Require CLI, baseline, identity and complete verdict accounting to qualify."""
    floor = float(environment.get("MUTATION_FLOOR", "72"))
    if (
        not math.isfinite(floor)
        or not 0 <= floor <= MAX_PERCENT
        or round(floor, 6) != floor
    ):
        raise ValueError("Invalid mutation threshold")
    source = environment["GITHUB_SHA"]
    if not re.fullmatch("[0-9a-f]{40}", source):
        raise ValueError("Invalid source revision")
    report: dict[str, object] = {
        "schema": 1,
        "repository": "mikejhill/verdiclip",
        "source_sha": source,
        "run_id": int(environment["GITHUB_RUN_ID"]),
        "run_attempt": int(environment["GITHUB_RUN_ATTEMPT"]),
        "policy": POLICY,
        "scope": "full",
        "threshold": floor,
        "status": "incomplete",
        "complete": False,
        "fresh": False,
        "reason": "Required mutation evidence did not complete",
        "counts": {},
        "score": None,
    }
    report["comparable_key"] = hashlib.sha256(
        f"{POLICY}\nfull\n{floor:.6f}".encode()
    ).hexdigest()
    outcomes = {
        key: environment.get(key, "not_run")
        for key in (
            "BASELINE_OUTCOME",
            "PROBE_OUTCOME",
            "MUTATION_OUTCOME",
            "IDENTITY_OUTCOME",
        )
    }
    report["outcomes"] = outcomes
    try:
        state = read_object(root / "mutation-run-state.json")
        identity = read_object(root / "mutation-identity-receipt.json")
        parsed = MutationReport.parse(read_evidence(root / "mutation-results.txt"))
        statuses = dict(parsed.statuses)
        report["counts"] = statuses
        overall = parsed.overall
        report["detected"] = overall.detected
        report["undetected"] = len(overall.undetected)
        report["total"] = overall.total
        report["score"] = overall.score if overall.total else None
        candidates, reused = identity.get("candidates"), identity.get("reused")
        complete = (
            all(value == "success" for value in outcomes.values())
            and state.get("run_completed") is True
            and state.get("source_sha") == source
            and state.get("scope") == []
            and identity.get("verified") is True
            and identity.get("originals_unchanged") is True
            and identity.get("tool") == "3.8.0"
            and type(candidates) is int
            and candidates > 0
            and candidates == overall.total
            and type(reused) is int
            and 0 <= reused <= candidates
            and set(statuses) <= DETECTED | UNDETECTED
            and sum(statuses.values()) == overall.total
        )
        if complete:
            # Cached verdicts are explicitly historical. A fresh comparable run
            # is required before survivor findings can be automatically closed.
            report["fresh"] = reused == 0
            report["historical_verdicts"] = reused
            findings = overall.score < floor
            report.update(
                complete=True,
                status="findings" if findings else "healthy",
                reason=(
                    "Mutation score is below the configured threshold"
                    if findings
                    else "Complete mutation score meets the configured threshold"
                ),
            )
    except (OSError, ValueError, TypeError, KeyError):
        report["reason"] = "Missing, malformed or inconsistent mutation evidence"
    return report


def main() -> None:
    """Write a small result artifact even when the mutation execution failed."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=Path("mutation-result.json"))
    args = parser.parse_args()
    evidence = result(args.root, dict(os.environ))
    args.output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(
        f"Asynchronous mutation status: {evidence['status']}; "
        f"complete={evidence['complete']}; fresh={evidence['fresh']}\n"
    )


if __name__ == "__main__":
    main()
