"""Pure child-harness integration; no SDK, Qt or real processes are loaded."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace, TracebackType
from typing import cast

import pytest
from scripts import retained_mutmut_order_diagnostic as diagnostic


class Call:
    """Track mandatory scope completion and unwinding with no real fixture."""

    def __init__(self) -> None:
        self.result: int | None = None
        self.closed = False

    def __enter__(self) -> Call:
        """Enter the fake mandatory observation scope."""
        return self

    def complete(self, result: int) -> None:
        """Retain the exact integer runner outcome."""
        self.result = result

    def __exit__(
        self,
        _kind: type[BaseException] | None,
        _value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> bool:
        """Record unconditional cleanup without suppressing original errors."""
        self.closed = True
        return False


class Observer:
    """Only a context port; real hook behavior is qualified separately."""

    def __init__(self) -> None:
        self.call = Call()

    def scope(self) -> Call:
        """Return the tracked mandatory scope."""
        return self.call


def test_statistics_bypass_observer_and_preserve_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The original statistics path does not construct plugins or import pytest."""
    marker, params, existing = object(), ["same"], [object()]

    def original(runner: object, values: list[str], **kwargs: object) -> int:
        assert runner is marker
        assert values is params
        assert kwargs["plugins"] is existing
        return 7

    def forbidden(*_args: object) -> None:
        pytest.fail("Statistics must not import pytest or create an observer")

    monkeypatch.setattr(diagnostic.importlib, "import_module", forbidden)
    target = cast(diagnostic.ExecuteTarget, SimpleNamespace(execute_pytest=original))
    api = cast(diagnostic.ObserverApi, SimpleNamespace(Observer=forbidden))
    with diagnostic.observe_clean_execution(
        target, api, Path("retained"), lambda: None, lambda _p: None
    ):
        assert target.execute_pytest(marker, params, plugins=existing) == 7
    assert target.execute_pytest is original


@pytest.mark.parametrize("status", [0, 3])
def test_clean_child_preserves_plugins_outcome_and_mandatory_scope(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    """Only the clean child imports pytest and appends the owned plugin."""
    pid = [1]
    monkeypatch.setattr(diagnostic.os, "getpid", lambda: pid[0])
    exit_kind = type("SessionExit", (Exception,), {})
    module = SimpleNamespace(exit=SimpleNamespace(Exception=exit_kind))
    monkeypatch.setattr(diagnostic.importlib, "import_module", lambda _name: module)
    observer, existing = Observer(), object()
    order = ("node",)
    constructed = []

    def factory(*args: object) -> Observer:
        constructed.append(args)
        return observer

    def original(_runner: object, _params: list[str], **kwargs: object) -> int:
        assert kwargs["plugins"] == [existing, observer]
        return status

    target = cast(diagnostic.ExecuteTarget, SimpleNamespace(execute_pytest=original))
    api = cast(diagnostic.ObserverApi, SimpleNamespace(Observer=factory))

    def report(_packet: dict[str, object]) -> None:
        return

    with diagnostic.observe_clean_execution(
        target, api, Path("retained"), lambda: order, report
    ):
        pid[0] = 2
        assert target.execute_pytest(object(), [], plugins=[existing]) == status
    assert constructed == [(Path("retained/mutants"), order, report, exit_kind)]
    assert observer.call.closed
    assert observer.call.result == status
    assert target.execute_pytest is original


def test_original_clean_error_unwinds_scope_and_restores_harness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An original exception cannot skip the mandatory observer finalization."""
    pid = [1]
    monkeypatch.setattr(diagnostic.os, "getpid", lambda: pid[0])
    module = SimpleNamespace(exit=SimpleNamespace(Exception=Exception))
    monkeypatch.setattr(diagnostic.importlib, "import_module", lambda _name: module)
    observer = Observer()
    prior = ValueError("original runner")

    def original(*_args: object, **_kwargs: object) -> object:
        raise prior

    target = cast(diagnostic.ExecuteTarget, SimpleNamespace(execute_pytest=original))
    api = cast(diagnostic.ObserverApi, SimpleNamespace(Observer=lambda *_a: observer))

    def invoke() -> None:
        with diagnostic.observe_clean_execution(
            target, api, Path("retained"), lambda: ("node",), lambda _p: None
        ):
            pid[0] = 2
            target.execute_pytest(object(), [])

    with pytest.raises(ValueError, match="original runner") as caught:
        invoke()
    assert caught.value is prior
    assert observer.call.closed
    assert observer.call.result is None
    assert target.execute_pytest is original


def test_clean_observation_cannot_import_pytest_into_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The frozen SDK parent is rejected before any clean observer setup."""
    monkeypatch.setattr(diagnostic.os, "getpid", lambda: 1)

    def forbidden(*_args: object, **_kwargs: object) -> object:
        pytest.fail("Parent must not execute or import clean pytest")

    monkeypatch.setattr(diagnostic.importlib, "import_module", forbidden)
    target = cast(diagnostic.ExecuteTarget, SimpleNamespace(execute_pytest=forbidden))
    api = cast(diagnostic.ObserverApi, SimpleNamespace(Observer=forbidden))

    def invoke() -> None:
        with diagnostic.observe_clean_execution(
            target, api, Path("retained"), lambda: ("node",), lambda _p: None
        ):
            target.execute_pytest(object(), [])

    with pytest.raises(RuntimeError, match="isolated child"):
        invoke()
    assert target.execute_pytest is forbidden


def test_fixed_observer_loader_preserves_paths_and_retires_registry() -> None:
    """Only the fixed control-owned file loads; no cached search path is added."""
    before = list(sys.path)
    assert diagnostic.OBSERVER_MODULE_NAME not in sys.modules
    with diagnostic.control_observer_api() as api:
        assert callable(api.Observer)
        assert sys.path == before
        assert diagnostic.OBSERVER_MODULE_NAME in sys.modules
    assert diagnostic.OBSERVER_MODULE_NAME not in sys.modules
    assert sys.path == before
