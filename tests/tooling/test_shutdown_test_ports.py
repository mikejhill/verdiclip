"""Fake shutdown ports target implementation globals even behind instrumentation."""

from types import FunctionType, ModuleType, SimpleNamespace
from typing import cast

import pytest
from tests.tooling import test_scroll_shutdown as shutdown_tests


@pytest.mark.parametrize("fail", [False, True])
def test_wrapped_method_uses_ports_and_restores_module(
    monkeypatch: pytest.MonkeyPatch, *, fail: bool
) -> None:
    """Instrumentation is still called; only implementation dependencies are faked."""
    module = ModuleType("fake_shutdown")
    module.__dict__["monotonic"] = lambda: 123
    exec(  # noqa: S102 - fixed synthetic function, no external source
        "def shutdown(values):\n    values.append(monotonic())\n",
        module.__dict__,
    )
    original = cast(FunctionType, module.__dict__["shutdown"])
    calls: list[str] = []

    def wrapper(values: list[int]) -> None:
        calls.append("wrapper")
        original(values)
        if fail:
            raise RuntimeError("original control")

    module.__dict__["Owner"] = SimpleNamespace(shutdown=wrapper)
    monkeypatch.setattr(shutdown_tests.importlib, "import_module", lambda _: module)
    invoke = shutdown_tests.method("fake.py", "Owner", "shutdown", monotonic=lambda: 0)
    values: list[int] = []
    if fail:
        with pytest.raises(RuntimeError, match="original control"):
            invoke(values)
    else:
        invoke(values)
    assert values == [0]
    assert calls == ["wrapper"]
    assert module.__dict__["monotonic"]() == 123
