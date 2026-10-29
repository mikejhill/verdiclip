"""Pure clean-boundary controls; no mutmut, Qt, application or child execution."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from scripts import retained_mutmut_clean_diagnostic as diagnostic


@dataclass
class Fixture:
    """In-memory ports matching the pinned orchestrator's phase sequencing."""

    api: diagnostic.CleanApi
    config: diagnostic.DebugConfig
    invoke: Callable[[], object]
    reports: list[object]
    original_forced_calls: list[bool]
    continuations: list[str]
    clean_calls: list[list[str]]
    failure: BaseException


def fixture(mode: str = "pass") -> Fixture:
    """Model pinned control flow; every real test operation remains a fake."""
    reports: list[object] = []
    forced: list[bool] = []
    continued: list[str] = []
    clean_calls: list[list[str]] = []
    config = SimpleNamespace(debug=False, selection=["tests/"], other="unchanged")
    failure = (
        SystemExit(1) if mode in {"stats_fail", "clean_fail"} else ValueError(mode)
    )
    duration_order: list[str] = []

    def stats(*_args: object, **_kwargs: object) -> None:
        if mode == "stats_fail":
            raise failure
        duration_order[:] = ["fresh-stats-fast", "fresh-stats-slow"]

    api = SimpleNamespace(run_stats_collection=stats)

    def collect(*args: object, **kwargs: object) -> str:
        if mode != "cache":
            api.run_stats_collection(*args, **kwargs)
        return "original-collect-result"

    def forced_fail(*_args: object, **_kwargs: object) -> None:
        forced.append(True)

    api.collect_or_load_stats = collect
    api.run_forced_fail_test = forced_fail

    def invoke() -> None:
        result = api.collect_or_load_stats("runner", apply_config_invalidation=True)
        assert result == "original-collect-result"
        if mode == "association_fail":
            raise failure
        clean_calls.append(list(duration_order))
        if mode in {"clean_fail", "clean_error"}:
            raise failure
        api.run_forced_fail_test("runner")
        continued.extend(["startup", "submit"])

    return Fixture(
        cast("diagnostic.CleanApi", api),
        config,
        invoke,
        reports,
        forced,
        continued,
        clean_calls,
        failure,
    )


def test_clean_pass_stops_before_forced_or_mutant_execution() -> None:
    """Fresh statistics reach clean, but a passing clean cannot start mutants."""
    case = fixture()
    originals = (
        case.api.run_stats_collection,
        case.api.collect_or_load_stats,
        case.api.run_forced_fail_test,
    )
    before = copy.deepcopy(vars(case.config))
    assert (
        diagnostic.clean_tests_only(
            case.api, case.config, case.invoke, case.reports.append
        )
        == 0
    )
    assert case.clean_calls == [["fresh-stats-fast", "fresh-stats-slow"]]
    assert case.original_forced_calls == []
    assert case.continuations == []
    assert vars(case.config) == before
    assert case.api.run_stats_collection is originals[0]
    assert case.api.collect_or_load_stats is originals[1]
    assert case.api.run_forced_fail_test is originals[2]
    assert case.reports[-1] == {
        "diagnostic_phase": "clean_completed_then_stopped",
        "forced_fail_started": False,
        "mutant_execution_started": False,
        "mutation_score": None,
        "nonreproduction_is_not_resolution": True,
    }


@pytest.mark.parametrize(
    "mode", ["stats_fail", "association_fail", "clean_fail", "clean_error"]
)
def test_original_failure_propagates_without_later_phases(mode: str) -> None:
    """Statistics, association and clean failures stay red with exact identity."""
    case = fixture(mode)
    originals = (
        case.api.run_stats_collection,
        case.api.collect_or_load_stats,
        case.api.run_forced_fail_test,
    )
    expected = SystemExit if mode in {"stats_fail", "clean_fail"} else ValueError
    with pytest.raises(expected) as caught:
        diagnostic.clean_tests_only(
            case.api, case.config, case.invoke, case.reports.append
        )
    assert caught.value is case.failure
    assert case.original_forced_calls == []
    assert case.continuations == []
    assert case.config.debug is False
    assert case.api.run_stats_collection is originals[0]
    assert case.api.collect_or_load_stats is originals[1]
    assert case.api.run_forced_fail_test is originals[2]
    if mode in {"stats_fail", "association_fail"}:
        assert case.clean_calls == []


def test_cached_statistics_cannot_admit_clean() -> None:
    """Cached/skipped statistics cannot manufacture a fresh duration order."""
    case = fixture("cache")
    with pytest.raises(RuntimeError, match="loaded/skipped"):
        diagnostic.clean_tests_only(
            case.api, case.config, case.invoke, case.reports.append
        )
    assert case.clean_calls == []
    assert case.original_forced_calls == []
    assert case.continuations == []
    assert case.config.debug is False


def test_premature_forced_boundary_is_failure_not_clean_pass() -> None:
    """A call before fresh statistics cannot be labelled successful clean."""
    case = fixture()

    def premature() -> object:
        return case.api.run_forced_fail_test("runner")

    with pytest.raises(RuntimeError, match="without fresh statistics"):
        diagnostic.clean_tests_only(
            case.api, case.config, premature, case.reports.append
        )
    assert case.original_forced_calls == []
    assert case.clean_calls == []
    assert case.config.debug is False


