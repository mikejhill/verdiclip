"""No native API is needed to exercise the ownership evidence contract."""

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from pytestqt.qtbot import QtBot
from tests.capture import cursor_admission as module
from tests.capture import cursor_admission_surface as native
from tests.capture.cursor_admission_policy import Admission, PreservedState
from tests.capture.cursor_admission_surface import NativeState, OwnedSurface

from verdiclip.capture.input_integrity import InputIntegrityGuard
from verdiclip.capture.input_native import Win32InputPlatform


def baseline() -> PreservedState:
    """Transient fake identities never enter a serialized native receipt."""
    return PreservedState(1, (0, 1, 2, 0, 3, 0, 0), (1, 4, -100, 200))


@pytest.mark.parametrize("field", ["foreground", "gui", "cursor"])
def test_preservation_failure_is_latched(field: str) -> None:
    """Retain strict ownership and explicitly bounded failure evidence."""
    state = baseline()
    changed = PreservedState(
        5 if field == "foreground" else state.foreground,
        (0, 1, 6, 0, 3, 0, 0) if field == "gui" else state.gui,
        (1, 4, -99, 200) if field == "cursor" else state.cursor,
    )
    case = Admission(state)
    assert not case.observe(changed, safe=True)
    assert not case.observe(state, safe=True)
    assert (
        case.verdict(broker_active=True, destroyed=True)
        == "unsupported-preservation-or-cleanup"
    )


@pytest.mark.parametrize("owned", [False, True])
@pytest.mark.parametrize("safe", [False, True])
def test_natural_message_requires_owned_and_preserved(
    *, owned: bool, safe: bool
) -> None:
    """Retain strict ownership and explicitly bounded failure evidence."""
    case = Admission(baseline())
    case.cursor_message(baseline(), owned=owned, safe=safe)
    assert case.messages == int(owned and safe)
    assert case.failed is not (owned and safe)


def test_message_storage_is_bounded() -> None:
    """Retain strict ownership and explicitly bounded failure evidence."""
    case = Admission(baseline())
    for _ in range(65):
        case.cursor_message(baseline(), owned=True, safe=True)
    assert case.messages == 64
    assert case.failed


@pytest.mark.parametrize(
    ("active", "destroyed"), [(False, True), (True, False), (False, False)]
)
def test_cleanup_and_broker_are_required(*, active: bool, destroyed: bool) -> None:
    """Retain strict ownership and explicitly bounded failure evidence."""
    case = Admission(baseline(), messages=1)
    assert (
        case.verdict(broker_active=active, destroyed=destroyed)
        == "unsupported-preservation-or-cleanup"
    )


def test_absent_natural_message_is_explicitly_unsupported() -> None:
    """Retain strict ownership and explicitly bounded failure evidence."""
    case = Admission(baseline())
    assert (
        case.verdict(broker_active=True, destroyed=True)
        == "unsupported-no-natural-owned-message"
    )
    case.cursor_message(baseline(), owned=True, safe=True)
    assert (
        case.verdict(broker_active=True, destroyed=True)
        == "natural-owned-message-observed"
    )


def test_surface_has_no_cursor_or_input_mutation_calls() -> None:
    """Retain strict ownership and explicitly bounded failure evidence."""
    source = Path("tests/capture/cursor_admission_surface.py").read_text()
    for forbidden in (
        ".SetCursor(",
        ".ShowCursor(",
        ".SetCapture(",
        ".AttachThreadInput(",
        ".SendInput(",
        ".SetForegroundWindow(",
    ):
        assert forbidden not in source
    assert "return 1  # Never invoke default/parent/Qt cursor handling." in source


@pytest.mark.parametrize("fault", [None, "wrong-owner", "wrong-hit", "unsafe", "read"])
def test_actual_native_callback_never_uses_default_cursor_handler(
    monkeypatch: pytest.MonkeyPatch, fault: str | None
) -> None:
    """Execute callback logic with injected OS reads, never native desktop calls."""
    admission = Admission(baseline())
    surface = object.__new__(OwnedSurface)
    surface.handle, surface.admission = 7, admission
    default: list[int] = []

    def read() -> PreservedState:
        if fault == "read":
            raise OSError("unavailable")
        return baseline()

    surface.state = cast(
        NativeState,
        SimpleNamespace(
            read=read,
            hit=lambda _state: 8 if fault == "wrong-hit" else 7,
        ),
    )
    surface.safe = lambda: fault != "unsafe"
    monkeypatch.setattr(
        surface,
        "api",
        SimpleNamespace(DefWindowProcW=lambda *_args: default.append(1)),
        raising=False,
    )
    result = surface._message(7, 0x20, 8 if fault == "wrong-owner" else 7, 1)
    assert result == 1
    assert not default
    assert admission.messages == int(fault is None)
    assert admission.failed == (fault is not None)


