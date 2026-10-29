"""Dormant hosted-only mutmut3.8 clean-test diagnosis; no Qt imports on import."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

SOURCE_SHA = "5fe14b25b651c02caea31786e44e9508595e4e19"
FIXTURE_SHA = "92c8d0cd4e1f5114a4ddf210b30bbf4e5c40f510"
SOURCE_CHOICES = (SOURCE_SHA, FIXTURE_SHA)
LOCK_SHA = "14e1d58cd3d516b6eff172dba8b92beccb93ecd7741add578af7a99adb39da78"
PINNED_FILES = {
    "__main__.py",
    "configuration.py",
    "runners/harness.py",
    "workers/isolation.py",
    "utils/file_utils.py",
    "code_coverage.py",
}
SOURCE_ROOTS = ["src/verdiclip/"]
STATS_SELECTION = ["tests/"]
PYTEST_ARGUMENTS = [
    "-p",
    "tests.mutation_support",
    "-p",
    "no:randomly",
    "-p",
    "no:cacheprovider",
]
type Operation = Callable[..., object]


class CleanApi(Protocol):
    """Only the three pinned orchestration functions temporarily observed."""

    run_stats_collection: Operation
    collect_or_load_stats: Operation
    run_forced_fail_test: Operation


class DebugConfig(Protocol):
    """The documented mutable configuration switch; not a CLI flag."""

    debug: bool


class _CleanBoundaryError(Exception):
    """Owned stop before forced-failure or actual mutant execution."""


@dataclass
class Observation:
    """Distinguish collected statistics from cached/skipped/failed attempts."""

    entered: bool = False
    completed: bool = False


def emit(value: object) -> None:
    """Write only explicit diagnostic phase metadata to normal stdout."""
    sys.stdout.write(json.dumps(value) + "\n")
    sys.stdout.flush()


def clean_tests_only(
    api: CleanApi,
    config: DebugConfig,
    invoke: Callable[[], object],
    report: Callable[[object], None] = emit,
) -> int:
    """Keep fresh statistics/clean ordering; stop before forced or mutant tests.

    The pinned _run reaches run_forced_fail_test ONLY after the real clean
    runner returned zero. Association and clean failures propagate unchanged.
    No copied prior timing/cache/identity-prepare state is claimed identical.
    """
    original_stats = api.run_stats_collection
    original_collect = api.collect_or_load_stats
    original_forced = api.run_forced_fail_test
    original_debug = config.debug
    observation = Observation()
    clean_admitted = False

    def observe_stats(*args: object, **kwargs: object) -> object:
        if observation.entered:
            raise RuntimeError("Unexpected repeated statistics entry")
        observation.entered = True
        report({"diagnostic_phase": "actual_statistics_entered"})
        result = original_stats(*args, **kwargs)
        observation.completed = True
        return result

    def collect_then_continue(*args: object, **kwargs: object) -> object:
        nonlocal clean_admitted
        result = original_collect(*args, **kwargs)
        if not observation.completed:
            raise RuntimeError("Statistics were loaded/skipped, not freshly collected")
        clean_admitted = True
        report({"diagnostic_phase": "fresh_statistics_complete_clean_admitted"})
        return result

    def stop_before_forced_fail(*_args: object, **_kwargs: object) -> object:
        if not observation.completed or not clean_admitted:
            raise RuntimeError("Forced-fail boundary reached without fresh statistics")
        raise _CleanBoundaryError

    config.debug = True
    api.run_stats_collection = observe_stats
    api.collect_or_load_stats = collect_then_continue
    api.run_forced_fail_test = stop_before_forced_fail
    try:
        report(
            {
                "diagnostic_phase": "fresh_generation_before_statistics",
                "debug": True,
                "identity_prepare_prelude_replayed": False,
                "historical_duration_order_replayed": False,
                "diagnostic_output_policy": "explicit_python_-u",
            }
        )
        invoke()
        raise RuntimeError("CLI returned without the required clean-test boundary")
    except _CleanBoundaryError:
        report(
            {
                "diagnostic_phase": "clean_completed_then_stopped",
                "forced_fail_started": False,
                "mutant_execution_started": False,
                "mutation_score": None,
                "nonreproduction_is_not_resolution": True,
            }
        )
        return 0
    finally:
        api.run_stats_collection = original_stats
        api.collect_or_load_stats = original_collect
        api.run_forced_fail_test = original_forced
        config.debug = original_debug


def validate_manifest(value: object) -> dict[str, object]:
    """Reject malformed/omitted pins before any package inspection or import."""
    if type(value) is not dict:
        raise ValueError("Manifest must be an object")
    data = cast("dict[str,object]", value)
    if set(data) != {
        "source_sha",
        "uv_lock_sha256",
        "mutmut_version",
        "mutmut_normalized_source_sha256",
    }:
        raise ValueError("Unexpected manifest schema")
    if (
        data["source_sha"] != SOURCE_SHA
        or data["uv_lock_sha256"] != LOCK_SHA
        or data["mutmut_version"] != "3.8.0"
    ):
        raise ValueError("Unreviewed source/lock/version identity")
    hashes = data["mutmut_normalized_source_sha256"]
    if type(hashes) is not dict or set(hashes) != PINNED_FILES:
        raise ValueError("Require all six exact reviewed source pins")
    if any(
        type(item) is not str or re.fullmatch("[0-9a-f]{64}", item) is None
        for item in hashes.values()
    ):
        raise ValueError("Malformed source digest")
    return data


def admit(
    root: Path, manifest: dict[str, object], source_sha: str = SOURCE_SHA
) -> Path:
    """Read-only source/version/hash checks before loading POSIX mutmut code."""
    if source_sha not in SOURCE_CHOICES:
        raise ValueError("Unreviewed requested source")
    if (
        sys.platform != "linux"
        or os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_OS") != "Linux"
    ):
        raise RuntimeError("Diagnostic requires hosted Linux Actions")
    if not sys.flags.isolated or Path.cwd().resolve() != root:
        raise RuntimeError("Require isolated Python in the retained source checkout")
    if any(
        getattr(stream, "write_through", False) is not True
        for stream in (sys.stdout, sys.stderr)
    ):
        raise RuntimeError("Require explicit Python -u for fork-child output")
    if Path("mutants").exists():
        raise RuntimeError("Fresh owned checkout required; no verdict/statistics cache")
    actual = subprocess.run(
        ["/usr/bin/git", "rev-parse", "HEAD"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    if actual != source_sha:
        raise RuntimeError("Source identity mismatch")
    subprocess.run(["/usr/bin/git", "diff", "--quiet", "HEAD", "--"], check=True)
    extras = subprocess.run(
        [
            "/usr/bin/git",
            "ls-files",
            "--others",
            "--",
            "src",
            "tests",
            "scripts",
            ":(top,glob)*.py",
        ],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.splitlines()
    if any(Path(name).suffix == ".py" for name in extras):
        raise RuntimeError("Unexpected Python source in retained checkout")
    if (
        hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest()
        != manifest["uv_lock_sha256"]
    ):
        raise RuntimeError("Retained lock mismatch")
    return admit_package(root, manifest)


def admit_package(root: Path, manifest: dict[str, object]) -> Path:
    """Bind every reviewed statistics implementation file to the locked venv."""
    if importlib.metadata.version("mutmut") != "3.8.0":
        raise RuntimeError("Re-review the statistics boundary for a different mutmut")
    environment = (root / ".venv").resolve(strict=True)
    if Path(sys.prefix).resolve() != environment:
        raise RuntimeError("Python is not retained locked environment")
    spec = importlib.util.find_spec("mutmut")
    if spec is None or spec.origin is None:
        raise RuntimeError("Pinned mutmut package absent")
    package = Path(spec.origin).resolve().parent
    if not package.is_relative_to(environment):
        raise RuntimeError("mutmut origin outside retained venv")
    hashes = cast("dict[str,str]", manifest["mutmut_normalized_source_sha256"])
    for name, expected in hashes.items():
        text = (package / name).read_text(encoding="utf-8")
        if hashlib.sha256(text.encode()).hexdigest() != expected:
            raise RuntimeError("Pinned mutmut source differs from reviewed3.8.0")
    return package


def main(arguments: list[str]) -> int:
    """Future hosted-only entry; never invoked during local preparation."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--source-sha", choices=SOURCE_CHOICES, default=SOURCE_SHA)
    options = parser.parse_args(arguments)
    root = Path(options.source_root).resolve(strict=True)
    manifest_path = Path(__file__).with_name("retained_mutmut_clean_source.json")
    manifest = validate_manifest(json.loads(manifest_path.read_text(encoding="utf-8")))
    package = admit(root, manifest, options.source_sha)
    module = importlib.import_module("mutmut.__main__")
    config = module.config()
    if config.source_paths != [Path(path) for path in SOURCE_ROOTS]:
        raise RuntimeError("Source-path configuration changed")
    if (
        config.pytest_add_cli_args_test_selection != STATS_SELECTION
        or config.pytest_add_cli_args != PYTEST_ARGUMENTS
    ):
        raise RuntimeError("Statistics selection/flags changed")
    if str(config.process_isolation.value) != "forkserver":
        raise RuntimeError("Statistics isolation differs from retained source")
    emit(
        {
            "source_sha": options.source_sha,
            "manifest_context_source_sha": SOURCE_SHA,
            "mutmut": "3.8.0",
            "mutmut_origin": str(package),
            "python": sys.version,
            "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            "debug_override_only": True,
            "identity_prepare_prelude_replayed": False,
            "diagnostic_output_policy": "explicit_python_-u",
            "driver_isolated_import_admission": True,
            "selection": STATS_SELECTION,
            "pytest_arguments": PYTEST_ARGUMENTS,
        }
    )

    def invoke() -> object:
        return module.cli.main(
            args=["run", "--max-children", "1"], standalone_mode=False
        )

    return clean_tests_only(cast("CleanApi", module), config, invoke)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
