"""Portable controls for isolated released-source imports and owner-thread gates."""

from __future__ import annotations

import builtins
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import sys
from asyncio import CancelledError
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import cast
from unittest.mock import Mock

import pytest
from tests.capture import released_pointer_child as owner

NONCE = "a" * 32
POINT = (31, -14)


class Pipe:
    """In-memory bytes only; never creates a native process, pipe, or reader."""

    def __init__(self) -> None:
        self.incoming: list[bytes] = []
        self.outgoing: list[dict[str, object]] = []
        self.bounds: list[int] = []
        self.waits: list[float] = []
        self.closed = False

    def send_bytes(self, value: bytes) -> None:
        """Record one JSON receipt."""
        self.outgoing.append(json.loads(value))

    def recv_bytes(self, maximum: int) -> bytes:
        """Enforce the same explicit maximum as a real connection."""
        self.bounds.append(maximum)
        value = self.incoming.pop(0)
        if len(value) > maximum:
            raise OSError("oversized")
        return value

    def poll(self, timeout: float = 0.0) -> bool:
        """Observe arrivals without time passing or blocking."""
        self.waits.append(timeout)
        return bool(self.incoming)

    def close(self) -> None:
        """Record endpoint retirement."""
        self.closed = True

    def command(self, phase: str, nonce: str = NONCE) -> None:
        """Queue the exact finite command shape."""
        self.incoming.append(json.dumps({"phase": phase, "nonce": nonce}).encode())


@pytest.fixture
def source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Make inert baseline-shaped bytes, never import a real product service."""
    for name in tuple(sys.modules):
        if name == "verdiclip" or name.startswith("verdiclip."):
            monkeypatch.delitem(sys.modules, name)
    source = tmp_path / "src"
    capture = source / "verdiclip" / "capture"
    capture.mkdir(parents=True)
    hashes = {}
    for name in ("service.py", "overlay.py"):
        data = ("# inert fixture " + name).encode()
        (capture / name).write_bytes(data)
        hashes[name] = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(owner, "_HASHES", hashes)
    return source


def module(name: str, path: Path) -> ModuleType:
    """Construct origin metadata without executing product code."""
    result = ModuleType(name)
    result.__file__ = str(path)
    result.__spec__ = importlib.machinery.ModuleSpec(name, None, origin=str(path))
    return result


def test_target_imports_only_standard_library(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolving the spawn target cannot transitively import product/test/Qt code."""
    original = builtins.__import__

    def guarded(
        name: str,
        global_scope: Mapping[str, object] | None = None,
        local_scope: Mapping[str, object] | None = None,
        fromlist: Sequence[str] | None = (),
        level: int = 0,
    ) -> ModuleType:
        assert not name.startswith(("verdiclip", "tests", "PySide6"))
        return original(name, global_scope, local_scope, fromlist, level)

    spec = importlib.util.spec_from_file_location("isolated_child", owner.__file__)
    assert spec is not None
    assert spec.loader is not None
    isolated = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, isolated)
    monkeypatch.setattr(builtins, "__import__", guarded)
    spec.loader.exec_module(isolated)
    assert callable(isolated.child)


@pytest.mark.parametrize(
    "name", ["verdiclip", "verdiclip.capture.service", "verdiclip.fake"]
)
def test_any_preloaded_product_module_refuses_source(
    source: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """Even an apparently baseline-local or null preload invalidates isolation."""
    monkeypatch.setitem(sys.modules, name, None)
    with pytest.raises(RuntimeError, match="product-preloaded"):
        owner.verify_source(str(source.parent))


def test_exact_release_hash_constants() -> None:
    """Controls cannot silently switch to the candidate's implementation."""
    assert owner._HASHES == {
        "service.py": (
            "3006a3f1abf5e7cb349674fc01441672975232d199131256cc2e88fcfd58bae8"
        ),
        "overlay.py": (
            "d6854656379edd56d334f96b54634a2261405e21d58c3a16d4dc15725481bda8"
        ),
    }


@pytest.mark.parametrize("name", ["service.py", "overlay.py"])
def test_each_release_file_must_match(source: Path, name: str) -> None:
    """A valid directory does not substitute for checking both source bytes."""
    assert owner.verify_source(str(source.parent)) == source
    (source / "verdiclip" / "capture" / name).write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="source-mismatch"):
        owner.verify_source(str(source.parent))


