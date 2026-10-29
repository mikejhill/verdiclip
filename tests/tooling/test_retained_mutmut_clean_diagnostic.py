"""Pure clean-boundary controls; no mutmut, Qt, application or child execution."""

from __future__ import annotations

import copy
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
