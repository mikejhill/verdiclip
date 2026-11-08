"""Independent native item witnesses fail closed without UIA mutations."""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import Mock

import pytest
from scripts import scrolling_item_witness as witness

from verdiclip.capture.scrolling import ScrollPosition
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Rect


def _mock(value: object) -> Mock:
    assert isinstance(value, Mock)
    return value


def _item(bounds: Rect) -> Mock:
    item = Mock(spec=witness._Interface)
    item.control_type.return_value = 50007
    item.offscreen.return_value = False
    item.bounds.return_value = bounds
    return item


def _reader() -> witness.ListItemWitness:
    reader = object.__new__(witness.ListItemWitness)
    reader.target = Mock()
    reader._body = Rect(10, 45, 63, 120)
    reader._owners, reader._retained = ExitStack(), ExitStack()
    reader._anchors, reader._index, reader._deadline = [], 0, float("inf")
    reader._automation, reader._walker = Mock(), Mock()
    _mock(reader._walker.parent).return_value = reader.target._element
    _mock(reader._automation.same_element).side_effect = lambda first, last: (
        first is last
    )
    return reader


@pytest.mark.parametrize("failure", [False, True])
def test_seed_retains_only_two_lowest_visible_items_and_releases_temporaries(
    *, failure: bool
) -> None:
    """Only two QI-retained interfaces survive the bounded per-frame scan."""
    reader = _reader()
    items = [_item(Rect(15, y, 50, 20)) for y in (70, 100, 130)]
    nodes = iter([*items, None])
    temporary_releases, retained_releases = Mock(), Mock()

    def relative(_element: object, owners: ExitStack, _slot: int) -> Mock | None:
        item = next(nodes)
        if item is not None:
            owners.callback(temporary_releases)
        return item

    def retain(item: Mock, slot: int, owners: ExitStack, *_prefix: object) -> Mock:
        assert slot == 0
        if failure and item is items[-1]:
            raise OSError("QueryInterface failed")
        owners.callback(retained_releases)
        return item

    for item in items:
        item.pointer_result.side_effect = lambda slot, owners, *prefix, item=item: (
            retain(item, slot, owners, *prefix)
        )
    _mock(reader._walker.relative).side_effect = relative
    reader._confirm = Mock()
    if failure:
        with pytest.raises(OSError, match="QueryInterface failed"):
            reader._seed()
        assert not reader._anchors
    else:
        reader._seed()
        assert [anchor.element for anchor in reader._anchors] == items[-2:]
        retained_releases.assert_not_called()
    assert temporary_releases.call_count == 3
    assert reader._confirm.call_count == 2
    reader.close()
    assert retained_releases.call_count == (1 if failure else 2)


@pytest.mark.parametrize("failure", [False, True])
def test_witness_resources_close_before_target_apartment(
    monkeypatch: pytest.MonkeyPatch, *, failure: bool
) -> None:
    """Normal and failed construction release all owned COM refs before teardown."""
    events: list[str] = []
    area = {"x": 10, "y": 20, "width": 80, "height": 145}
    header = {"x": 10, "y": 20, "width": 63, "height": 25}
    bar = {"x": 73, "y": 20, "width": 17, "height": 145}
    geometry: dict[str, object] = {
        "captured_bounds": area,
        "uia": {
            "truncated": False,
            "nodes": [
                {"parent": None, "control_type": 50008},
                {
                    "parent": 0,
                    "offscreen": False,
                    "control_type": 50034,
                    "bounds": header,
                },
                {"parent": 0, "offscreen": False, "control_type": 50014, "bounds": bar},
            ],
        },
    }
    automation, walker = Mock(), Mock()

    def interface(_pointer: object, owners: ExitStack) -> Mock:
        owners.callback(events.append, "automation")
        return automation

    def retained_walker(slot: int, owners: ExitStack) -> Mock:
        assert slot == 16
        if failure:
            raise OSError("Walker creation failed")
        owners.callback(events.append, "walker")
        return walker

    automation.pointer_result.side_effect = retained_walker
    monkeypatch.setattr(witness, "_Interface", interface)
    with ExitStack() as owners:
        owners.callback(events.append, "apartment")
        target = Mock(_owners=owners)
        if failure:
            with pytest.raises(OSError, match="Walker creation failed"):
                witness.ListItemWitness(target, geometry)
        else:
            reader = witness.ListItemWitness(target, geometry)
            reader._retained.callback(events.append, "items")
            assert reader._body == Rect(10, 45, 63, 120)
        assert events == []
    assert events == (
        ["automation", "apartment"]
        if failure
        else ["items", "walker", "automation", "apartment"]
    )


