"""Let mutmut's mutants run under Qt; a no-op outside ``mutmut run``.

mutmut wraps every function in a ``(*args, **kwargs)`` trampoline. PySide
decides how many signal arguments to pass to a slot from the slot's own
signature, so a trampoline makes it pass them all: ``toggled(bool)`` into a
``_validate(self)`` slot raises ``TypeError`` and every Qt test fails. This
caps the positional arguments at what the original function accepts, which
is what PySide would have done for the unwrapped slot.

It also runs each mutant's tests fastest first. mutmut stops at the first
failure, so a mutant that a quick unit test catches never waits for the slow
widget journeys that happen to cover the same code.

mutmut loads this as a pytest plugin (``-p tests.mutation_support`` in
``[tool.mutmut]``), before any test imports a mutated ``verdiclip`` module.
"""

from __future__ import annotations

import functools
import importlib
import inspect
import json
import logging
import os
import sys
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Final, override

import pytest

type Wrapper = Callable[[Callable[..., Any]], Callable[..., Any]]


class TrampolineArity:
    """Wraps mutmut's trampoline factory so wrappers keep the slot's arity."""

    @staticmethod
    def positional_limit(func: Callable[..., Any]) -> int | None:
        """Return how many positional arguments ``func`` takes (None: any number)."""
        params = inspect.signature(func).parameters.values()
        if any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in params):
            return None
        positional = (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
        return sum(p.kind in positional for p in params)

    @classmethod
    def capped(cls, factory: Callable[..., Wrapper]) -> Callable[..., Wrapper]:
        """Return ``factory`` with each wrapper limited to its function's arity."""

        @functools.wraps(factory)
        def wrap(*factory_args: Any, **factory_kwargs: Any) -> Wrapper:  # noqa: ANN401
            decorate = factory(*factory_args, **factory_kwargs)

            def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
                wrapped = decorate(func)
                limit = cls.positional_limit(func)
                plain = not (
                    inspect.isgeneratorfunction(func)
                    or inspect.iscoroutinefunction(func)
                    or inspect.isasyncgenfunction(func)
                )
                if limit is None or not plain:
                    return wrapped

                @functools.wraps(func)
                def call(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
                    return wrapped(*args[:limit], **kwargs)

                return call

            return decorator

        return wrap

    @classmethod
    def install(cls) -> None:
        """Patch mutmut if this process is a mutation run."""
        if "mutmut" not in sys.modules:
            return
        # Any: mutmut is only installed for mutation runs, not for type checks
        trampoline: Any = importlib.import_module("mutmut.mutation.trampoline")
        trampoline.wrap_in_trampoline = cls.capped(trampoline.wrap_in_trampoline)


TrampolineArity.install()


class ForkserverLogOwner(logging.Filter):
    """Only the warmed forkserver may write its inherited rotating handler."""

    def __init__(self) -> None:
        super().__init__()
        self.pid = os.getpid()

    @override
    def filter(self, record: logging.LogRecord) -> bool:
        """Keep parent lifetime/exit records, not duplicate worker messages."""
        return record.process == self.pid

    @classmethod
    def install(cls) -> None:
        """Bind during collect warmup; forked sessions must not rebind ownership."""
        if "mutmut" not in sys.modules:
            return
        for handler in logging.getLogger("mutmut").handlers:
            if isinstance(handler, RotatingFileHandler) and not any(
                isinstance(existing, cls) for existing in handler.filters
            ):
                handler.addFilter(cls())


# Written by mutmut's stats run, in the mutants/ folder it runs the tests from
STATS: Final = Path("mutmut-stats.json")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Order tests by their duration in mutmut's stats run, fastest first."""
    if "mutmut" not in sys.modules or not STATS.is_file():
        return
    durations = json.loads(STATS.read_text(encoding="utf-8")).get("duration_by_test")
    if isinstance(durations, dict):
        items.sort(key=lambda item: float(durations.get(item.nodeid, 0.0)))


STATS_FAILURES: Final = (
    Path(os.environ.get("GITHUB_WORKSPACE", ".")) / "mutation-stats-failures.jsonl"
)
FAILURE_LIMIT: Final = 65536
_stats_receipt_ready = False


def retain_stats_failure(report: pytest.TestReport | pytest.CollectReport) -> None:
    """Keep child failure evidence that mutmut's parent output buffer cannot see."""
    if os.environ.get("MUTANT_UNDER_TEST") != "stats" or not _stats_receipt_ready:
        return
    try:
        if not report.failed:
            return
        record = {
            "nodeid": report.nodeid[:1024],
            "when": getattr(report, "when", "collect"),
            "failure": report.longreprtext[:8192],
            "source_sha": os.environ.get("GITHUB_SHA"),
            "run_id": os.environ.get("GITHUB_RUN_ID"),
            "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        }
        line = json.dumps(record, ensure_ascii=False) + "\n"
        size = STATS_FAILURES.stat().st_size if STATS_FAILURES.exists() else 0
        if size + len(line.encode("utf-8")) > FAILURE_LIMIT:
            return
        with STATS_FAILURES.open("a", encoding="utf-8") as stream:
            stream.write(line)
    except Exception:  # noqa: BLE001 - optional diagnostics preserve original outcome
        return


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Retain only failed reports in the real statistics context."""
    retain_stats_failure(report)


def pytest_collectreport(report: pytest.CollectReport) -> None:
    """Retain collection failures as well as executed-test failures."""
    retain_stats_failure(report)


def pytest_sessionstart() -> None:
    """A resumed same-commit run must not publish an earlier failure as current."""
    global _stats_receipt_ready  # noqa: PLW0603 - per-process pytest session state
    _stats_receipt_ready = False
    ForkserverLogOwner.install()
    if os.environ.get("MUTANT_UNDER_TEST") == "stats":
        try:
            STATS_FAILURES.write_text("", encoding="utf-8")
        except OSError:
            return
        _stats_receipt_ready = True
