"""Fixed command and kernel PID boundaries without native process creation."""

import ctypes
from ctypes import wintypes
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from tests.platform.test_moving_edit_runtime import manifest

from verdiclip.platform import moving_edit_bootstrap as bootstrap
from verdiclip.platform import moving_edit_channel as channel
from verdiclip.platform.moving_edit_protocol import MovingEditLaunch


def test_fixed_command_preserves_exact_attested_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real builder has no executable/module choice beyond the manifest."""
    runtime = replace(manifest(tmp_path), executable="//fixture/share/python.exe")
    launch = MovingEditLaunch(
        "VerdiClip-moving-edit-" + "a" * 32,
        "b" * 32,
        7,
        1_000_000_000,
        31_000_000_000,
        "c" * 32,
        8,
        123,
    )
    monkeypatch.setattr(channel, "inspect_runtime", lambda: runtime)
    command = channel._build_command(runtime, launch)
    assert command.executable == runtime.executable
    assert command.directory == str(Path(runtime.executable).parent)
    assert command.arguments == bootstrap.moving_arguments(
        runtime.serialize(), launch.serialize()
    )
    monkeypatch.setattr(
        channel,
        "inspect_runtime",
        lambda: replace(runtime, versions=("other", "10.2.0")),
    )
    with pytest.raises(ValueError, match="Selected runtime differs"):
        channel._build_command(runtime, launch)


@pytest.mark.parametrize("succeeds", [True, False])
def test_kernel_pid_uses_actual_out_parameter(
    monkeypatch: pytest.MonkeyPatch, *, succeeds: bool
) -> None:
    """Exercise actual ctypes signature and native failure, not a PID seam result."""
    calls = []

    class Function:
        argtypes: object = None
        restype: object = None

        def __call__(self, descriptor: int, output: Any) -> int:  # noqa: ANN401 - ctypes byref output
            calls.append(descriptor)
            ctypes.cast(output, ctypes.POINTER(wintypes.ULONG))[0] = 8472
            return int(succeeds)

    function = Function()

    def dll(name: str, *, use_last_error: bool) -> SimpleNamespace:
        assert name == "kernel32"
        assert use_last_error is True
        return SimpleNamespace(GetNamedPipeClientProcessId=function)

    monkeypatch.setattr(channel.ctypes, "WinDLL", dll, raising=False)
    monkeypatch.setattr(channel.ctypes, "get_last_error", lambda: 123, raising=False)
    monkeypatch.setattr(
        channel.ctypes,
        "WinError",
        lambda code: OSError(code, "native PID failure"),
        raising=False,
    )
    if succeeds:
        assert channel._peer_pid(91) == 8472
    else:
        with pytest.raises(OSError, match="native PID failure") as result:
            channel._peer_pid(91)
        assert result.value.errno == 123
    assert calls == [91]
    assert function.argtypes == [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]
    assert function.restype is wintypes.BOOL
