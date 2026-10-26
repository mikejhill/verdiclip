"""Run the repository's simulated tab contracts and measured scoped coverage."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import secrets
import shutil
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Final

COVERAGE_FLOOR: Final = 0.9

TESTS = (
    "tests/test_tabs_ci_harness.py",
    "tests/document/test_history.py",
    "tests/document/test_admission.py",
    "tests/document/test_jobs.py",
    "tests/editor/test_workspace.py",
    "tests/editor/test_workspace_properties.py",
    "tests/editor/test_tab_state_sequences.py",
    "tests/editor/test_tab_synthetic_placement.py",
    "tests/editor/test_tab_activation_order.py",
    "tests/editor/test_tab_accessibility.py",
    "tests/editor/test_tab_bar.py",
    "tests/editor/test_close_prompt.py",
    "tests/platform/test_tab_drag.py",
    "tests/platform/test_tab_synthetic_topology.py",
    "tests/output/test_delivery.py",
    "tests/output/test_atomic_save.py",
    "tests/shell/test_reviewed_delivery.py",
    "tests/shell/test_scroll_review_delivery.py",
    "tests/ux/test_tabs_journeys.py",
)
COVERAGE = (
    "verdiclip.document.history",
    "verdiclip.document.admission",
    "verdiclip.editor.page",
    "verdiclip.editor.jobs",
    "verdiclip.editor.tab_bar",
    "verdiclip.editor.transfer",
    "verdiclip.editor.closing",
    "verdiclip.editor.workspace",
    "verdiclip.platform.tab_drag",
    "verdiclip.output.delivery",
    "verdiclip.shell.scroll_review",
)


def write_summary(output: Path, manifest: dict[str, object], exit_code: int) -> int:
    """Expose scope and measured line/branch rates without claiming native proof."""
    rates: dict[str, float] = {}
    coverage = output / "coverage.xml"
    if coverage.is_file():
        try:
            measured = ET.parse(coverage).getroot()  # noqa: S314 - own pytest-cov output
            rates = {
                name: float(measured.attrib[name])
                for name in ("line-rate", "branch-rate")
            }
            manifest["coverage_rates"] = rates
        except (ET.ParseError, OSError, KeyError, ValueError) as error:
            manifest["coverage_error"] = str(error)
    # pytest-cov's combined percentage does not enforce branch rate separately.
    coverage_passed = bool(rates) and all(
        value >= COVERAGE_FLOOR for value in rates.values()
    )
    if exit_code == 0 and not coverage_passed:
        exit_code = 1
    manifest.update(
        status="passed" if exit_code == 0 else "failed",
        exit_code=exit_code,
        coverage_gate="lines >=90% AND branches >=90%",
    )
    summary = [
        "# Simulated repository tab contracts",
        "",
        f"Status: **{manifest['status']}**; Qt {manifest['qt']}; "
        f"Python {manifest['python']}.",
        f"Source: `{manifest['source_head']}`; dirty: `{manifest['source_dirty']}`; "
        f"replay seed: `{manifest['seed']}`.",
        "",
        "Coverage is scoped to the modules listed in environment.json; it does not",
        "measure the entire application or prove physical/native behavior.",
        "Gate: tests pass, scoped lines >=90%, and scoped branches >=90%.",
        "",
    ]
    if rates:
        summary.extend(
            [
                "| Scoped lines | Scoped branches |",
                "| --- | --- |",
                f"| {rates['line-rate']:.2%} | {rates['branch-rate']:.2%} |",
                "",
            ]
        )
    (output / "summary.md").write_text("\n".join(summary), encoding="utf-8")
    return exit_code


def prepare_output(repo: Path, requested: Path) -> Path:
    """Clear only owned reports in a checkout-local evidence directory."""
    output = (repo / requested).resolve()
    if output == repo or not output.is_relative_to(repo):
        raise ValueError("Output must be a dedicated directory inside this checkout")
    if output.relative_to(repo).parts[0] not in {".test-tmp", "artifacts"}:
        raise ValueError(
            "Output must be under .test-tmp/ or artifacts/ in this checkout"
        )
    output.mkdir(parents=True, exist_ok=True)
    # No stale rates on collection failure, recursive cleanup or report symlinks.
    targets = [
        output / name
        for name in (
            "environment.json",
            "coverage.xml",
            "junit.xml",
            "pytest.log",
            "summary.md",
        )
    ]
    if any(target.is_symlink() for target in targets):
        raise ValueError("Report paths cannot be symbolic links")
    for target in targets:
        target.unlink(missing_ok=True)
    return output


def fail_preflight(output: Path, message: str, runtime: str, python: str) -> int:
    """Replace prior green evidence with an explicit failed runtime prerequisite."""
    manifest: dict[str, object] = {
        "schema": 1,
        "phase": "preflight",
        "status": "failed",
        "exit_code": 2,
        "qt": runtime,
        "python": python,
        "source_head": None,
        "source_dirty": None,
        "seed": None,
        "failure": message,
        "native_or_physical_claim": False,
    }
    write_summary(output, manifest, 2)
    (output / "environment.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    sys.stderr.write(message + "\n")
    return 2


def main() -> int:
    """Use one command for CI/local replay, preserving failures and provenance."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path(".test-tmp/tabs-ci"))
    parser.add_argument("--expected-qt")
    parser.add_argument("--expected-python")
    parser.add_argument("--seed", type=int)
    args = parser.parse_args()
    from PySide6.QtCore import qVersion  # noqa: PLC0415 - version only, no app

    runtime = qVersion()
    python = f"{sys.version_info.major}.{sys.version_info.minor}"
    repo = Path(__file__).resolve().parents[1]
    try:
        output = prepare_output(repo, args.output_dir)
    except ValueError as error:
        parser.error(str(error))
    if args.expected_qt is not None and runtime != args.expected_qt:
        return fail_preflight(
            output,
            f"Expected Qt {args.expected_qt}, actual Qt {runtime}",
            runtime,
            python,
        )
    if args.expected_python is not None and python != args.expected_python:
        return fail_preflight(
            output,
            f"Expected Python {args.expected_python}, actual Python {python}",
            runtime,
            python,
        )
    seed = args.seed if args.seed is not None else secrets.randbits(32)
    if not 0 <= seed < 2**32:
        parser.error("Seed must fit an unsigned 32-bit integer")
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "tests.tabs_ci_support",
        "--hypothesis-profile=tabs_ci",
        f"--hypothesis-seed={seed}",
        f"--randomly-seed={seed}",
        "--strict-markers",
        "--strict-config",
        "--cov-fail-under=90",
        "--cov-report=term-missing",
        f"--cov-report=xml:{output / 'coverage.xml'}",
        f"--junitxml={output / 'junit.xml'}",
        f"--basetemp={output / ('temp-' + uuid.uuid4().hex)}",
        *[f"--cov={module}" for module in COVERAGE],
        *TESTS,
    ]
    env = os.environ | {
        "QT_QPA_PLATFORM": "offscreen",
        "PYTHONUTF8": "1",
        "PYTHONPATH": os.pathsep.join((str(repo / "src"), str(repo))),
        "COVERAGE_FILE": str(output / ".coverage"),
        "PYTEST_ADDOPTS": "",
        "PYTEST_PLUGINS": "",
    }
    git = shutil.which("git")
    if git is None:
        parser.error("Git is required to record the tested source revision")
    head = subprocess.check_output(  # noqa: S603 - fixed argv, no shell
        [git, "rev-parse", "HEAD"], cwd=repo, text=True, encoding="utf-8"
    ).strip()
    dirty = bool(
        subprocess.check_output(  # noqa: S603 - fixed argv, no shell
            [git, "status", "--porcelain"], cwd=repo, text=True, encoding="utf-8"
        ).strip()
    )
    manifest: dict[str, object] = {
        "schema": 1,
        "phase": "tests",
        "qualification": "simulated repository tab contracts",
        "native_or_physical_claim": False,
        "source_head": head,
        "source_dirty": dirty,
        "source_root": str(repo / "src"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "runner": {
            key: os.environ.get(key)
            for key in ("RUNNER_OS", "RUNNER_ARCH", "ImageOS", "ImageVersion")
        },
        "qt": runtime,
        "seed": seed,
        "dependencies": {
            name: importlib.metadata.version(name)
            for name in (
                "PySide6-Essentials",
                "shiboken6",
                "pytest",
                "pytest-qt",
                "pytest-cov",
                "pytest-randomly",
                "hypothesis",
                "mss",
            )
        },
        "qt_platform": "offscreen",
        "hypothesis_database": None,
        "coverage_modules": COVERAGE,
        "tests": TESTS,
        "command": command,
        "status": "running",
    }
    receipt = output / "environment.json"
    receipt.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    started = time.monotonic()
    try:
        result = subprocess.run(  # noqa: S603 - fixed tests/argv, no shell
            command,
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
        )
        log = result.stdout + result.stderr
        exit_code = result.returncode
    except subprocess.TimeoutExpired as error:
        partial = error.stdout
        log = (
            partial.decode("utf-8", errors="replace")
            if isinstance(partial, bytes)
            else partial or ""
        )
        partial_error = error.stderr
        log += (
            partial_error.decode("utf-8", errors="replace")
            if isinstance(partial_error, bytes)
            else partial_error or ""
        )
        log += "\nTab contracts exceeded the 180-second CI deadline."
        exit_code = 124
    (output / "pytest.log").write_text(log, encoding="utf-8")
    manifest.update(
        status="passed" if exit_code == 0 else "failed",
        exit_code=exit_code,
        elapsed_seconds=time.monotonic() - started,
    )
    exit_code = write_summary(output, manifest, exit_code)
    receipt.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    sys.stdout.write(log)
    sys.stdout.write(f"\nSimulated tab CI evidence: {output}\nSeed: {seed}\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
