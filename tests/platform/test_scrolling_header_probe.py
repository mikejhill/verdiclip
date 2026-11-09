"""Hosted geometry diagnostics use bounded, read-only public metadata calls."""

from __future__ import annotations

import ctypes as c
import ctypes.wintypes as w
import json
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from scripts import scrolling_header_probe as probe

from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Rect
from verdiclip.platform import scrolling as production


@pytest.mark.parametrize("success", [True, False])
def test_scroll_info_reports_native_fields_or_explicit_failure(
    monkeypatch: pytest.MonkeyPatch, *, success: bool
) -> None:
    """GetScrollInfo's mask/layout and error semantics preserve numeric evidence."""
    target = Mock()
    target._window = 17
    target._user.IsWindow.return_value = True
    target._user.GetAncestor.return_value = 17
    reset_error, last_error = Mock(), Mock(return_value=5)
    monkeypatch.setattr(probe.c, "set_last_error", reset_error, raising=False)
    monkeypatch.setattr(probe.c, "get_last_error", last_error, raising=False)

    def read(handle: int, bar: int, pointer: c._CArgObject) -> bool:
        assert (handle, bar) == (19, 2)
        info = c.cast(pointer, c.POINTER(probe._ScrollInfo)).contents
        assert info.size == c.sizeof(probe._ScrollInfo) == 28
        assert info.mask == 0x17
        info.minimum, info.maximum, info.page = -2, 15999, 1787
        info.position, info.track_position = 893, 894
        return success

    getter = Mock(side_effect=read)
    library = Mock(return_value=SimpleNamespace(GetScrollInfo=getter))
    monkeypatch.setattr(probe.c, "WinDLL", library, raising=False)
    assert probe._scroll_info(target, 19, 2) == {
        "native_handle": 19,
        "bar": 2,
        "success": success,
        "window_valid": True,
        "root_matches": True,
        "error": 0 if success else 5,
        "min": -2 if success else None,
        "max": 15999 if success else None,
        "page": 1787 if success else None,
        "pos": 893 if success else None,
        "trackpos": 894 if success else None,
    }
    assert getter.argtypes == (w.HWND, c.c_int, c.POINTER(probe._ScrollInfo))
    assert getter.restype is w.BOOL
    assert [call[0] for call in target._user.method_calls] == [
        "IsWindow",
        "GetAncestor",
    ]
    target._user.GetScrollInfo.assert_not_called()
    library.assert_called_once_with("user32", use_last_error=True)
    reset_error.assert_called_once_with(0)
    assert last_error.call_count == (0 if success else 1)


@pytest.mark.parametrize("valid", [True, False])
def test_scroll_info_rejects_dead_or_unrelated_handles(
    monkeypatch: pytest.MonkeyPatch, *, valid: bool
) -> None:
    """A stale/reused HWND is never read as the previously observed scrollbar."""
    target = Mock()
    target._window = 17
    target._user.IsWindow.return_value = valid
    target._user.GetAncestor.return_value = 99
    independent = Mock()
    monkeypatch.setattr(
        probe.c, "WinDLL", Mock(return_value=independent), raising=False
    )
    result = probe._scroll_info(target, 19, 2)
    assert result["success"] is False
    assert result["window_valid"] is valid
    assert result["root_matches"] is False
    assert result["error"] is None
    assert all(result[key] is None for key in ("min", "max", "page", "pos", "trackpos"))
    target._user.GetScrollInfo.assert_not_called()
    independent.GetScrollInfo.assert_not_called()
    assert target._user.GetAncestor.call_count == int(valid)


