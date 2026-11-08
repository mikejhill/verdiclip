"""Hosted geometry diagnostics use bounded, read-only public metadata calls."""

from __future__ import annotations

import ctypes as c
import ctypes.wintypes as w
import json
from contextlib import ExitStack
from unittest.mock import Mock

import pytest
from scripts import scrolling_header_probe as probe

from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Rect


@pytest.mark.parametrize("dpi", [96, 120, 144, 192])
def test_window_dpi_reads_selected_window_without_walking_tree(dpi: int) -> None:
    """Record the actual API value, not a derived or assumed monitor scale."""
    target = Mock()
    target._window, target._native_handle = 17, 19
    getter = target._user.GetDpiForWindow
    getter.return_value = dpi
    assert probe.window_dpi(target) == dpi
    getter.assert_called_once_with(17)
    assert getter.argtypes == (w.HWND,)
    assert getter.restype is w.UINT
    assert target.check.call_count == 2
    assert {call[0] for call in target.method_calls} == {
        "check",
        "_user.GetDpiForWindow",
    }


def test_window_dpi_rejects_zero() -> None:
    """The documented invalid-HWND result must not look like valid DPI evidence."""
    target = Mock()
    target._user.GetDpiForWindow.return_value = 0
    with pytest.raises(CaptureError, match="selected window DPI"):
        probe.window_dpi(target)


def _element(kind: int) -> Mock:
    result = Mock(spec=probe._Interface)
    result.control_type.return_value = kind
    result.bounds.return_value = Rect(178, 146, 824, 24)
    result.native_handle.return_value = 17
    return result


def test_probe_uses_only_numeric_metadata() -> None:
    """The header's reported bounds are retained, never inferred or cropped."""
    element = _element(50034)
    result = probe._node(element, 0, 1)
    assert result == {
        "parent": 0,
        "depth": 1,
        "control_type": 50034,
        "header": True,
        "header_item": False,
        "bounds": {"x": 178, "y": 146, "width": 824, "height": 24},
        "native_handle": 17,
        "offscreen": False,
    }
    assert {call[0] for call in element.method_calls} == {
        "control_type",
        "bounds",
        "native_handle",
        "call",
    }
    assert element.call.call_args.args[:2] == (38, (c.POINTER(w.BOOL),))
    json.dumps(result)


def test_probe_visits_siblings_before_descendants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Header discovery is not starved by the preceding list item's children."""
    root, item, header, text = (_element(kind) for kind in (50008, 50007, 50034, 50020))
    edges = {(root, 4): item, (item, 6): header, (item, 4): text}
    relative = Mock(side_effect=lambda _, element, __, slot: edges.get((element, slot)))
    monkeypatch.setattr(probe, "_relative", relative)
    with ExitStack() as owners:
        result = probe._tree(root, Mock(), owners)
    assert result == {
        "nodes": [
            probe._node(root, None, 0),
            probe._node(item, 0, 1),
            probe._node(header, 0, 1),
            probe._node(text, 1, 2),
        ],
        "truncated": False,
    }
    assert not any(
        call.args[1] is text and call.args[3] == 4 for call in relative.call_args_list
    )


@pytest.mark.parametrize("limit", ["nodes", "time"])
def test_probe_explicitly_marks_bounded_output(
    monkeypatch: pytest.MonkeyPatch, limit: str
) -> None:
    """Cycles and large/slow providers cannot produce an unbounded scan."""
    root = _element(50008)
    relative = Mock(return_value=root)
    monkeypatch.setattr(probe, "_relative", relative)
    if limit == "nodes":
        monkeypatch.setattr(probe, "_MAX_NODES", 3)
    else:
        monkeypatch.setattr(probe.time, "monotonic", Mock(side_effect=[0.0, 3.0]))
    with ExitStack() as owners:
        result = probe._tree(root, Mock(), owners)
    assert result["truncated"] is True
    nodes = result["nodes"]
    assert isinstance(nodes, list)
    assert len(nodes) == (3 if limit == "nodes" else 1)
    assert relative.call_count <= 3


@pytest.mark.parametrize("slot", [4, 6])
def test_nullable_walker_results_have_local_lifetime(slot: int) -> None:
    """The public child/sibling ABI is used; null does not become an interface."""
    walker, element = Mock(), Mock()
    with ExitStack() as owners:
        assert probe._relative(walker, element, owners, slot) is None
    assert walker.call.call_args.args[:3] == (
        slot,
        (c.c_void_p, c.POINTER(c.c_void_p)),
        element._pointer,
    )


def test_nonnull_walker_reference_is_owned_locally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A child reference is released by the probe stack, including on failure."""
    walker, element, child, released = Mock(), Mock(), Mock(), Mock()

    def read(_slot: int, _types: object, _element: object, out: c._CArgObject) -> None:
        c.cast(out, c.POINTER(c.c_void_p)).contents.value = 42

    def own(pointer: c.c_void_p, owners: ExitStack) -> Mock:
        assert pointer.value == 42
        owners.callback(released)
        return child

    walker.call.side_effect = read
    monkeypatch.setattr(probe, "_Interface", own)

    def inspect() -> None:
        with ExitStack() as owners:
            assert probe._relative(walker, element, owners, 4) is child
            released.assert_not_called()
            raise CaptureError("later read failed")

    with pytest.raises(CaptureError, match="later read failed"):
        inspect()
    released.assert_called_once_with()


def test_native_client_requires_success() -> None:
    """Native absence is explicit; a failed native API cannot report zero bounds."""
    target = Mock()
    target._native_handle = None
    assert probe._native_client(target) is None
    target._user.GetClientRect.assert_not_called()
    target._native_handle = 17
    target._user.GetClientRect.return_value = False
    with pytest.raises(CaptureError, match="could not read"):
        probe._native_client(target)
    target._user.GetClientRect.return_value = True
    target._user.ClientToScreen.return_value = False
    with pytest.raises(CaptureError, match="could not map"):
        probe._native_client(target)


def test_native_client_maps_both_corners() -> None:
    """A numeric public client rectangle is mapped without application guesses."""
    target = Mock()
    target._native_handle = 17

    def client(_handle: int, pointer: c._CArgObject) -> bool:
        bounds = c.cast(pointer, c.POINTER(w.RECT)).contents
        bounds.right, bounds.bottom = 824, 532
        return True

    def screen(_handle: int, pointer: c._CArgObject) -> bool:
        point = c.cast(pointer, c.POINTER(w.POINT)).contents
        point.x += 178
        point.y += 146
        return True

    target._user.GetClientRect.side_effect = client
    target._user.ClientToScreen.side_effect = screen
    assert probe._native_client(target) == {
        "native_handle": 17,
        "bounds": {"x": 178, "y": 146, "width": 824, "height": 532},
    }
