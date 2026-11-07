"""Parent/DPI telemetry is bounded evidence, never an origin fallback."""

from __future__ import annotations

import ctypes
from copy import deepcopy
from typing import Any, cast

import pytest
from scripts import panel_matrix_geometry as geometry


class Reader:
    """Predetermined native parent chain and DPI category fixture."""

    def __init__(self, parents: dict[int, int]) -> None:
        self.parents = parents
        self.calls: list[str] = []

    def call(self, name: str, *args: object) -> int:
        """Return only the read-only calls expected by chain traversal."""
        self.calls.append(name)
        if name == "GetThreadDpiAwarenessContext":
            return -4
        if name == "AreDpiAwarenessContextsEqual":
            return int(args[0] == args[1])
        if name == "GetParent":
            assert isinstance(args[0], int)
            return self.parents[args[0]]
        raise AssertionError(name)


def stub_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate traversal from native field reads for portable tests."""
    monkeypatch.setattr(
        geometry, "window_evidence", lambda _reader, hwnd, _manifest: {"hwnd": hwnd}
    )


def test_exact_parent_chain_never_selects_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the verified terminal root ends a complete chain."""
    stub_windows(monkeypatch)
    reader = Reader({1: 2, 2: 3})
    report = geometry.parent_geometry(reader, 1, {"root_hwnd": 3})
    assert report["parent_chain"] == [{"hwnd": 1}, {"hwnd": 2}, {"hwnd": 3}]
    assert report["origin_selected"] is False
    assert report["numeric_phase_qualified"] is False
    assert report["thread_dpi_awareness"] == "per-monitor-v2"
    assert all(not name.startswith("Set") for name in reader.calls)


@pytest.mark.parametrize("parents", [{1: 1}, {1: 0}, {i: i + 1 for i in range(1, 10)}])
def test_incomplete_cyclic_overlong_chains_reject(
    monkeypatch: pytest.MonkeyPatch, parents: dict[int, int]
) -> None:
    """No missing, repeating or excessive native ancestry can be substituted."""
    stub_windows(monkeypatch)
    reader = Reader(parents)
    with pytest.raises(RuntimeError, match="chain"):
        geometry.parent_geometry(reader, 1, {"root_hwnd": 99})
    assert reader.calls.count("GetParent") <= geometry.MAX_ANCESTORS


def test_missing_or_unknown_awareness_rejects() -> None:
    """Unknown DPI contexts are not treated as equivalent to expected DPI."""
    reader = Reader({})
    with pytest.raises(RuntimeError, match="unavailable"):
        geometry.awareness(reader, 0)
    with pytest.raises(RuntimeError, match="unknown"):
        geometry.awareness(reader, 123)


def test_wrong_root_ancestry_rejects_before_coordinates() -> None:
    """The root is verified before any diagnostic window geometry is consumed."""

    class WrongRoot:
        def call(self, name: str, *_args: object) -> int:
            assert name == "GetAncestor"
            return 99

    manifest: dict[str, Any] = {"root_hwnd": 3, "owned_process_ids": [4]}
    with pytest.raises(RuntimeError, match="owned root"):
        geometry.window_evidence(WrongRoot(), 1, manifest)