@pytest.mark.parametrize("fault", [None, "show", "destroy", "destroy-false"])
def test_actual_session_destroys_surface_before_broker_cleanup(
    qtbot: object, monkeypatch: pytest.MonkeyPatch, fault: str | None
) -> None:
    """Session exception paths retain independent broker retirement ownership."""
    events: list[str] = []
    callbacks: list[Callable[[bool, str], None]] = []

    class Broker:
        active = False

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.closed = SimpleNamespace(connect=callbacks.append)
            self.input_event = SimpleNamespace(connect=lambda _callback: None)

        def start(self, *_args: object) -> None:
            events.append("start")
            self.active = True

        def release(self) -> None:
            events.append("release")
            self.active = False
            cleaned = True
            callbacks[0](cleaned, "")

    class Surface:
        def __init__(self, *_args: object) -> None:
            pass

        def show(self) -> None:
            events.append("show")
            if fault == "show":
                raise OSError("show failure")

        def close(self) -> bool:
            events.append("destroy")
            if fault == "destroy":
                raise OSError("destroy failure")
            return fault != "destroy-false"

    monkeypatch.setattr(module, "NativeInputBroker", Broker)
    monkeypatch.setattr(module, "OwnedSurface", Surface)
    state = cast(
        NativeState,
        SimpleNamespace(
            read=baseline,
            hit=lambda _state: 2,
            process=lambda _handle: 10,
        ),
    )
    if fault is not None:
        error_type = RuntimeError if fault == "destroy-false" else OSError
        with pytest.raises(error_type, match=r"failure|retirement unconfirmed"):
            module.observe_once(cast(QtBot, qtbot), state, cancel_early=True)
    else:
        result = module.observe_once(cast(QtBot, qtbot), state, cancel_early=True)
        assert result["verdict"] == "unsupported-no-natural-owned-message"
        assert result["broker_cleanup_confirmed"] is True
    assert events == ["start", "show", "destroy", "release"]


