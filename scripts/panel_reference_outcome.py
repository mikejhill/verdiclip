"""Retain independent owned-child and supervisor failure evidence; never admit."""

from __future__ import annotations

import json
from itertools import islice
from pathlib import Path
from typing import Any

from scripts.panel_feasibility import write

MAX_READINESS_BYTES = 2_000_000
MAX_OBSERVATIONS = 16
MAX_FAILURE_LENGTH = 8192
MAX_CLEANUP_ERRORS = 16
KNOWN_COMPLETED = ("reference.final_guard", "reference.source.close")


def child_failure(
    path: Path, root: list[int], exit_code: int | None
) -> tuple[str | None, str]:
    """Use complete bounded owned readiness only after process exit."""
    if exit_code is None:
        return None, "child still running; readiness not finalized"
    try:
        if path.stat().st_size > MAX_READINESS_BYTES:
            return None, "readiness exceeds byte bound"
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as error:
        return None, f"readiness unavailable or malformed: {type(error).__name__}"
    return validated_child_failure(value, root)


def validated_child_failure(value: object, root: list[int]) -> tuple[str | None, str]:
    """Reject malformed/schema-incomplete diagnostics instead of trusting text."""
    if not isinstance(value, dict) or value.get("root_rect") != root:
        return None, "readiness root/schema differs"
    if (
        not isinstance(value.get("observations"), list)
        or len(value["observations"]) > MAX_OBSERVATIONS
        or "first_failure" not in value
    ):
        return None, "readiness observation/schema differs"
    failure = value["first_failure"]
    if failure is None:
        return None, "finalized readiness reports no child failure"
    if not isinstance(failure, str) or not 0 < len(failure) <= MAX_FAILURE_LENGTH:
        return None, "readiness failure field invalid"
    return failure, "finalized owned readiness failure retained"


def retain_failure(
    output: Path,
    manifest: dict[str, Any],
    records: list[dict[str, Any]],
    exit_code: int | None,
    supervisor_error: str | None,
) -> str:
    """Record all available verdicts and select an informative primary error."""
    original, readiness_status = child_failure(
        output / "reference/readiness.json", manifest["allowed_rect"], exit_code
    )
    completed = {
        r.get("operation")
        for r in records
        if r.get("event") == "call_end" and r.get("operation") in KNOWN_COMPLETED
    }
    cleanup_errors = list(
        islice(
            (
                r["operation"][:MAX_FAILURE_LENGTH]
                if isinstance(r.get("operation"), str)
                else "invalid cleanup error detail"
                for r in records
                if r.get("event") == "cleanup_error"
            ),
            MAX_CLEANUP_ERRORS,
        )
    )
    fallback = supervisor_error or f"Reference helper failed with exit {exit_code}"
    primary = original or fallback
    try:
        write(
            output / "reference-outcome.json",
            {
                "primary_failure": primary,
                "owned_readiness_failure": original,
                "readiness_status": readiness_status,
                "exit_code": exit_code,
                "supervisor_failure": supervisor_error,
                "final_guard_end_recorded": "reference.final_guard" in completed,
                "source_close_end_recorded": "reference.source.close" in completed,
                "cleanup_errors": cleanup_errors,
                "success_admitted": False,
            },
        )
    except (OSError, ValueError, TypeError, RecursionError) as error:
        return (
            f"{primary}; supervisor: {fallback}; "
            f"outcome retention failed: {type(error).__name__}"
        )
    return primary