def test_scroll_info_only_uses_selected_and_visible_direct_scrollbar_handles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prior role metadata permits cheap reads without guessing native classes."""
    target = Mock()
    target._native_handle = 17
    scrollbar = {
        "parent": 0,
        "depth": 1,
        "control_type": 50014,
        "offscreen": False,
        "native_handle": 19,
    }
    nodes = [
        {"parent": None},
        scrollbar,
        scrollbar,
        {**scrollbar, "control_type": 50033, "native_handle": 20},
        {**scrollbar, "offscreen": True, "native_handle": 21},
        {**scrollbar, "parent": 1, "depth": 2, "native_handle": 22},
        {**scrollbar, "depth": 2, "native_handle": 23},
        {**scrollbar, "native_handle": None},
        {**scrollbar, "native_handle": 0},
    ]
    read = Mock(
        side_effect=lambda _, handle, bar: {"native_handle": handle, "bar": bar}
    )
    monkeypatch.setattr(probe, "_scroll_info", read)
    geometry: dict[str, object] = {"uia": {"nodes": nodes, "truncated": False}}
    assert probe.scroll_info(target, geometry) == [
        {"native_handle": 17, "bar": 1},
        {"native_handle": 19, "bar": 2},
    ]
    assert target.check.call_count == 2
    target._element.assert_not_called()


def test_scroll_info_has_no_native_target_without_reported_handles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing native metadata never falls back to a guessed top-level handle."""
    target = Mock()
    target._native_handle = None
    read = Mock()
    monkeypatch.setattr(probe, "_scroll_info", read)
    assert probe.scroll_info(target, {"uia": {"nodes": []}}) == []
    read.assert_not_called()


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


def test_probe_native_signature_isolated_from_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real ctypes type guards survive independent probe/product interleaving."""
    target = Mock()
    target._window = 17
    target._user.IsWindow.return_value = True
    target._user.GetAncestor.return_value = 17
    monkeypatch.setattr(probe.c, "set_last_error", Mock(), raising=False)
    monkeypatch.setattr(probe.c, "get_last_error", Mock(return_value=0), raising=False)
    calls: list[str] = []

    def product_read(
        _handle: int, _bar: int, pointer: c._Pointer[production._ScrollInfo]
    ) -> int:
        calls.append("production")
        pointer.contents.position = 7106
        return 1

    def probe_read(
        _handle: int, _bar: int, pointer: c._Pointer[probe._ScrollInfo]
    ) -> int:
        calls.append("probe")
        pointer.contents.position = 7106
        return 1

    # CFUNCTYPE provides the same typed pointer conversion on Linux and Windows;
    # no OS call or untyped pointer cast can hide incompatible structure classes.
    product_types = (w.HWND, c.c_int, c.POINTER(production._ScrollInfo))
    probe_types = (w.HWND, c.c_int, c.POINTER(probe._ScrollInfo))
    product_getter = c.CFUNCTYPE(w.BOOL, *product_types)(product_read)
    probe_getter = c.CFUNCTYPE(w.BOOL, *probe_types)(probe_read)
    target._user.GetScrollInfo = product_getter
    library = Mock(return_value=SimpleNamespace(GetScrollInfo=probe_getter))
    monkeypatch.setattr(probe.c, "WinDLL", library, raising=False)
    assert c.sizeof(production._ScrollInfo) == c.sizeof(probe._ScrollInfo)
    for _ in range(2):
        assert probe._scroll_info(target, 19, 2)["pos"] == 7106
        assert target._user.GetScrollInfo is product_getter
        assert product_getter.argtypes == product_types
        assert probe_getter.argtypes == probe_types
        assert product_getter.restype is w.BOOL
        result = production._ScrollInfo()
        assert product_getter(19, 2, c.byref(result))
        assert result.position == 7106
        with pytest.raises(c.ArgumentError, match="LP__ScrollInfo"):
            product_getter(19, 2, c.byref(probe._ScrollInfo()))
        with pytest.raises(c.ArgumentError, match="LP__ScrollInfo"):
            probe_getter(19, 2, c.byref(production._ScrollInfo()))
    assert calls == ["probe", "production", "probe", "production"]
    assert library.call_count == 2
