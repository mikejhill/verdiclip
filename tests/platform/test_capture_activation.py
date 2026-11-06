"""Post-capture focus admission never manipulates the foreground or native menus."""

import ctypes
from types import SimpleNamespace

import pytest

from verdiclip.capture.input_integrity import GuiThreadInfo
from verdiclip.platform import capture_activation


@pytest.mark.parametrize(
    ("flags", "field"),
    [(2, ""), (4, ""), (8, ""), (16, ""), (0, "capture"), (0, "menu"), (0, "move")],
)
def test_native_protected_context_refuses_activation(
    monkeypatch: pytest.MonkeyPatch, flags: int, field: str
) -> None:
    """Each native interaction flag and owner blocks focus transfer."""
    user = native_user(monkeypatch, flags=flags, field=field)
    assert not capture_activation.permits_capture_activation()
    assert user.reads == 2


def native_user(  # noqa: PLR0913 - independent native fault inputs
    monkeypatch: pytest.MonkeyPatch,
    *,
    flags: int = 0,
    field: str = "",
    success: bool = True,
    foreground: tuple[int, int] = (12, 12),
    active: int = 12,
) -> SimpleNamespace:
    """Bind only fake read APIs, with independently controlled GUI state."""
    user = SimpleNamespace(reads=0)
    reads = 0

    def get_foreground() -> int:
        nonlocal reads
        reads += 1
        user.reads = reads
        return foreground[min(reads - 1, 1)]

    def get_info(_thread: int, pointer: ctypes.c_void_p) -> bool:
        info = ctypes.cast(pointer, ctypes.POINTER(GuiThreadInfo)).contents
        info.active = active
        info.flags = flags
        if field:
            setattr(info, field, 19)
        return success

    user.GetForegroundWindow = get_foreground
    user.GetGUIThreadInfo = get_info
    monkeypatch.setattr(capture_activation, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: user, raising=False)
    return user


@pytest.mark.parametrize(
    "case", ["normal", "caret", "missing", "failed", "changed", "wrong-thread"]
)
def test_native_query_and_foreground_consistency(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """Unavailable, changed or mismatched foreground state is never admitted."""
    native_user(
        monkeypatch,
        flags=1 if case == "caret" else 0,
        success=case != "failed",
        active=13 if case == "wrong-thread" else 12,
        foreground=(0, 0)
        if case == "missing"
        else (12, 13)
        if case == "changed"
        else (12, 12),
    )
    assert capture_activation.permits_capture_activation() is (
        case in {"normal", "caret"}
    )


def test_unavailable_native_api_is_passive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Loader/read failures cannot enable activation."""

    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError("unavailable")

    monkeypatch.setattr(capture_activation, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(ctypes, "WinDLL", fail, raising=False)
    assert not capture_activation.permits_capture_activation()
