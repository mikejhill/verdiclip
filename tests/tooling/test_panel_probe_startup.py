"""Startup diagnostics never substitute for native probe watchdog evidence."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from scripts import panel_feasibility as supervisor
from scripts import panel_probe_startup as startup


def test_fixed_import_unchanged_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wrapper records phases without creating the original output directory."""
    arguments = ["wrapper", "--output", str(tmp_path / "probe"), "--x", "42"]
    monkeypatch.setattr(sys, "argv", arguments)

    def invoke() -> int:
        assert sys.argv is arguments
        assert not (tmp_path / "probe").exists()
        value = json.loads((tmp_path / "probe-startup.json").read_bytes())
        assert [phase["phase"] for phase in value["phases"]] == [
            "entered",
            "import_start",
            "import_complete",
        ]
        return 7

    def load(name: str) -> object:
        assert name == "scripts.panel_uia_probe"
        value = json.loads((tmp_path / "probe-startup.json").read_bytes())
        assert value["phases"][-1]["phase"] == "import_start"
        return SimpleNamespace(main=invoke)

    monkeypatch.setattr(startup.importlib, "import_module", load)
    assert startup.main() == 7
    assert (
        json.loads((tmp_path / "probe-startup.json").read_bytes())["phases"][-1][
            "phase"
        ]
        == "returned"
    )
    assert not (tmp_path / "probe-startup.pending").exists()


@pytest.mark.parametrize(
    "error", [RuntimeError("bad import"), SystemExit(3), KeyboardInterrupt()]
)
def test_original_exception_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    """Errors and SystemExit are recorded then re-raised as the same object."""
    monkeypatch.setattr(sys, "argv", ["wrapper", "--output", str(tmp_path / "probe")])

    def fail(_name: str) -> None:
        raise error

    monkeypatch.setattr(startup.importlib, "import_module", fail)
    with pytest.raises(type(error)) as caught:
        startup.main()
    assert caught.value is error
    record = json.loads((tmp_path / "probe-startup.json").read_bytes())["phases"][-1]
    assert record["phase"] == "exception"
    assert record["exception_type"] == type(error).__name__


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--output"],
        ["--output", "relative/probe"],
        ["--output=/unused/probe"],
        ["--output", "/unused/probe", "--output", "/unused/probe"],
        ["x"] * 33,
    ],
)
def test_invalid_output_rejected(arguments: list[str]) -> None:
    """Malformed routing cannot write a diagnostic in an inferred directory."""
    with pytest.raises(ValueError, match=r".+"):
        startup.diagnostic_path(arguments)


def test_atomic_bounded_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Interrupted replacement leaves prior valid evidence and bounded pending bytes."""
    path = tmp_path / "probe-startup.json"
    diagnostic = startup.StartupDiagnostic(path)
    diagnostic.record("entered")
    previous = path.read_bytes()

    def fail(_self: Path, _target: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="replace failed"):
        diagnostic.record("exception", ValueError("é" * 10000))
    assert path.read_bytes() == previous
    pending = path.with_suffix(".pending").read_bytes()
    assert len(pending) <= 8192
    assert len(json.loads(pending)["phases"][-1]["message"]) == 512
    with pytest.raises(ValueError, match="already exists"):
        startup.StartupDiagnostic(path)


def test_phase_and_byte_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject growth before publication or allocation of a large diagnostic file."""
    diagnostic = startup.StartupDiagnostic(tmp_path / "probe-startup.json")
    for _ in range(5):
        diagnostic.record("entered")
    with pytest.raises(ValueError, match="bound"):
        diagnostic.record("entered")
    diagnostic = startup.StartupDiagnostic(tmp_path / "other.json")
    monkeypatch.setattr(startup, "_MAX_BYTES", 1)
    with pytest.raises(ValueError, match="bound"):
        diagnostic.record("entered")
    assert not diagnostic.path.exists()


def test_launcher_preserves_probe_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the module target changes; no diagnostic event extends the watchdog."""
    commands: list[list[str]] = []

    class Job:
        def start(self, command: list[str], cwd: Path) -> tuple[int, int]:
            assert cwd == supervisor.ROOT
            commands.append(command)
            return 123, 456

        def wait(self, handle: int, timeout: int) -> int:
            assert (handle, timeout) == (456, 0)
            return 0

    monkeypatch.setattr(supervisor.os, "environ", {})
    fixture = {"kind": "owned-edge", "click": [42, 43], "pid": 789}
    result = supervisor.run_probe(
        cast("supervisor.OwnedJob", Job()), fixture, tmp_path, experiment="browser"
    )
    assert result["exit_code"] == 0
    assert commands[0][1:] == [
        "-m",
        "scripts.panel_probe_startup",
        "--x",
        "42",
        "--y",
        "43",
        "--expected-pid",
        "789",
        "--fixture-manifest",
        str(tmp_path / "fixture.json"),
        "--output",
        str(tmp_path / "probe"),
        "--settle-discovery",
        "--browser-sequence",
    ]
    assert supervisor.NATIVE_CALL_DEADLINE_SECONDS == 3


def test_diagnostic_phase_does_not_extend_watchdog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A flushed import-start phase never becomes provider readiness evidence."""
    (tmp_path / "probe-startup.json").write_bytes(
        b'{"phases":[{"phase":"import_start"}]}'
    )

    class Job:
        def start(self, _command: list[str], _cwd: Path) -> tuple[int, int]:
            return 123, 456

        def wait(self, _handle: int, _timeout: int) -> None:
            return None

    times = iter([0.0, 0.0, 3.01])
    monkeypatch.setattr(supervisor.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(supervisor.os, "environ", {})
    fixture = {"kind": "owned-edge", "click": [42, 43], "pid": 789}
    with pytest.raises(RuntimeError, match="startup exceeded three seconds"):
        supervisor.run_probe(cast("supervisor.OwnedJob", Job()), fixture, tmp_path)


def test_failure_to_log_exception_preserves_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A diagnostics disk error cannot mask the original import exception."""
    monkeypatch.setattr(sys, "argv", ["wrapper", "--output", str(tmp_path / "probe")])
    original = ImportError("first failure")
    record = startup.StartupDiagnostic.record

    def fail(_name: str) -> None:
        raise original

    def log(
        self: startup.StartupDiagnostic, phase: str, error: BaseException | None = None
    ) -> None:
        if error is not None:
            raise OSError("disk full")
        record(self, phase, error)

    monkeypatch.setattr(startup.importlib, "import_module", fail)
    monkeypatch.setattr(startup.StartupDiagnostic, "record", log)
    with pytest.raises(ImportError, match="first failure") as caught:
        startup.main()
    assert caught.value is original