@pytest.mark.parametrize("kind", ["missing", "overflow", "ambiguous"])
def test_seed_rejects_missing_unbounded_or_ambiguous_pairs(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """No incomplete or duplicated item pair can certify displacement."""
    reader = _reader()
    first, last = _item(Rect(15, 100, 50, 20)), _item(Rect(15, 130, 50, 20))
    _mock(reader._walker.relative).side_effect = (
        [first, None] if kind == "missing" else [first, last, None]
    )
    if kind == "overflow":
        monkeypatch.setattr(witness, "_MAX_CHILDREN", 1)
    elif kind == "ambiguous":
        _mock(reader._automation.same_element).return_value = True
        _mock(reader._automation.same_element).side_effect = None
    with pytest.raises(CaptureError, match=r"bounded pair|ambiguous"):
        reader._seed()


@pytest.mark.parametrize("same", [True, False])
def test_identity_confirmation_compares_retained_with_fresh_hit_test(
    *, same: bool
) -> None:
    """A live comparison, never CompareElements(ref,ref), rejects replaced items."""
    reader = _reader()
    bounds = Rect(15, 100, 50, 20)
    retained, fresh = _item(bounds), _item(bounds)
    _mock(reader._automation.pointer_result).return_value = fresh
    _mock(reader._automation.same_element).side_effect = lambda a, b: (
        a is b or (same and a is retained and b is fresh)
    )
    if same:
        reader._confirm(retained, bounds)
    else:
        with pytest.raises(CaptureError, match="identity changed"):
            reader._confirm(retained, bounds)
    _mock(reader._automation.same_element).assert_any_call(retained, fresh)
    assert _mock(reader._automation.pointer_result).call_args.args[0] == 7


def test_hit_test_ancestor_walk_is_bounded() -> None:
    """A provider cycle never becomes an unbounded ancestry walk."""
    reader = _reader()
    text = _item(Rect(15, 100, 50, 20))
    text.control_type.return_value = 50020
    _mock(reader._automation.pointer_result).return_value = text
    _mock(reader._walker.parent).return_value = text
    with pytest.raises(CaptureError, match="no current hit-tested"):
        reader._confirm(text, text.bounds())
    assert _mock(reader._walker.parent).call_count == 8


@pytest.mark.parametrize(
    "change",
    ["none", "delta", "size", "hidden", "outside", "parent", "missing", "stale"],
)
def test_final_observation_uses_two_raw_rectangles_without_reseeding(
    change: str,
) -> None:
    """Agreeing native bounds establish movement independently of pixel stitching."""
    reader = _reader()
    reader._index = 1
    first, last = _item(Rect(15, 70, 50, 20)), _item(Rect(15, 100, 50, 20))
    reader._anchors = [
        witness._Anchor(first, Rect(15, 100, 50, 20)),
        witness._Anchor(last, Rect(15, 130, 50, 20)),
    ]
    reader._confirm, reader._seed = Mock(), Mock()
    position = ScrollPosition(0.0, 100.0, 50.0)
    _mock(reader.target.position).return_value = position
    if change == "delta":
        last.bounds.return_value = Rect(15, 101, 50, 20)
    elif change == "size":
        last.bounds.return_value = Rect(15, 100, 49, 20)
    elif change == "hidden":
        last.offscreen.return_value = True
    elif change == "outside":
        last.bounds.return_value = Rect(15, 20, 50, 20)
    elif change == "parent":
        _mock(reader._walker.parent).return_value = None
    elif change == "missing":
        reader._anchors.pop()
    elif change == "stale":
        last.bounds.side_effect = OSError("Element no longer available")
    if change == "none":
        result = reader.observe(position)
        assert result == {
            "from_frame": 0,
            "items": [
                {
                    "before": {"x": 15, "y": 100, "width": 50, "height": 20},
                    "after": {"x": 15, "y": 70, "width": 50, "height": 20},
                    "same_identity": True,
                    "same_parent": True,
                },
                {
                    "before": {"x": 15, "y": 130, "width": 50, "height": 20},
                    "after": {"x": 15, "y": 100, "width": 50, "height": 20},
                    "same_identity": True,
                    "same_parent": True,
                },
            ],
        }
        assert not reader._anchors
    else:
        with pytest.raises((CaptureError, OSError)):
            reader.observe(position)
    reader._seed.assert_not_called()


@pytest.mark.parametrize("change", ["position", "budget"])
def test_witness_read_cannot_outlive_position_or_soft_budget(
    monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """Metadata must stay tied to the stable frame within the fixed read budget."""
    reader = _reader()
    reader._seed = Mock()
    position = ScrollPosition(0.0, 0.0, 50.0)
    _mock(reader.target.position).return_value = position
    if change == "position":
        _mock(reader.target.position).side_effect = [
            position,
            ScrollPosition(0.0, 1.0, 50.0),
        ]
    else:
        monkeypatch.setattr(witness.time, "monotonic", Mock(side_effect=[0.0, 2.0]))
    with pytest.raises(CaptureError, match=r"moved during|read budget"):
        reader.observe(position)
