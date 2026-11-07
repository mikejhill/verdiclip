"""Portable admission tests, not claims of native installed-channel qualification."""

import ctypes
import importlib.abc
import importlib.machinery
import zipfile
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import cast, override

import pytest
from scripts import installed_channel_smoke as smoke


def terminal(*, passed: bool = True) -> dict[str, object]:
    """Construct exact clean terminal evidence for independent mutation tests."""
    return {
        "passed": passed,
        "failure": None if passed else "cancelled",
        "cleanup_failure": None,
        "not_launched": False,
        "helper": {
            "pid": 22,
            "producer_verified": True,
            "exit_code": 0,
            "cleanup_confirmed": True,
            "closed": True,
            "cleanup_errors": [],
            "failure": None,
        },
    }


def test_clean_ping_and_cancel() -> None:
    """Success and expected cancellation require separate exact predicates."""
    assert smoke.admit_terminal("ping-finish", terminal(), 1)
    for case in smoke.CASES[1:4]:
        assert smoke.admit_terminal(case, terminal(passed=False), 0)
    assert not smoke.admit_terminal("parent-death", terminal(), 1)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cleanup_confirmed", False),
        ("closed", False),
        ("cleanup_errors", ["close denied"]),
        ("failure", "inspect failed"),
    ],
)
def test_cleanup_failures_never_pass(field: str, value: object) -> None:
    """An expected cancellation must not hide failed native cleanup."""
    receipt = terminal(passed=False)
    helper = receipt["helper"]
    assert isinstance(helper, dict)
    helper[field] = value
    assert not smoke.admit_terminal("cancel-ready", receipt, 0)


@pytest.mark.parametrize(
    ("case", "replies"), [("ping-finish", 0), ("ping-finish", 2), ("cancel-ready", 1)]
)
def test_reply_count_is_exact(case: str, replies: int) -> None:
    """Missing, duplicate and unexpected replies cannot pass."""
    assert not smoke.admit_terminal(
        case, terminal(passed=case == "ping-finish"), replies
    )


def test_unstarted_cancel_and_cleanup_timeout() -> None:
    """No-launch cancellation is valid, but cleanup expiry is never valid."""
    receipt = terminal(passed=False)
    receipt.update(helper=None, not_launched=True)
    assert smoke.admit_terminal("cancel-immediate", receipt, 0)
    receipt["cleanup_failure"] = "cleanup-timeout"
    assert not smoke.admit_terminal("cancel-immediate", receipt, 0)


def test_receipt_bounds(tmp_path: Path) -> None:
    """Retained JSON must be bounded and an object."""
    path = tmp_path / "receipt.json"
    smoke.write_receipt(path, {"ok": True})
    assert smoke.read_receipt(path) == {"ok": True}
    with pytest.raises(ValueError, match="bound"):
        smoke.write_receipt(path, "a" * smoke.MAX_RECEIPT_BYTES)
    path.write_text("[]")
    with pytest.raises(TypeError, match="object"):
        smoke.read_receipt(path)
    path.write_bytes(b"a" * (smoke.MAX_RECEIPT_BYTES + 1))
    with pytest.raises(ValueError, match="bound"):
        smoke.read_receipt(path)


@pytest.mark.parametrize("natural", [True, False])
def test_parent_death_requires_natural_exit(
    monkeypatch: pytest.MonkeyPatch, *, natural: bool
) -> None:
    """Pinned peer death and empty outer job must precede any fallback cleanup."""
    clock = [0.0]
    calls = []

    class Owner:
        job = 12

        def snapshot(self) -> dict[str, object]:
            return {"exit_code": 17, "members": [] if natural else [22]}

    class Witness:
        def __init__(self, job: int, gui: int, peer: int) -> None:
            assert (job, gui, peer) == (12, 21, 22)

        def kill_parent(self) -> None:
            calls.append("kill-parent")

        def exited(self) -> bool:
            return natural

        def close(self) -> None:
            calls.append("close-witness")

    monkeypatch.setattr(smoke, "DeathWitness", Witness)
    monkeypatch.setattr(smoke.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        smoke.time, "sleep", lambda amount: clock.__setitem__(0, clock[0] + amount)
    )
    result = smoke.observe_parent_death(
        cast("smoke.ExternalOwner", Owner()), {"pid": 21, "peer": {"pid": 22}}, 0.03
    )
    assert result["passed"] is natural
    assert calls[0] == "kill-parent"
    assert calls.count("kill-parent") == 1


