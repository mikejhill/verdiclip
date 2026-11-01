"""Portable orchestration proofs; no mutmut, application, Qt or child execution."""

from __future__ import annotations

import copy
import importlib.machinery
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from scripts import retained_mutmut_stats_diagnostic as diagnostic


@dataclass
class Fixture:
    """Only in-memory ports for the reviewed statistics boundary."""

    api: diagnostic.StatsApi
    config: diagnostic.DebugConfig
    invoke: Callable[[], object]
    reports: list[object]
    continuations: list[str]
    calls: list[tuple[tuple[object, ...], dict[str, object], bool]]
    failure: BaseException | None


def make_fixture(mode: str = "pass") -> Fixture:
    """Build fake CLI control flow without importing a POSIX worker package."""
    reports: list[object] = []
    continuations: list[str] = []
    calls: list[tuple[tuple[object, ...], dict[str, object], bool]] = []
    config = SimpleNamespace(debug=False, selection=["tests/"], other="unchanged")
    failure = SystemExit(1) if mode == "fail" else ValueError("statistics failure")

    def stats(*args: object, **kwargs: object) -> None:
        calls.append((args, kwargs, config.debug))
        if mode in {"fail", "exception"}:
            raise failure

    api = SimpleNamespace(run_stats_collection=stats)

    def collect(*args: object, **kwargs: object) -> None:
        if mode != "cache":
            api.run_stats_collection(*args, **kwargs)

    api.collect_or_load_stats = collect

    def invoke() -> None:
        api.collect_or_load_stats("runner", apply_config_invalidation=True)
        continuations.append("actual mutation would start")

    return Fixture(
        cast("diagnostic.StatsApi", api),
        config,
        invoke,
        reports,
        continuations,
        calls,
        failure,
    )


def test_pass_stops_before_mutants_and_restores_originals() -> None:
    """A successful statistics call cannot continue into real mutant tests."""
    fixture = make_fixture()
    stats = fixture.api.run_stats_collection
    collect = fixture.api.collect_or_load_stats
    before = copy.deepcopy(vars(fixture.config))
    result = diagnostic.statistics_only(
        fixture.api,
        fixture.config,
        fixture.invoke,
        fixture.reports.append,
    )
    assert result == 0
    assert fixture.continuations == []
    assert fixture.calls == [(("runner",), {"apply_config_invalidation": True}, True)]
    assert fixture.api.run_stats_collection is stats
    assert fixture.api.collect_or_load_stats is collect
    assert vars(fixture.config) == before
    assert fixture.reports[-1] == {
        "diagnostic_phase": "statistics_completed_then_stopped",
        "mutant_execution_started": False,
        "mutation_score": None,
    }


@pytest.mark.parametrize("mode", ["fail", "exception"])
def test_original_statistics_failure_cannot_continue(mode: str) -> None:
    """Retain the exact original failure and restore the documented debug flag."""
    fixture = make_fixture(mode)
    expected = SystemExit if mode == "fail" else ValueError
    stats, collect = fixture.api.run_stats_collection, fixture.api.collect_or_load_stats
    with pytest.raises(expected) as caught:
        diagnostic.statistics_only(
            fixture.api, fixture.config, fixture.invoke, fixture.reports.append
        )
    assert caught.value is fixture.failure
    assert fixture.continuations == []
    assert fixture.config.debug is False
    assert fixture.api.run_stats_collection is stats
    assert fixture.api.collect_or_load_stats is collect


def test_cached_statistics_cannot_pass_or_continue() -> None:
    """Loaded/skipped statistics must not masquerade as a fresh diagnosis."""
    fixture = make_fixture("cache")
    with pytest.raises(RuntimeError, match="loaded/skipped"):
        diagnostic.statistics_only(
            fixture.api, fixture.config, fixture.invoke, fixture.reports.append
        )
    assert fixture.continuations == []
    assert fixture.calls == []
    assert fixture.config.debug is False


def test_unexpected_cli_return_cannot_pass() -> None:
    """A CLI that evades the observed boundary is an incomplete diagnosis."""
    fixture = make_fixture()
    with pytest.raises(RuntimeError, match="without the required"):
        diagnostic.statistics_only(
            fixture.api, fixture.config, lambda: None, fixture.reports.append
        )
    assert fixture.config.debug is False


def manifest() -> dict[str, object]:
    """Read only the portable adjacent reviewed data, never a host cache path."""
    path = Path(diagnostic.__file__).with_name("retained_mutmut_stats_source.json")
    return cast("dict[str,object]", json.loads(path.read_text(encoding="utf-8")))


def test_manifest_requires_each_exact_source_pin() -> None:
    """No omitted file can bypass the six reviewed source implementations."""
    good = manifest()
    hashes = cast("dict[str,str]", good["mutmut_normalized_source_sha256"])
    assert set(hashes) == diagnostic.PINNED_FILES
    assert diagnostic.validate_manifest(good) is good
    for name in diagnostic.PINNED_FILES:
        bad = copy.deepcopy(good)
        del cast("dict[str,str]", bad["mutmut_normalized_source_sha256"])[name]
        with pytest.raises(ValueError, match="all six"):
            diagnostic.validate_manifest(bad)


def test_manifest_rejects_malformed_shapes_versions_and_digests() -> None:
    """Malformed JSON-shaped inputs fail before package inspection/import."""
    for bad in [
        None,
        [],
        {},
        dict(manifest(), mutmut_version="3.9"),
        dict(manifest(), mutmut_normalized_source_sha256={}),
        dict(manifest(), source_sha="other"),
    ]:
        with pytest.raises(ValueError, match=r"object|schema|identity|all six"):
            diagnostic.validate_manifest(bad)
    bad = manifest()
    cast("dict[str,str]", bad["mutmut_normalized_source_sha256"])["__main__.py"] = (
        "notahash"
    )
    with pytest.raises(ValueError, match="digest"):
        diagnostic.validate_manifest(bad)


def find_instance(paths: list[str]) -> Path:
    """Resolve plain file specs; never execute any package initializer."""
    first = importlib.machinery.PathFinder.find_spec("verdiclip", paths)
    assert first is not None
    second = importlib.machinery.PathFinder.find_spec(
        "verdiclip.shell", first.submodule_search_locations
    )
    assert second is not None
    third = importlib.machinery.PathFinder.find_spec(
        "verdiclip.shell.instance", second.submodule_search_locations
    )
    assert third is not None
    assert third.origin is not None
    return Path(third.origin).resolve()


def test_conditional_ipc_root_counterexample_without_execution(tmp_path: Path) -> None:
    """Show only that fresh and inherited path lists can resolve differently."""
    original, staged = tmp_path / "src", tmp_path / "mutants/src"
    for path in [original, staged]:
        package = path / "verdiclip/shell"
        package.mkdir(parents=True)
        for target in [
            path / "verdiclip/__init__.py",
            package / "__init__.py",
            package / "instance.py",
        ]:
            target.write_text(
                'raise AssertionError("must not execute")', encoding="utf-8"
            )
    parent = find_instance([str(staged), str(tmp_path / "mutants"), str(original)])
    child = find_instance([str(tmp_path / "mutants"), str(original)])
    assert parent != child
    assert parent == staged / "verdiclip/shell/instance.py"
    assert child == original / "verdiclip/shell/instance.py"
