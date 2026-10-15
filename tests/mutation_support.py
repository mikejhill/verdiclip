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
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

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

# Written by mutmut's stats run, in the mutants/ folder it runs the tests from
STATS: Final = Path("mutmut-stats.json")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Order tests by their duration in mutmut's stats run, fastest first."""
    if "mutmut" not in sys.modules or not STATS.is_file():
        return
    durations = json.loads(STATS.read_text(encoding="utf-8")).get("duration_by_test")
    if isinstance(durations, dict):
        items.sort(key=lambda item: float(durations.get(item.nodeid, 0.0)))