@pytest.mark.parametrize("elapsed", [14.999, 15.0, 15.001])
def test_late_receipt_cannot_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, elapsed: float
) -> None:
    """Final evidence consumption must finish strictly before the original limit."""
    clock = [0.0]
    closed = []

    class Owner:
        def start(self, _command: object) -> None:
            pass

        def snapshot(self) -> dict[str, object]:
            return {"exit_code": 0, "members": []}

        def close(self) -> list[str]:
            closed.append(True)
            return []

    def read(_path: Path) -> dict[str, object]:
        clock[0] = elapsed
        return {
            "case": "ping-finish",
            "passed": True,
            "terminal": terminal(),
            "replies": 1,
            "identity": {"pid": 22},
            "witnesses": {"ready_observed": True},
            "generation": 1,
            "errors": [],
            "heartbeat_count": 1,
            "paint_count": 1,
        }

    monkeypatch.setattr(smoke, "ExternalOwner", Owner)
    monkeypatch.setattr(
        smoke,
        "cleanup_owner",
        lambda owner: {"confirmed": not owner.close(), "forced": False},
    )
    monkeypatch.setattr(smoke, "HelperCommand", lambda *_args: None)
    monkeypatch.setattr(smoke, "read_receipt", read)
    monkeypatch.setattr(smoke.time, "monotonic", lambda: clock[0])
    result = smoke.run_case("ping-finish", tmp_path, tmp_path / "app.whl")
    assert result["passed"] is (elapsed < 15)
    assert closed == [True]