@pytest.mark.parametrize(
    "fault", ["destroy-false", "still-live", "unregister-false", "destroy-error"]
)
def test_native_cleanup_failure_retains_callback_owner(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """The actual retirement method cannot free an OS-reachable ctypes callback."""
    surface = object.__new__(OwnedSurface)
    surface.handle, surface.atom = 7, 1
    surface.name, surface.module = "owned", 9
    retained = [surface]
    monkeypatch.setattr(native, "_UNRETIRED", retained)

    def destroy(_handle: int) -> bool:
        if fault == "destroy-error":
            raise OSError("destroy unavailable")
        return fault != "destroy-false"

    monkeypatch.setattr(
        surface,
        "api",
        SimpleNamespace(
            DestroyWindow=destroy,
            IsWindow=lambda _handle: fault == "still-live",
            UnregisterClassW=lambda *_args: fault != "unregister-false",
        ),
        raising=False,
    )
    if fault == "destroy-error":
        with pytest.raises(OSError, match="destroy unavailable"):
            surface.close()
    else:
        closed = surface.close()
        assert not closed
    assert retained == [surface]
    assert surface.atom == 1


def test_foreign_capture_refuses_before_constructing_owned_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing capture cannot be borrowed to establish our cursor ownership."""
    state = PreservedState(1, (0, 1, 2, 99, 3, 0, 0), (1, 4, 100, 200))

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Must not construct resources")

    monkeypatch.setattr(module, "NativeInputBroker", forbidden)
    monkeypatch.setattr(module, "OwnedSurface", forbidden)
    result = module.observe_once(
        cast(QtBot, SimpleNamespace()),
        cast(NativeState, SimpleNamespace(read=lambda: state)),
        cancel_early=False,
    )
    assert result["verdict"] == "unsupported-foreign-capture"
    assert result["surface_created"] is False


@pytest.mark.parametrize("fault", [None, "pid", "integrity", "own-level", "state"])
def test_owned_hit_cannot_mask_original_foreign_recipient(
    monkeypatch: pytest.MonkeyPatch, fault: str | None
) -> None:
    """Successful ordinary guard on our overlay does not replace foreign proof."""
    monkeypatch.setattr(Win32InputPlatform, "recipient_safe", lambda *_args: True)
    platform = object.__new__(module.GuardedPlatform)
    platform.admission = Admission(baseline())
    platform.recipients = {7: 10}
    platform.state = cast(
        NativeState,
        SimpleNamespace(
            read=lambda: (
                baseline()
                if fault != "state"
                else PreservedState(8, (), (1, 4, 100, 200))
            ),
            process=lambda _handle: 11 if fault == "pid" else 10,
        ),
    )
    platform._integrity = cast(
        InputIntegrityGuard,
        SimpleNamespace(
            _kernel=SimpleNamespace(GetCurrentProcess=lambda: 1),
            _level=lambda _handle: None if fault == "own-level" else 2,
            _permitted_process=lambda *_args: fault != "integrity",
        ),
    )
    allowed = platform.recipient_safe(1)
    assert allowed == (fault is None)


@pytest.mark.parametrize("fault", [None, "request", "stale", "closed", "owner"])
def test_menu_refresh_requires_new_ack_and_preserved_popup(fault: str | None) -> None:
    """Old snapshots cannot satisfy a post-session preservation check."""
    calls: list[object] = []

    def poll() -> int | None:
        calls.append("request")
        return None if fault == "request" else 7

    def acknowledged(sequence: int) -> bool:
        calls.append(sequence)
        return fault != "stale"

    def wait(predicate: Callable[[], bool], *, timeout: int) -> None:
        assert timeout == 1000
        if not predicate():
            raise TimeoutError("missing fresh reply")

    peer = cast(
        module.MenuPeer,
        SimpleNamespace(
            poll=poll,
            acknowledged=acknowledged,
            preservation_errors=lambda: [fault] if fault in {"closed", "owner"} else [],
        ),
    )
    bot = cast(QtBot, SimpleNamespace(waitUntil=wait))
    if fault is None:
        module.refresh_menu(bot, peer)
    else:
        with pytest.raises((RuntimeError, TimeoutError)):
            module.refresh_menu(bot, peer)
    assert calls == (["request"] if fault == "request" else ["request", 7])


def test_menu_setup_requests_snapshot_until_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fixture reports on request while TrackPopupMenuEx is still blocked."""
    events: list[object] = []
    peer = object.__new__(module.MenuPeer)
    sequence = 0

    def poll() -> int:
        nonlocal sequence
        sequence += 1
        events.append(sequence)
        return sequence

    monkeypatch.setattr(peer, "start", lambda **_kw: events.append("start"))
    monkeypatch.setattr(peer, "poll", poll)
    monkeypatch.setattr(peer, "acknowledged", lambda seq: seq == sequence)
    monkeypatch.setattr(
        peer, "preservation_errors", lambda: ["starting"] if sequence < 2 else []
    )

    def wait(predicate: Callable[[], bool], *, timeout: int) -> None:
        assert timeout == 10000
        assert not predicate()
        assert not predicate()
        assert predicate()

    module.prepare_peer(
        cast(QtBot, SimpleNamespace(waitUntil=wait)), object.__new__(NativeState), peer
    )
    assert events == ["start", 1, 2]


def test_menu_setup_failure_still_closes_owned_peer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failed request/ack cannot strand the test-owned popup child."""
    events: list[str] = []
    monkeypatch.setattr(module, "require_hosted", lambda _env: None)
    monkeypatch.setattr(
        module.DesktopSession, "current", lambda: SimpleNamespace(state=0, flags=1)
    )
    monkeypatch.setattr(module, "NativeState", SimpleNamespace)
    monkeypatch.setattr(
        module,
        "MenuPeer",
        lambda: SimpleNamespace(close=lambda: events.append("close")),
    )

    def fail(*_args: object) -> None:
        raise TimeoutError("missing acknowledgment")

    monkeypatch.setattr(module, "prepare_peer", fail)
    with pytest.raises(TimeoutError, match="acknowledgment"):
        module.run_admission(cast(QtBot, SimpleNamespace()), tmp_path, "menu")
    assert events == ["close"]