def test_relative_baseline_rejected(source: Path) -> None:
    """The child never resolves an ambiguous working-directory-relative checkout."""
    del source
    with pytest.raises(RuntimeError, match="path-invalid"):
        owner.verify_source("relative")


@pytest.mark.parametrize("part", ["src", "service.py", "overlay.py"])
def test_redirected_baseline_paths_rejected(
    source: Path, monkeypatch: pytest.MonkeyPatch, part: str
) -> None:
    """Simulated symlink escapes need no symlink privilege on Windows runners."""
    original = Path.resolve

    def redirected(path: Path, *, strict: bool = False) -> Path:
        resolved = original(path, strict=strict)
        return source.parent / "outside" if path.name == part else resolved

    monkeypatch.setattr(Path, "resolve", redirected)
    with pytest.raises(RuntimeError, match=r"source-path-invalid|source-mismatch"):
        owner.verify_source(str(source.parent))


@pytest.mark.parametrize("field", ["file", "spec", "package", "missing"])
def test_every_origin_and_package_path_must_be_local(
    source: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    """A baseline service cannot hide a candidate dependency or package path."""
    path = source / "verdiclip" / "capture" / "service.py"
    loaded = module("verdiclip.capture.service", path)
    outside = source.parent / "foreign.py"
    outside.write_text("# inert", encoding="utf-8")
    if field == "file":
        loaded.__file__ = str(outside)
    elif field == "spec":
        loaded.__spec__ = importlib.machinery.ModuleSpec(
            loaded.__name__, None, origin=str(outside)
        )
    elif field == "package":
        loaded.__path__ = [str(source), str(outside.parent)]
    else:
        loaded.__spec__ = None
    monkeypatch.setitem(sys.modules, loaded.__name__, loaded)
    with pytest.raises(RuntimeError, match="origin-mismatch"):
        owner.verify_origins(source)


def test_no_loaded_origins_is_not_attestation(source: Path) -> None:
    """An empty audit cannot report baseline verification."""
    with pytest.raises(RuntimeError, match="origin-unavailable"):
        owner.verify_origins(source)


def test_delayed_imports_follow_verified_source(
    source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the real old factories are imported after inserting baseline src first."""
    factories = {"CaptureService": Mock(), "MssScreenSource": Mock()}
    calls = []

    def import_product(name: str) -> ModuleType:
        calls.append(name)
        assert sys.path[0] == str(source)
        path = source / "verdiclip" / "capture" / "service.py"
        result = module(name, path)
        for key, value in factories.items():
            setattr(result, key, value)
        monkeypatch.setitem(sys.modules, name, result)
        return result

    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(owner.importlib, "import_module", import_product)
    loaded = owner.load_baseline(str(source.parent))
    assert calls == ["verdiclip.capture.service", "verdiclip.capture.grabber"]
    assert loaded.capture_service is factories["CaptureService"]
    assert loaded.screen_source is factories["MssScreenSource"]
    assert owner.NoTargets().visible_windows() == ()
    assert owner.NoTargets().foreground() is None


@pytest.mark.parametrize(
    "payload", [b'{"phase":"a","phase":"b"}', b"[]", b"{", b'{"x":NaN}', b"x" * 4097]
)
def test_wire_rejects_nonprotocol_frames(payload: bytes) -> None:
    """Malformed, duplicate, non-finite and oversized frames fail closed."""
    pipe = Pipe()
    pipe.incoming.append(payload)
    with pytest.raises((ValueError, RuntimeError, OSError)):
        owner.receive(pipe, owner.time.monotonic() + 5)
    assert pipe.bounds == [4096]


def test_wire_deadline_and_size(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only arrival is waited for, and expired/late frames cannot be consumed."""
    pipe = Pipe()
    pipe.command("start")
    monkeypatch.setattr(owner.time, "monotonic", Mock(return_value=10.0))
    assert owner.receive(pipe, 11.0) == {"phase": "start", "nonce": NONCE}
    assert pipe.waits == [1.0]
    pipe.command("start")
    with pytest.raises(RuntimeError, match="timeout"):
        owner.receive(pipe, 10.0)
    assert len(pipe.incoming) == 1
    monkeypatch.setattr(owner.time, "monotonic", Mock(side_effect=[10.0, 12.0]))
    with pytest.raises(RuntimeError, match="unavailable"):
        owner.receive(pipe, 11.0)
    with pytest.raises(RuntimeError, match="message-size"):
        owner.send(pipe, {"x": "x" * 4096})
    assert not pipe.outgoing


def hosted() -> dict[str, str]:
    """A synthetic authorized-host environment; never used for native execution."""
    return {
        "GITHUB_ACTIONS": "true",
        "RUNNER_ENVIRONMENT": "github-hosted",
        "RUNNER_OS": "Windows",
        "RUNNER_ARCH": "X64",
        "GITHUB_REPOSITORY": "mikejhill/verdiclip",
        "VERDICLIP_HOSTED_SCROLLBAR": "1",
        "VERDICLIP_RELEASED_BASELINE": "1",
        "QT_QPA_PLATFORM": "windows",
        "VERDICLIP_QUALIFICATION_HEAD": "a" * 40,
        "VERDICLIP_HOSTED_SOURCE": "a" * 40,
    }


@pytest.mark.parametrize("key", list(hosted()))
def test_each_host_guard_is_required(monkeypatch: pytest.MonkeyPatch, key: str) -> None:
    """A local/foreign/unknown/unpinned source cannot start this native target."""
    environment = hosted()
    monkeypatch.setattr(owner.sys, "platform", "win32")
    owner.require_hosted(environment)
    environment.pop(key)
    with pytest.raises(RuntimeError, match="hosted-source-required"):
        owner.require_hosted(environment)


def service_and_native() -> tuple[Mock, Mock]:
    """Build only inert old-service/native observations."""
    service = Mock(spec=owner.Service)
    overlay = Mock(spec=owner.Overlay)
    overlay.winId.return_value = 7
    overlay.isVisible.return_value = True
    service.is_selecting = True
    service.overlays = (overlay,)
    native = Mock(spec=owner.Native)
    native.cross = 44
    native.is_window.return_value = True
    native.visible.return_value = True
    native.process_id.return_value = os.getpid()
    native.pointer.return_value = owner.Pointer(
        showing=True, cursor=44, point=POINT, window=7, foreground=7
    )
    return service, native


@pytest.mark.parametrize(
    "failure",
    [
        "not-selecting",
        "empty",
        "too-many",
        "hidden-qt",
        "zero",
        "duplicate",
        "dead",
        "hidden-native",
        "foreign-pid",
        "hidden-cursor",
        "wrong-cross",
        "missing-cross",
        "moved",
        "foreign-pointer",
    ],
)
def test_admission_requires_all_live_facts(failure: str) -> None:  # noqa: C901 - independent negative controls
    """Every required observation is causal, not an API-call success receipt."""
    service, native = service_and_native()
    if failure == "not-selecting":
        service.is_selecting = False
    elif failure == "empty":
        service.overlays = ()
    elif failure == "too-many":
        service.overlays *= 17
    elif failure == "hidden-qt":
        service.overlays[0].isVisible.return_value = False
    elif failure == "zero":
        service.overlays[0].winId.return_value = 0
    elif failure == "duplicate":
        service.overlays *= 2
    elif failure == "dead":
        native.is_window.return_value = False
    elif failure == "hidden-native":
        native.visible.return_value = False
    elif failure == "foreign-pid":
        native.process_id.return_value = os.getpid() + 1
    elif failure == "missing-cross":
        native.cross = 0
    else:
        native.pointer.return_value = owner.Pointer(
            failure != "hidden-cursor",
            45 if failure == "wrong-cross" else 44,
            (32, -14) if failure == "moved" else POINT,
            8 if failure == "foreign-pointer" else 7,
            7,
        )
    assert owner.eligible(service, native, POINT) is None


@pytest.mark.parametrize("foreground", [7, 8])
def test_foreground_is_classified_without_requiring_preservation(
    foreground: int,
) -> None:
    """The released service may really activate its own overlay; record this fact."""
    service, native = service_and_native()
    native.pointer.return_value = owner.Pointer(
        showing=True, cursor=44, point=POINT, window=7, foreground=foreground
    )
    assert owner.eligible(service, native, POINT) == owner.Admission(
        (7,), foreground == 7
    )


def session(mode: str = "cancel") -> tuple[owner.Session, Pipe, Mock, Mock, Mock]:
    """A timer callback state machine with no Qt application or native handles."""
    service, native = service_and_native()
    pipe, quit_app = Pipe(), Mock()
    value = owner.Session(
        pipe, NONCE, mode, POINT, service=service, native=native, quit_app=quit_app
    )
    return value, pipe, service, native, quit_app


def test_cancel_waits_only_for_retirement_then_parent_finish() -> None:
    """First restoration snapshot occurs before source/process/pipe teardown."""
    value, pipe, service, native, quit_app = session()
    value.tick()
    assert pipe.outgoing == [
        {"phase": "admitted", "nonce": NONCE, "handles": [7], "foreground_owned": True}
    ]
    pipe.command("action")
    value.tick()
    service.cancel.assert_called_once()
    value.tick()
    assert len(pipe.outgoing) == 1
    service.is_selecting = False
    value.tick()
    assert len(pipe.outgoing) == 1
    native.is_window.return_value = False
    value.tick()
    assert pipe.outgoing[-1] == {"phase": "retired", "nonce": NONCE}
    assert value.phase == "finish"
    assert not value.completed
    assert native.pointer.call_count == 2  # Admission and fresh action only.
    assert not pipe.closed
    quit_app.assert_not_called()
    pipe.command("finish")
    value.tick()
    assert value.completed
    assert not value.failed
    quit_app.assert_called_once()


@pytest.mark.parametrize("mode", ["cancel", "crash"])
@pytest.mark.parametrize("change", ["moved", "handles", "wrong-cross", "lost-window"])
def test_action_reproves_same_live_ownership(mode: str, change: str) -> None:
    """An admitted child cannot act using only an earlier proof."""
    value, pipe, service, native, quit_app = session(mode)
    value.tick()
    if change == "moved":
        native.pointer.return_value = owner.Pointer(
            showing=True, cursor=44, point=(0, 0), window=7, foreground=7
        )
    elif change == "handles":
        service.overlays[0].winId.return_value = 9
        native.pointer.return_value = owner.Pointer(
            showing=True, cursor=44, point=POINT, window=9, foreground=9
        )
    elif change == "wrong-cross":
        native.pointer.return_value = owner.Pointer(
            showing=True, cursor=45, point=POINT, window=7, foreground=7
        )
    else:
        native.is_window.return_value = False
    pipe.command("action")
    value.tick()
    assert value.failed
    assert not value.completed
    assert len(pipe.outgoing) == 1
    service.cancel.assert_not_called()
    native.terminate.assert_not_called()
    quit_app.assert_called_once()


def test_crash_marker_immediately_precedes_self_termination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A returned termination call fails; no cancel or successful receipt follows."""
    value, pipe, service, native, quit_app = session("crash")
    calls = []
    original_send = pipe.send_bytes

    def send(value: bytes) -> None:
        original_send(value)
        calls.append(pipe.outgoing[-1]["phase"])

    def terminate() -> None:
        calls.append("terminate")
        raise RuntimeError("native-returned")

    monkeypatch.setattr(pipe, "send_bytes", send)
    native.terminate.side_effect = terminate
    value.tick()
    pipe.command("action")
    value.tick()
    assert calls == ["admitted", "crash-entry", "terminate"]
    assert pipe.outgoing[-1] == {"phase": "crash-entry", "nonce": NONCE}
    service.cancel.assert_not_called()
    assert value.failed
    quit_app.assert_called_once()


@pytest.mark.parametrize("phase", ["admission", "action", "retirement", "finish"])
def test_every_phase_has_one_absolute_deadline(
    monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    """Timeout never extends just because observations remain unfavorable."""
    value, pipe, service, native, quit_app = session()
    value.phase = phase
    value.deadline = 10
    monkeypatch.setattr(owner.time, "monotonic", Mock(return_value=11))
    value.tick()
    value.tick()
    assert value.failed
    assert not value.completed
    assert pipe.outgoing == []
    service.cancel.assert_not_called()
    native.pointer.assert_not_called()
    quit_app.assert_called_once()


@pytest.mark.parametrize(
    "command", ["finish", "start", "crash", "cancel", "wrong-nonce", "extra"]
)
def test_action_accepts_only_exact_command(command: str) -> None:
    """Mode cannot be switched or a nonce/extra field smuggled over the pipe."""
    value, pipe, service, native, quit_app = session()
    value.tick()
    if command == "extra":
        pipe.incoming.append(
            json.dumps({"phase": "action", "nonce": NONCE, "extra": 1}).encode()
        )
    else:
        pipe.command(
            "action" if command == "wrong-nonce" else command,
            "b" * 32 if command == "wrong-nonce" else NONCE,
        )
    value.tick()
    assert value.failed
    service.cancel.assert_not_called()
    native.terminate.assert_not_called()
    quit_app.assert_called_once()


def test_early_action_cannot_trigger_before_admission() -> None:
    """The parent cannot bypass a missing visible CrossCursor."""
    value, pipe, service, native, _quit_app = session()
    native.cross = 0
    pipe.command("action")
    value.tick()
    assert value.failed
    assert not pipe.outgoing
    service.cancel.assert_not_called()


def test_child_attests_before_start_and_never_creates_gui_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mocked entrypoint proves factory/GUI ordering and exact private readiness."""
    pipe = Pipe()
    pipe.command("start")
    baseline = owner.Baseline(Mock(), Mock())
    identity = {"private": "runtime"}
    calls = []
    monkeypatch.setattr(owner, "require_hosted", Mock())

    def load(_root: str) -> owner.Baseline:
        calls.append("verified")
        return baseline

    def run_gui(*_args: object) -> None:
        assert calls == ["verified"]
        assert pipe.bounds == [4096]
        assert pipe.outgoing == [
            {
                "phase": "ready",
                "nonce": NONCE,
                "pid": os.getpid(),
                "runtime": identity,
                "baseline_verified": True,
            }
        ]
        calls.append("gui")

    monkeypatch.setattr(owner, "load_baseline", load)
    monkeypatch.setattr(owner, "runtime", lambda: identity)
    monkeypatch.setattr(owner, "_run_gui", run_gui)
    owner.child(pipe, NONCE, "private-root", "cancel", POINT)
    assert calls == ["verified", "gui"]
    assert pipe.closed


@pytest.mark.parametrize("failure", ["source", "ready", "start", "gui"])
def test_child_failure_closes_pipe_without_private_traceback(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """No raw provider exception, source path, or opaque handle escapes the child."""
    pipe = Mock(spec=owner.Wire)
    baseline, gui = Mock(), Mock()
    pipe.recv_bytes.return_value = json.dumps(
        {"phase": "start", "nonce": NONCE}
    ).encode()
    monkeypatch.setattr(owner, "require_hosted", Mock())
    loader = Mock(return_value=baseline)
    monkeypatch.setattr(owner, "load_baseline", loader)
    monkeypatch.setattr(owner, "runtime", dict)
    monkeypatch.setattr(owner, "_run_gui", gui)
    target = {
        "source": loader,
        "ready": pipe.send_bytes,
        "start": pipe.recv_bytes,
        "gui": gui,
        "close": pipe.close,
    }[failure]
    target.side_effect = OSError("private-provider-path-handle")
    with pytest.raises(SystemExit) as caught:
        owner.child(pipe, NONCE, "private-root", "cancel", POINT)
    assert caught.value.code == 1
    pipe.close.assert_called_once()
    if failure in {"source", "ready", "start"}:
        gui.assert_not_called()


@pytest.mark.parametrize("phase", ["action", "finish", "wrong-nonce"])
def test_rejected_start_never_constructs_gui(
    monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    """Baseline attestation does not itself grant permission to open the desktop."""
    pipe, gui = Pipe(), Mock()
    pipe.command(
        "start" if phase == "wrong-nonce" else phase,
        "b" * 32 if phase == "wrong-nonce" else NONCE,
    )
    monkeypatch.setattr(owner, "require_hosted", Mock())
    monkeypatch.setattr(owner, "load_baseline", Mock())
    monkeypatch.setattr(owner, "runtime", dict)
    monkeypatch.setattr(owner, "_run_gui", gui)
    with pytest.raises(SystemExit):
        owner.child(pipe, NONCE, "private-root", "cancel", POINT)
    gui.assert_not_called()
    assert pipe.closed


def test_gui_uses_unmodified_old_factories_and_delays_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All Qt constructors are mocked: no source capture or native GUI is run."""
    pipe = Pipe()
    service, native = service_and_native()
    source, app, timer = Mock(), Mock(), Mock()
    baseline = owner.Baseline(Mock(return_value=source), Mock(return_value=service))
    monkeypatch.setattr("PySide6.QtWidgets.QApplication", Mock(return_value=app))
    monkeypatch.setattr("PySide6.QtCore.QTimer", Mock(return_value=timer))
    monkeypatch.setattr(owner, "Native", Mock(return_value=native))

    def event_loop() -> int:
        tick = timer.timeout.connect.call_args.args[0]
        tick()
        pipe.command("action")
        tick()
        service.is_selecting = False
        native.is_window.return_value = False
        tick()
        assert pipe.outgoing[-1]["phase"] == "retired"
        source.close.assert_not_called()
        app.quit.assert_not_called()
        assert not pipe.closed
        pipe.command("finish")
        tick()
        return 0

    app.exec.side_effect = event_loop
    owner._run_gui(pipe, NONCE, "cancel", POINT, baseline)
    assert isinstance(baseline.capture_service, Mock)
    args, kwargs = baseline.capture_service.call_args
    assert args[0] is source
    assert isinstance(args[1], owner.NoTargets)
    assert kwargs == {"show_magnifier": False}
    assert app.setQuitOnLastWindowClosed.call_args.args == (False,)
    timer.setInterval.assert_called_once_with(10)
    service.capture_region.assert_called_once()
    source.close.assert_called_once()
    timer.stop.assert_called_once()
    assert service.cancel.call_count == 2


@pytest.mark.parametrize(
    "error", [OSError("private"), KeyboardInterrupt(), SystemExit(9)]
)
def test_independent_cleanup_retains_every_owned_action(error: BaseException) -> None:
    """A failed cancel or timer stop cannot strand source or pipe cleanup."""
    first, second, third = Mock(side_effect=error), Mock(), Mock()
    with pytest.raises(type(error)):
        owner._cleanup((first, second, third))
    second.assert_called_once()
    third.assert_called_once()


def test_source_cleanup_when_service_construction_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ownership starts immediately, not only after every constructor succeeds."""
    source = Mock()
    baseline = owner.Baseline(
        Mock(return_value=source), Mock(side_effect=RuntimeError("constructor"))
    )
    monkeypatch.setattr("PySide6.QtWidgets.QApplication", Mock())
    with pytest.raises(RuntimeError, match="constructor"):
        owner._run_gui(Pipe(), NONCE, "cancel", POINT, baseline)
    source.close.assert_called_once()


def test_native_backend_loads_shared_cross_and_kills_only_self(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mock DLL calls assert exact Qt cursor loading and forced-exit identity."""
    user, kernel = Mock(), Mock()
    user.LoadImageW.return_value = 44
    kernel.GetCurrentProcess.return_value = 501
    monkeypatch.setattr(
        owner.ctypes, "WinDLL", Mock(side_effect=[user, kernel]), raising=False
    )
    native = owner.Native()
    assert native.cross == 44
    user.LoadImageW.assert_called_once_with(None, 32515, 2, 0, 0, 0x8040)
    with pytest.raises(RuntimeError, match="termination-returned"):
        native.terminate()
    kernel.TerminateProcess.assert_called_once_with(501, 97)


def test_native_observation_is_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real ctypes structs are filled by fake DLL calls, never by desktop APIs."""
    user, kernel = Mock(), Mock()
    user.LoadImageW.return_value = 44
    user.WindowFromPoint.return_value = 7
    user.GetForegroundWindow.return_value = 8

    def fill(address: object) -> bool:
        info = cast("owner.CursorInfo", getattr(address, "_obj"))  # noqa: B009 - ctypes byref wrapper is deliberately opaque
        assert info.size == owner.ctypes.sizeof(owner.CursorInfo)
        info.flags, info.cursor = 1, 44
        info.point.x, info.point.y = POINT
        return True

    user.GetCursorInfo.side_effect = fill
    monkeypatch.setattr(
        owner.ctypes, "WinDLL", Mock(side_effect=[user, kernel]), raising=False
    )
    native = owner.Native()
    assert native.pointer() == owner.Pointer(
        showing=True, cursor=44, point=POINT, window=7, foreground=8
    )
    assert {call[0] for call in user.mock_calls} == {
        "LoadImageW",
        "GetCursorInfo",
        "WindowFromPoint",
        "GetForegroundWindow",
    }


@pytest.mark.parametrize(
    "control", [KeyboardInterrupt(), SystemExit(9), CancelledError()]
)
def test_callback_control_propagates_after_independent_cleanup(
    monkeypatch: pytest.MonkeyPatch, control: BaseException
) -> None:
    """Qt does not swallow signals, and cleanup failures cannot replace them."""
    pipe = Pipe()
    service, native = service_and_native()
    native.pointer.side_effect = control
    source, app, timer = Mock(), Mock(), Mock()
    service.cancel.side_effect = OSError("cleanup-private")
    baseline = owner.Baseline(Mock(return_value=source), Mock(return_value=service))
    monkeypatch.setattr("PySide6.QtWidgets.QApplication", Mock(return_value=app))
    monkeypatch.setattr("PySide6.QtCore.QTimer", Mock(return_value=timer))
    monkeypatch.setattr(owner, "Native", Mock(return_value=native))

    def event_loop() -> int:
        timer.timeout.connect.call_args.args[0]()
        app.quit.assert_called_once()
        return 0

    app.exec.side_effect = event_loop
    with pytest.raises(type(control)) as caught:
        owner._run_gui(pipe, NONCE, "cancel", POINT, baseline)
    assert caught.value is control
    source.close.assert_called_once()
    timer.stop.assert_called_once()
    assert not pipe.outgoing


@pytest.mark.parametrize(
    "control", [KeyboardInterrupt(), SystemExit(9), CancelledError()]
)
def test_child_control_preserved_with_pipe_retirement(
    monkeypatch: pytest.MonkeyPatch, control: BaseException
) -> None:
    """Outer control flow is never relabeled as an ordinary fixed exit failure."""
    pipe = Mock(spec=owner.Wire)
    pipe.close.side_effect = OSError("close-private")
    monkeypatch.setattr(owner, "require_hosted", Mock(side_effect=control))
    with pytest.raises(type(control)) as caught:
        owner.child(pipe, NONCE, "private-root", "cancel", POINT)
    assert caught.value is control
    pipe.close.assert_called_once()
    pipe.send_bytes.assert_not_called()


@pytest.mark.parametrize(
    ("error", "stage", "reason"),
    [
        (RuntimeError("private-provider-path"), "source", "unknown"),
        (RuntimeError("baseline-source-mismatch"), "source", "invalid-proof"),
        (owner.FailureError("action", "unavailable"), "action", "unavailable"),
        (owner.FailureError("retirement", "timeout"), "retirement", "timeout"),
    ],
)
def test_fixed_failure_receipt_contains_no_provider_text(
    monkeypatch: pytest.MonkeyPatch, error: Exception, stage: str, reason: str
) -> None:
    """Only the allowlisted first boundary/reason travels to parent diagnostics."""
    pipe = Pipe()
    monkeypatch.setattr(owner, "require_hosted", Mock(side_effect=error))
    with pytest.raises(SystemExit) as caught:
        owner.child(pipe, NONCE, "private-root", "cancel", POINT)
    assert caught.value.code == 1
    assert pipe.outgoing == [
        {
            "phase": "failed",
            "nonce": NONCE,
            "stage": stage,
            "reason": reason,
        }
    ]
    assert pipe.closed


def test_failure_reporting_preserves_control(monkeypatch: pytest.MonkeyPatch) -> None:
    """A reporting interrupt cannot mask control or prevent endpoint cleanup."""
    pipe = Mock(spec=owner.Wire)
    control = KeyboardInterrupt()
    monkeypatch.setattr(
        owner, "require_hosted", Mock(side_effect=RuntimeError("private"))
    )
    pipe.send_bytes.side_effect = control
    with pytest.raises(KeyboardInterrupt) as caught:
        owner.child(pipe, NONCE, "private-root", "cancel", POINT)
    assert caught.value is control
    pipe.close.assert_called_once()


def test_cleanup_control_takes_precedence_over_ordinary_failure() -> None:
    """Control raised during cleanup is retained after every sibling is tried."""
    control, last = KeyboardInterrupt(), Mock()

    def exercise() -> None:
        try:
            raise RuntimeError("ordinary")
        finally:
            owner._cleanup((Mock(side_effect=control), last))

    with pytest.raises(KeyboardInterrupt) as caught:
        exercise()
    assert caught.value is control
    last.assert_called_once()


def test_pipe_cleanup_failure_stays_fixed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Final endpoint cleanup cannot print a raw provider/path exception."""
    pipe = Mock(spec=owner.Wire)
    pipe.recv_bytes.return_value = json.dumps(
        {"phase": "start", "nonce": NONCE}
    ).encode()
    pipe.close.side_effect = OSError("private-close-path")
    monkeypatch.setattr(owner, "require_hosted", Mock())
    monkeypatch.setattr(owner, "load_baseline", Mock())
    monkeypatch.setattr(owner, "runtime", dict)
    monkeypatch.setattr(owner, "_run_gui", Mock())
    with pytest.raises(SystemExit) as caught:
        owner.child(pipe, NONCE, "private-root", "cancel", POINT)
    assert caught.value.code == 1
    assert json.loads(pipe.send_bytes.call_args.args[0]) == {
        "phase": "failed",
        "nonce": NONCE,
        "stage": "finish",
        "reason": "unknown",
    }