@pytest.mark.parametrize(
    "failure", [None, "second-open", "membership", "wait", "close"]
)
def test_death_witness_pins_members_and_closes_all(  # noqa: C901 - fixed API fault matrix
    monkeypatch: pytest.MonkeyPatch,
    failure: str | None,
) -> None:
    """Native signature seam proves only owned live handles can be terminated."""
    calls: list[tuple[str, object]] = []

    class Function:
        def __init__(self, name: str) -> None:
            self.name = name

        def __call__(self, *args: object) -> int:
            calls.append((self.name, args[0]))
            if self.name == "OpenProcess":
                if failure == "second-open" and args[2] == 22:
                    return 0
                return cast("int", args[2])
            if self.name == "IsProcessInJob":
                ctypes.cast(cast("int", args[2]), ctypes.POINTER(ctypes.c_long))[0] = (
                    failure != "membership"
                )
                return 1
            if self.name == "WaitForSingleObject":
                return 0 if failure == "wait" else smoke.WAIT_TIMEOUT
            if self.name == "CloseHandle":
                return int(failure != "close")
            return 1

    class Kernel:
        def __getattr__(self, name: str) -> Function:
            function = Function(name)
            setattr(self, name, function)
            return function

    monkeypatch.setattr(
        smoke.ctypes, "WinDLL", lambda *_a, **_k: Kernel(), raising=False
    )
    monkeypatch.setattr(smoke.ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(
        smoke.ctypes, "WinError", lambda _code: OSError("denied"), raising=False
    )
    if failure in ("second-open", "membership", "wait"):
        with pytest.raises((OSError, RuntimeError)):
            smoke.DeathWitness(12, 21, 22)
        assert ("CloseHandle", 21) in calls
        assert not any(name == "TerminateProcess" for name, _ in calls)
    else:
        witness = smoke.DeathWitness(12, 21, 22)
        witness.kill_parent()
        assert ("TerminateProcess", 21) in calls
        assert ("TerminateProcess", 22) not in calls
        if failure == "close":
            with pytest.raises(RuntimeError, match="close failures"):
                witness.close()
        else:
            witness.close()
        assert [(name, value) for name, value in calls if name == "CloseHandle"] == [
            ("CloseHandle", 21),
            ("CloseHandle", 22),
        ]


@pytest.mark.parametrize("case", ["ping-finish", "cancel-ready", "delete-startup"])
def test_missing_case_witness_rejects(case: str) -> None:
    """A summary success cannot stand in for readiness or facade deletion."""
    receipt: dict[str, object] = {
        "case": case,
        "passed": True,
        "terminal": terminal(passed=case == "ping-finish"),
        "replies": int(case == "ping-finish"),
        "generation": 1,
        "errors": [],
        "heartbeat_count": 1,
        "paint_count": 1,
        "identity": {"pid": 22},
        "witnesses": {},
    }
    assert not smoke.admit_child(case, receipt)
    receipt["witnesses"] = {
        "ready_observed": True,
        "facade_deleted": True,
        "lifetime_retained": True,
    }
    assert smoke.admit_child(case, receipt)
    receipt["paint_count"] = -1
    assert not smoke.admit_child(case, receipt)


@pytest.mark.parametrize("exits", [True, False])
def test_fallback_cleanup_has_separate_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    *,
    exits: bool,
) -> None:
    """Requested termination does not establish that the outer job is empty."""
    clock = [0.0]
    calls = []

    class Api:
        def terminate(self, job: int) -> None:
            calls.append(("terminate", job))

    class Owner:
        process = object()
        job = 12
        api = Api()

        def snapshot(self) -> dict[str, object]:
            done = bool(calls) and exits
            return {"exit_code": 17 if done else None, "members": [] if done else [22]}

        def close(self) -> list[str]:
            calls.append(("close", 12))
            return []

    monkeypatch.setattr(smoke.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        smoke.time, "sleep", lambda amount: clock.__setitem__(0, clock[0] + amount)
    )
    result = smoke.cleanup_owner(cast("smoke.ExternalOwner", Owner()))
    assert result["forced"] is True
    assert result["confirmed"] is exits
    assert calls == [("terminate", 12), ("close", 12)]


def test_ready_identity_must_match_retired_producer() -> None:
    """A different or nonpositive claimed peer cannot corroborate terminal evidence."""
    receipt: dict[str, object] = {
        "case": "ping-finish",
        "passed": True,
        "terminal": terminal(),
        "replies": 1,
        "generation": 1,
        "errors": [],
        "heartbeat_count": 0,
        "paint_count": 0,
        "identity": {"pid": 22},
        "witnesses": {"ready_observed": True},
    }
    assert smoke.admit_child("ping-finish", receipt)
    for pid in (0, -1, True, 23):
        receipt["identity"] = {"pid": pid}
        assert not smoke.admit_child("ping-finish", receipt)