def test_unexpected_cli_return_is_incomplete() -> None:
    """The absence of the pinned stop boundary must never become a pass."""
    case = fixture()
    with pytest.raises(RuntimeError, match="without the required clean-test"):
        diagnostic.clean_tests_only(
            case.api, case.config, lambda: None, case.reports.append
        )
    assert case.config.debug is False


def test_manifest_is_exact_main_and_all_six_implementations() -> None:
    """The new target is explicitly5fe, not a silently retargeted old control."""
    path = Path(diagnostic.__file__).with_name("retained_mutmut_clean_source.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert diagnostic.validate_manifest(data) is data
    assert data["source_sha"] == "5fe14b25b651c02caea31786e44e9508595e4e19"
    assert set(data["mutmut_normalized_source_sha256"]) == diagnostic.PINNED_FILES


def source_admission_ports(
    monkeypatch: pytest.MonkeyPatch, root: Path, actual_head: str
) -> tuple[dict[str, object], list[list[str]], list[str]]:
    """Fake only OS/process/package ports; inspect no installed mutmut or Qt."""
    manifest_path = Path(diagnostic.__file__).with_name(
        "retained_mutmut_clean_source.json"
    )
    manifest = diagnostic.validate_manifest(
        json.loads(manifest_path.read_text(encoding="utf-8"))
    )
    control_root = Path(diagnostic.__file__).parents[1]
    # Model the real Linux checkout, even when these pure ports run on Windows.
    lock = (control_root / "uv.lock").read_text(encoding="utf-8").encode("utf-8")
    assert hashlib.sha256(lock).hexdigest() == manifest["uv_lock_sha256"]
    (root / "uv.lock").write_bytes(lock)
    monkeypatch.chdir(root)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_OS", "Linux")
    monkeypatch.setattr(
        diagnostic,
        "sys",
        SimpleNamespace(
            platform="linux",
            flags=SimpleNamespace(isolated=True),
            stdout=SimpleNamespace(write_through=True),
            stderr=SimpleNamespace(write_through=True),
        ),
    )
    commands: list[list[str]] = []
    packages: list[str] = []

    def git(command: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        return SimpleNamespace(
            stdout=actual_head if command[1:3] == ["rev-parse", "HEAD"] else ""
        )

    def package(checkout: Path, context: dict[str, object]) -> Path:
        assert checkout == root
        assert context is manifest
        packages.append("admitted")
        return root / ".venv" / "mutmut"

    monkeypatch.setattr(diagnostic.subprocess, "run", git)
    monkeypatch.setattr(diagnostic, "admit_package", package)
    return manifest, commands, packages


@pytest.mark.parametrize("source", diagnostic.SOURCE_CHOICES)
def test_requested_source_admits_only_its_exact_head(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, source: str
) -> None:
    """Every reviewed checkout retains clean-source and identical lock checks."""
    manifest, commands, packages = source_admission_ports(monkeypatch, tmp_path, source)
    assert diagnostic.admit(tmp_path, manifest, source) == tmp_path / ".venv" / "mutmut"
    assert [command[1] for command in commands] == ["rev-parse", "diff", "ls-files"]
    assert packages == ["admitted"]
    assert (
        manifest["source_sha"] == diagnostic.SOURCE_SHA
    )  # Shared historical package context.


@pytest.mark.parametrize(
    ("source", "actual"),
    [
        (requested, head)
        for requested in diagnostic.SOURCE_CHOICES
        for head in diagnostic.SOURCE_CHOICES
        if head != requested
    ],
)
def test_other_reviewed_head_is_not_substitutable_for_requested_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, source: str, actual: str
) -> None:
    """An allowed SHA is still rejected when it is not the requested checkout."""
    manifest, commands, packages = source_admission_ports(monkeypatch, tmp_path, actual)
    with pytest.raises(RuntimeError, match="Source identity mismatch"):
        diagnostic.admit(tmp_path, manifest, source)
    assert len(commands) == 1
    assert packages == []


def test_default_source_remains_historical(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Existing callers cannot silently change from the historical default."""
    manifest, _, packages = source_admission_ports(
        monkeypatch, tmp_path, diagnostic.SOURCE_SHA
    )
    diagnostic.admit(tmp_path, manifest)
    assert packages == ["admitted"]


def test_changed_fixture_lock_is_rejected_before_package_access(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fixture normalization must not weaken the production's exact hash gate."""
    manifest, _, packages = source_admission_ports(
        monkeypatch, tmp_path, diagnostic.SOURCE_SHA
    )
    lock = tmp_path / "uv.lock"
    lock.write_bytes(lock.read_bytes() + b"\n# changed dependency input\n")
    with pytest.raises(RuntimeError, match="Retained lock mismatch"):
        diagnostic.admit(tmp_path, manifest)
    assert packages == []


def test_unknown_source_is_rejected_before_process_or_package_access(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No arbitrary checkout or implicit current HEAD can enter the diagnostic."""
    manifest, commands, packages = source_admission_ports(
        monkeypatch, tmp_path, diagnostic.SOURCE_SHA
    )
    with pytest.raises(ValueError, match="Unreviewed requested source"):
        diagnostic.admit(tmp_path, manifest, "0" * 40)
    assert commands == []
    assert packages == []


def test_unknown_cli_source_fails_before_even_resolving_checkout(
    tmp_path: Path,
) -> None:
    """Argparse restricts the public entry to the reviewed literal pins."""
    with pytest.raises(SystemExit) as caught:
        diagnostic.main(
            ["--source-root", str(tmp_path / "absent"), "--source-sha", "HEAD"]
        )
    assert caught.value.code == 2