class AbiReader:
    """Write through the actual ctypes argument buffers used by the helper."""

    def __init__(
        self, failure: tuple[str, int] | None = None, *, change_root: bool = False
    ) -> None:
        self.failure = failure
        self.change_root = change_root
        self.counts: dict[str, int] = {}
        self.buffers: list[Any] = []
        self.root = 8

    def call(self, name: str, *args: object) -> int:
        """Exercise native BOOL failures separately from signed output values."""
        self.counts[name] = self.counts.get(name, 0) + 1
        if self.failure == (name, self.counts[name]):
            return 0
        if name == "GetAncestor":
            return 99 if self.change_root and self.counts[name] == 2 else self.root
        constants = {
            "GetThreadDpiAwarenessContext": -4,
            "GetWindowDpiAwarenessContext": -4,
            "GetDpiForWindow": 120,
        }
        if name in constants:
            return constants[name]
        if name == "AreDpiAwarenessContextsEqual":
            return int(args[0] == args[1])
        if name == "GetParent":
            assert isinstance(args[0], int)
            return args[0] + 1
        return self.write_buffer(name, args)

    def write_buffer(self, name: str, args: tuple[object, ...]) -> int:
        """Native pointer output writes are not mocked away by field dictionaries."""
        if name == "GetClassNameW":
            assert isinstance(args[1], ctypes.Array)
            args[1].value = "OwnedWindow"
            self.buffers.append(args[1])
            return 11
        pointer = cast("Any", args[1])
        output = pointer._obj
        self.buffers.append(output)
        if name == "GetWindowThreadProcessId":
            output.value = 4
        elif name == "GetWindowRect":
            output.left, output.top, output.right, output.bottom = -100, -80, 40, 60
        elif name == "GetClientRect":
            output.left, output.top, output.right, output.bottom = 0, 0, 140, 140
        elif name == "ClientToScreen":
            output.x -= 100
            output.y -= 80
        else:
            raise AssertionError(name)
        return 1


def test_real_window_fields_signed_and_snapshot_immutable() -> None:
    """Complete native field path preserves signed coordinates and copies values."""
    reader = AbiReader()
    manifest = {"root_hwnd": 8, "owned_process_ids": [4]}
    first = geometry.window_evidence(reader, 1, manifest)
    second = geometry.window_evidence(reader, 1, manifest)
    assert first == second
    assert first["window_rect"] == [-100, -80, 40, 60]
    assert first["client_rect"] == [0, 0, 140, 140]
    assert first["screen_client_rect"] == [-100, -80, 40, 60]
    assert first["dpi"] == 120
    assert first["dpi_awareness"] == "per-monitor-v2"
    saved = deepcopy(first)
    for buffer in reader.buffers:
        ctypes.memset(ctypes.addressof(buffer), 0, ctypes.sizeof(buffer))
    assert first == saved
    assert second == saved
    second["window_rect"][0] = 0
    assert first == saved


@pytest.mark.parametrize(
    "failure",
    [
        ("GetWindowThreadProcessId", 1),
        ("GetClassNameW", 1),
        ("GetWindowRect", 1),
        ("GetClientRect", 1),
        ("ClientToScreen", 1),
        ("ClientToScreen", 2),
        ("GetDpiForWindow", 1),
        ("GetWindowDpiAwarenessContext", 1),
    ],
)
def test_failed_native_geometry_api_rejects(failure: tuple[str, int]) -> None:
    """Each native failure is rejected before constructing an evidence record."""
    reader = AbiReader(failure)
    with pytest.raises(RuntimeError):
        geometry.window_evidence(reader, 1, {"root_hwnd": 8, "owned_process_ids": [4]})
    assert reader.counts[failure[0]] == failure[1]


def test_final_ancestry_change_rejects_complete_fields() -> None:
    """Even populated native fields cannot survive the final root check failing."""
    reader = AbiReader(change_root=True)
    with pytest.raises(RuntimeError, match="root changed"):
        geometry.window_evidence(reader, 1, {"root_hwnd": 8, "owned_process_ids": [4]})
    assert reader.counts["GetAncestor"] == 2
    assert reader.counts["GetDpiForWindow"] == 1


def test_exact_eight_node_chain_with_real_native_buffers() -> None:
    """The exact inclusive traversal limit succeeds without querying past root."""
    reader = AbiReader()
    report = geometry.parent_geometry(
        reader, 1, {"root_hwnd": 8, "owned_process_ids": [4]}
    )
    assert [node["hwnd"] for node in report["parent_chain"]] == list(range(1, 9))
    assert reader.counts["GetParent"] == 7
    assert len(report["parent_chain"]) == geometry.MAX_ANCESTORS