def test_forced_cleanup_never_preserves_pass(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Even independently confirmed fallback death cannot upgrade natural proof."""

    class Owner:
        def start(self, _command: object) -> None:
            pass

        def snapshot(self) -> dict[str, object]:
            return {"exit_code": 0, "members": []}

    monkeypatch.setattr(smoke, "ExternalOwner", Owner)
    monkeypatch.setattr(smoke, "HelperCommand", lambda *_args: None)
    monkeypatch.setattr(smoke, "read_receipt", lambda _p: {})
    monkeypatch.setattr(smoke, "admit_child", lambda *_args: True)
    monkeypatch.setattr(
        smoke, "cleanup_owner", lambda _owner: {"confirmed": True, "forced": True}
    )
    assert (
        smoke.run_case("ping-finish", tmp_path, tmp_path / "wheel.whl")["passed"]
        is False
    )


def test_parent_death_preserves_inspection_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing final handle close annotates rather than hides the first failure."""

    class Owner:
        job = 12

    class Witness:
        def __init__(self, *_args: object) -> None:
            pass

        def kill_parent(self) -> None:
            pass

        def exited(self) -> bool:
            raise OSError("first inspection error")

        def close(self) -> None:
            raise OSError("second close error")

    monkeypatch.setattr(smoke, "DeathWitness", Witness)
    with pytest.raises(OSError, match="first inspection error") as caught:
        smoke.observe_parent_death(
            cast("smoke.ExternalOwner", Owner()),
            {"pid": 21, "peer": {"pid": 22}},
            smoke.time.monotonic() + 1,
        )
    assert any("second close error" in note for note in caught.value.__notes__)


@pytest.fixture
def channel_wheel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate the channel's independent loaded-file checks from the base verifier."""

    @dataclass
    class Runtime:
        origins: tuple[tuple[str, str], ...]

    root = tmp_path / "installed packages å"
    package = root / "verdiclip"
    package.mkdir(parents=True)
    (package / "__init__.py").write_bytes(b"# Installed package.\n")
    modules = (
        smoke.helper_channel,
        smoke.helper_peer,
        smoke.helper_protocol,
        smoke.helper_bootstrap,
        smoke.scroll_stitch,
        smoke.frame_envelope,
        smoke.observe_protocol,
        smoke.observe_native,
        smoke.observe_provider,
    )
    wheel = tmp_path / "fixture.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for module in modules:
            relative = module.__name__.replace(".", "/") + ".py"
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = ("# " + relative + "\n").encode()
            path.write_bytes(payload)
            archive.writestr(relative, payload)
            monkeypatch.setattr(module, "__file__", str(path))

    class Loader(importlib.abc.Loader):
        @override
        def exec_module(self, module: ModuleType) -> None:
            """Supply only the already independently tested base inspection result."""
            module.inspect_runtime = lambda _wheel: Runtime(  # ty: ignore[unresolved-attribute]
                (("verdiclip", str(package / "__init__.py")),)
            )

    monkeypatch.setattr(
        smoke.importlib.util,
        "spec_from_file_location",
        lambda name, _path: importlib.machinery.ModuleSpec(name, Loader()),
    )
    return wheel


def test_channel_verifier_checks_all_fixed_loaded_files(channel_wheel: Path) -> None:
    """The real channel verifier accepts exact files in both package subdirectories."""
    assert smoke.verify_wheel(channel_wheel)["origins"]


@pytest.mark.parametrize(
    "module_name",
    [
        "helper_channel",
        "helper_peer",
        "helper_protocol",
        "helper_bootstrap",
        "scroll_stitch",
        "frame_envelope",
        "observe_protocol",
        "observe_native",
        "observe_provider",
    ],
)
@pytest.mark.parametrize("failure", ["mixed-origin", "missing-wheel", "wrong-bytes"])
def test_channel_verifier_rejects_each_module_mismatch(
    channel_wheel: Path,
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    failure: str,
) -> None:
    """Extra installed files or mixed imports cannot masquerade as exact wheel code."""
    module = getattr(smoke, module_name)
    relative = module.__name__.replace(".", "/") + ".py"
    if failure == "mixed-origin":
        other = channel_wheel.parent / "other.py"
        other.write_bytes(Path(module.__file__).read_bytes())
        monkeypatch.setattr(module, "__file__", str(other))
        with pytest.raises(RuntimeError, match="outside the verified wheel"):
            smoke.verify_wheel(channel_wheel)
        return
    with zipfile.ZipFile(channel_wheel) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    if failure == "missing-wheel":
        del entries[relative]
    else:
        entries[relative] += b"# Different bytes.\n"
    with zipfile.ZipFile(channel_wheel, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    with pytest.raises((KeyError, ValueError)):
        smoke.verify_wheel(channel_wheel)
