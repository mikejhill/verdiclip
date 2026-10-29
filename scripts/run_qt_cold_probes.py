"""Bound fresh-process Qt diagnostics and retain incomplete evidence explicitly."""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

from scripts.qt_cold_probe import MODES

TIMEOUT_SECONDS = 30
OUTPUT_LIMIT = 65_536
COMPARISON_FIELDS = (
    "source_sha",
    "run_id",
    "run_attempt",
    "python",
    "qt",
    "qpa",
    "style",
    "configured_font_directory",
    "matching_direct_font_files",
    "matching_direct_font_bytes",
    "image_os",
    "image_version",
    "windows_build",
    "runner_os",
    "runner_arch",
)


def compare_contexts(
    product: dict[str, object], stock: dict[str, object]
) -> dict[str, object]:
    """Missing or mismatched provenance makes timing comparison incomparable."""
    differences = [
        key
        for key in COMPARISON_FIELDS
        if product.get(key) is None
        or product.get(key) == ""
        or stock.get(key) != product.get(key)
    ]
    return {
        "comparable_environment": not differences,
        "missing_or_different": differences,
        "limit": "Matched environment does not establish cold OS caches or causality",
    }


def _bounded_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value[:OUTPUT_LIMIT].decode("utf-8", errors="replace")
    return value[:OUTPUT_LIMIT]


def _validate_phase(phase: object, name: str) -> None:
    if not isinstance(phase, dict) or phase.get("name") != name:
        raise ValueError("Wrong phase order")
    if phase.get("completed") is not True:
        raise ValueError("Incomplete measured phase")
    for clock in ("wall_ms", "process_cpu_ms", "thread_cpu_ms"):
        value = phase.get(clock)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise ValueError("Invalid phase clock")


def validate_report(
    raw: str,
    mode: str,
    expected_qt: str | None = None,
    expected_qpa: str | None = None,
) -> dict[str, object]:
    """Reject absent, malformed, mismatched or incomplete child evidence."""
    if len(raw) > OUTPUT_LIMIT:
        raise ValueError("Oversized probe output")
    report = json.loads(raw)
    if not isinstance(report, dict):
        raise TypeError("Probe report must be an object")
    metadata = report.get("metadata")
    phases = report.get("phases")
    if not isinstance(metadata, dict) or metadata.get("mode") != mode:
        raise ValueError("Wrong probe identity")
    if metadata.get("process_isolated") is not True:
        raise ValueError("Missing process boundary")
    for key, expected_value in (("qt", expected_qt), ("qpa", expected_qpa)):
        if expected_value is not None and metadata.get(key) != expected_value:
            raise ValueError(f"Wrong measured {key} profile")
    expected = ("application-construction", mode)
    if not isinstance(phases, list) or len(phases) != len(expected):
        raise ValueError("Missing unique measured phases")
    for phase, name in zip(phases, expected, strict=True):
        _validate_phase(phase, name)
    return cast("dict[str, object]", report)


def run_all(
    directory: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    *,
    expected_qt: str | None = None,
    expected_qpa: str | None = None,
    modes: tuple[str, ...] = MODES,
) -> bool:
    """One child per primitive; expiry/nonzero/invalid output never passes."""
    directory.mkdir(parents=True, exist_ok=True)
    complete = True
    for mode in modes:
        receipt: dict[str, object] = {"schema": 1, "mode": mode, "complete": False}
        try:
            child = runner(
                [sys.executable, "-m", "scripts.qt_cold_probe", mode],
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
                check=False,
            )
            receipt["returncode"] = child.returncode
            receipt["stdout"] = _bounded_output(child.stdout)
            receipt["stderr"] = _bounded_output(child.stderr)
            if child.returncode != 0:
                receipt["status"] = "execution-failure"
            else:
                receipt["report"] = validate_report(
                    child.stdout, mode, expected_qt, expected_qpa
                )
                receipt.update(status="complete", complete=True)
        except subprocess.TimeoutExpired as error:
            # subprocess.run kills and waits for its direct child on expiry.
            # No input, hooks, subprocesses or visible windows are created there.
            receipt.update(status="timeout", deadline_seconds=TIMEOUT_SECONDS)
            receipt["stdout"] = _bounded_output(error.stdout)
            receipt["stderr"] = _bounded_output(error.stderr)
        except (ValueError, TypeError, OSError) as error:
            receipt.update(status="incomplete", error=str(error))
        complete = complete and receipt["complete"] is True
        (directory / f"{mode}.json").write_text(
            json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
    return complete


def main(argv: list[str] | None = None) -> None:
    """Refuse accidental local use before launching any child or importing Qt."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=MODES)
    args = parser.parse_args(argv)
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_OS") != "Windows"
    ):
        raise SystemExit("This diagnostic is restricted to hosted Windows CI.")
    expected_qt = os.environ.get("QT_VERSION")
    if expected_qt is None:
        raise SystemExit("Missing pinned QT_VERSION for constituent probes.")
    if not run_all(
        args.output,
        expected_qt=expected_qt,
        expected_qpa=os.environ.get("QT_QPA_PLATFORM", "offscreen"),
        modes=(args.mode,) if args.mode is not None else MODES,
    ):
        raise SystemExit(
            "One or more constituent probes are incomplete; inspect receipts."
        )


if __name__ == "__main__":
    main()
