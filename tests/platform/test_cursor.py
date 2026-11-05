"""Deterministic cursor-lease faults; no native APIs or presentation claims."""

from __future__ import annotations

import ctypes
from types import SimpleNamespace
from typing import cast

import pytest
from tests.capture.input_dll_fixture import DllFunction

from verdiclip.exceptions import PlatformError
from verdiclip.platform.cursor import CursorInfo, CursorVisibilityLease


class CursorApi:
    """Separate logical cursor flags from the unqueryable Magnification state."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[str] = []
        self.flags = 1
        self.shown = True
        self.fail = self.raise_on = ""
        monkeypatch.setattr(ctypes, "WinDLL", self.load, raising=False)

    def operation(self, name: str) -> bool:
        """A mutation may occur even when its result is false or uncertain."""
        self.calls.append(name)
        if self.raise_on == name:
            raise OSError(f"Fixture {name} failed")
        return self.fail != name

    def load(self, name: str, **_kwargs: object) -> SimpleNamespace:
        """Bind the production ABI against independent, in-memory ports."""
        assert name in {"user32", "Magnification"}
        if not self.operation(f"load:{name}"):
            raise OSError("Fixture DLL unavailable")
        if name == "user32":
            return SimpleNamespace(GetCursorInfo=DllFunction(self.observe))
        return SimpleNamespace(
            MagInitialize=DllFunction(lambda: self.operation("initialize")),
            MagShowSystemCursor=DllFunction(self.show),
            MagUninitialize=DllFunction(lambda: self.operation("uninitialize")),
        )

    def observe(self, address: object) -> bool:
        """The flags deliberately do not change when Magnification hides."""
        info = ctypes.cast(
            cast(ctypes.c_void_p, address), ctypes.POINTER(CursorInfo)
        ).contents
        assert info.size == ctypes.sizeof(CursorInfo)
        info.flags = self.flags
        info.cursor = 123
        return self.operation("observe")

    def show(self, visible: object) -> bool:
        """Record the actual side effect separately from API return success."""
        self.shown = bool(visible)
        return self.operation("restore" if self.shown else "hide")


def test_one_hide_is_restored_once_without_rechecking_logical_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Readiness can follow suppression; cleanup has an explicit show operation."""
    api = CursorApi(monkeypatch)
    lease = CursorVisibilityLease()
    lease.acquire()
    assert not api.shown
    assert lease.suppression_attempted
    assert api.calls == [
        "load:user32",
        "observe",
        "load:Magnification",
        "initialize",
        "hide",
    ]
    cleaned = lease.close()
    assert cleaned
    assert api.shown
    assert api.calls[-2:] == ["restore", "uninitialize"]
    previous = api.calls.copy()
    cleaned = lease.close()
    assert cleaned
    assert api.calls == previous


@pytest.mark.parametrize("flags", [0, 2, 3, 4])
def test_hidden_suppressed_or_unknown_flags_do_not_change_global_visibility(
    monkeypatch: pytest.MonkeyPatch, flags: int
) -> None:
    """Skipping these flags is conservative, not proof of a previous API state."""
    api = CursorApi(monkeypatch)
    api.flags, api.shown = flags, False
    lease = CursorVisibilityLease()
    lease.acquire()
    cleaned = lease.close()
    assert cleaned
    assert not api.shown
    assert api.calls == ["load:user32", "observe"]
    assert not lease.suppression_attempted


@pytest.mark.parametrize("failure", ["observe", "load:Magnification", "initialize"])
def test_failed_preparation_does_not_attempt_hide_or_show(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """No restoration is claimed necessary before successful initialization."""
    api = CursorApi(monkeypatch)
    api.fail = failure
    lease = CursorVisibilityLease()
    with pytest.raises((OSError, PlatformError)):
        lease.acquire()
    cleaned = lease.close()
    assert cleaned
    assert "hide" not in api.calls
    assert "restore" not in api.calls
    assert "uninitialize" not in api.calls


@pytest.mark.parametrize("raises", [False, True])
def test_failed_hide_still_has_an_explicit_restoration_obligation(
    monkeypatch: pytest.MonkeyPatch, *, raises: bool
) -> None:
    """A refusal or exception after the hide effect never skips cleanup."""
    api = CursorApi(monkeypatch)
    if raises:
        api.raise_on = "hide"
    else:
        api.fail = "hide"
    lease = CursorVisibilityLease()
    with pytest.raises((OSError, PlatformError)):
        lease.acquire()
    assert not api.shown
    cleaned = lease.close()
    assert cleaned
    assert api.shown
    assert api.calls[-2:] == ["restore", "uninitialize"]


@pytest.mark.parametrize("failure", ["restore", "uninitialize"])
@pytest.mark.parametrize("raises", [False, True])
def test_teardown_failures_remain_unconfirmed_and_cannot_be_relabelled_clean(
    monkeypatch: pytest.MonkeyPatch, failure: str, *, raises: bool
) -> None:
    """Both teardown stages are attempted, and the failed receipt is latched."""
    api = CursorApi(monkeypatch)
    lease = CursorVisibilityLease()
    lease.acquire()
    if raises:
        api.raise_on = failure
        with pytest.raises(OSError, match=failure):
            lease.close()
    else:
        api.fail = failure
        cleaned = lease.close()
        assert not cleaned
    assert api.calls[-2:] == ["restore", "uninitialize"]
    previous = api.calls.copy()
    api.fail = api.raise_on = ""
    cleaned = lease.close()
    assert not cleaned
    assert api.calls == previous


@pytest.mark.parametrize("started", [False, True])
def test_closed_or_started_lease_cannot_be_reacquired(
    monkeypatch: pytest.MonkeyPatch, *, started: bool
) -> None:
    """Repeated capture requires a new owner, never a second hide on one lease."""
    api = CursorApi(monkeypatch)
    lease = CursorVisibilityLease()
    if started:
        lease.acquire()
    else:
        cleaned = lease.close()
        assert cleaned
    before = api.calls.copy()
    with pytest.raises(PlatformError, match="cannot be reused"):
        lease.acquire()
    assert api.calls == before
    cleaned = lease.close()
    assert cleaned


def test_logically_showing_does_not_reveal_another_clients_hidden_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Document the unresolved global-state risk rather than assert preservation."""
    api = CursorApi(monkeypatch)
    api.shown = False
    lease = CursorVisibilityLease()
    lease.acquire()
    assert not api.shown
    cleaned = lease.close()
    assert cleaned
    # This is the candidate's known incompatibility, not acceptable preservation.
    # The same logical flags occur with both initially visible and hidden state.
    assert api.flags == 1
    assert api.shown
