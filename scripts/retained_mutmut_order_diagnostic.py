"""Hosted-only historical-order diagnosis; importing never loads Qt."""

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
from abc import abstractmethod
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

SOURCE_SHA = "5fe14b25b651c02caea31786e44e9508595e4e19"
LOCK_SHA = "14e1d58cd3d516b6eff172dba8b92beccb93ecd7741add578af7a99adb39da78"
PINNED_FILES = {
    "__main__.py",
    "configuration.py",
    "runners/harness.py",
    "workers/isolation.py",
    "utils/file_utils.py",
    "code_coverage.py",
    "stats.py",
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
DATA_MODULE_NAME = "_verdiclip_diagnostic_duration_data"
HISTORICAL_CACHE_KEY = (
    "mutmut-v5-Linux-X64-5fe14b25b651c02caea31786e44e9508595e4e19-"
    "380086239e0a24f09e8b3f6a97c3ac122cdb7ec536097c5e004083b8c092b948-"
    "40889523071-1"
)


class ReplayResult(Protocol):
    """Only the fixed data-validation receipt crosses the control boundary."""

    receipt: dict[str, object]


class ReplayDataApi(Protocol):
    """The separately reviewed control-owned data helper, never cached code."""

    @abstractmethod
    def install_replay(self, fresh_root: Path, historical_root: Path) -> ReplayResult:
        """Replace only fresh durations after strict historical-data validation."""


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
    after_statistics: Callable[[], None] | None = None,
) -> int:
    """Keep fresh statistics/clean ordering; stop before forced or mutant tests.

    The pinned _run reaches run_forced_fail_test ONLY after the real clean
    runner returned zero. Association and clean failures propagate unchanged.
    The data hook replays only admitted durations; it does not replay the
    original identity-prepare prelude, cache state or native scheduling.
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
        if after_statistics is not None:
            after_statistics()
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
                "historical_duration_data_installed": False,
                "historical_duration_data_requested": after_statistics is not None,
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
        raise ValueError("Require all seven exact reviewed source pins")
    if any(
        type(item) is not str or re.fullmatch("[0-9a-f]{64}", item) is None
        for item in hashes.values()
    ):
        raise ValueError("Malformed source digest")
    return data


def admit(root: Path, manifest: dict[str, object]) -> Path:
    """Read-only source/version/hash checks before loading POSIX mutmut code."""
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
    if actual != SOURCE_SHA:
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


@contextmanager
def control_data_api() -> Generator[ReplayDataApi]:
    """Load only the fixed sibling helper without adding any import search path."""
    path = Path(__file__).with_name("retained_mutmut_duration_data.py")
    spec = importlib.util.spec_from_file_location(DATA_MODULE_NAME, path)
    if spec is None or spec.loader is None or DATA_MODULE_NAME in sys.modules:
        raise RuntimeError("Control data helper cannot be admitted")
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[DATA_MODULE_NAME] = loaded
    try:
        spec.loader.exec_module(loaded)
        yield cast(ReplayDataApi, loaded)
    finally:
        if sys.modules.get(DATA_MODULE_NAME) is loaded:
            sys.modules.pop(DATA_MODULE_NAME)


def main(arguments: list[str]) -> int:
    """Future hosted-only entry; never invoked during local preparation."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True)
    options = parser.parse_args(arguments)
    root = Path(options.source_root).resolve(strict=True)
    manifest_path = Path(__file__).with_name("retained_mutmut_order_source.json")
    manifest = validate_manifest(json.loads(manifest_path.read_text(encoding="utf-8")))
    package = admit(root, manifest)
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
            "source_sha": SOURCE_SHA,
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

    with control_data_api() as data:

        def install_durations() -> None:
            # Pinned collect_stats waits for its child, and save_stats finishes
            # before this hook. The dedicated job has no concurrent SDK writer.
            # These exclusive directories are a precondition, not a hostile-
            # writer or filesystem compare-and-swap guarantee.
            replay = data.install_replay(root / "mutants", root.parent / "mutants")
            emit(
                {
                    "diagnostic_phase": "historical_durations_installed",
                    "historical_cache_key": HISTORICAL_CACHE_KEY,
                    "preflight_observed_cache_id": 9705229902,
                    "cache_id_verified_by_restore_action": False,
                    "historical_duration_data_installed": True,
                    "actual_clean_collection_order_verified": False,
                    "identity_prepare_prelude_replayed": False,
                    "validation": replay.receipt,
                }
            )

        return clean_tests_only(
            cast("CleanApi", module), config, invoke, after_statistics=install_durations
        )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
