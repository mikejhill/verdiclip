"""Independent native item witnesses fail closed without UIA mutations."""

from __future__ import annotations

from contextlib import ExitStack
from typing import cast
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
    reader._horizontal, reader._right_overflow = 0.0, False
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
                    "visible_before": {"x": 15, "y": 100, "width": 50, "height": 20},
                    "visible_after": {"x": 15, "y": 70, "width": 50, "height": 20},
                    "same_identity": True,
                    "same_parent": True,
                },
                {
                    "before": {"x": 15, "y": 130, "width": 50, "height": 20},
                    "after": {"x": 15, "y": 100, "width": 50, "height": 20},
                    "visible_before": {"x": 15, "y": 130, "width": 50, "height": 20},
                    "visible_after": {"x": 15, "y": 100, "width": 50, "height": 20},
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


def _footer_geometry() -> dict[str, object]:
    """Use actual measured200% raw role rectangles, not product layout output."""
    return {
        "captured_bounds": {"x": 343, "y": 282, "width": 654, "height": 425},
        "uia": {
            "truncated": False,
            "nodes": [
                {"parent": None, "control_type": 50008},
                {
                    "parent": 0,
                    "offscreen": False,
                    "control_type": 50034,
                    "bounds": {"x": 343, "y": 282, "width": 1406, "height": 52},
                },
                {
                    "parent": 0,
                    "offscreen": False,
                    "control_type": 50014,
                    "bounds": {"x": 343, "y": 673, "width": 620, "height": 34},
                },
                {
                    "parent": 0,
                    "offscreen": False,
                    "control_type": 50014,
                    "bounds": {"x": 963, "y": 282, "width": 34, "height": 391},
                },
            ],
        },
    }


def test_raw_footer_layout_clips_only_proven_header_overflow() -> None:
    """Visible Header620x52, body339px and rails partition measured654x425."""
    geometry = _footer_geometry()
    area, header, footer, rail = witness.list_layout(geometry)
    assert area == Rect(343, 282, 654, 425)
    assert header == Rect(343, 282, 620, 52)
    assert footer == Rect(343, 673, 620, 34)
    assert rail == Rect(963, 282, 34, 391)
    assert witness.header_layout(geometry) == (area, header)
    assert footer.top - header.bottom == 339


@pytest.mark.parametrize(
    "change",
    [
        "header-left",
        "header-top",
        "header-short",
        "header-height",
        "footer-left",
        "footer-width",
        "footer-bottom",
        "rail-top",
        "rail-width",
        "rail-bottom",
        "fractional",
        "missing",
        "duplicate",
        "hidden",
        "nested",
        "wrong-role",
    ],
)
def test_raw_footer_layout_rejects_incomplete_or_ambiguous_partition(
    change: str,
) -> None:
    """Neither overflow alone nor guessed toolbar geometry can certify cropping."""
    geometry = _footer_geometry()
    tree = cast("dict[str, object]", geometry["uia"])
    nodes = cast("list[dict[str, object]]", tree["nodes"])
    header, footer, rail = [
        cast("dict[str, float]", node["bounds"]) for node in nodes[1:]
    ]
    changes = {
        "header-left": (header, "x", 344),
        "header-top": (header, "y", 283),
        "header-short": (header, "width", 619),
        "header-height": (header, "height", 392),
        "footer-left": (footer, "x", 344),
        "footer-width": (footer, "width", 619),
        "footer-bottom": (footer, "height", 33),
        "rail-top": (rail, "y", 283),
        "rail-width": (rail, "width", 33),
        "rail-bottom": (rail, "height", 390),
        "fractional": (footer, "height", 33.5),
    }
    if change in changes:
        bounds, field, value = changes[change]
        bounds[field] = value
    elif change == "missing":
        nodes.pop()
    elif change == "duplicate":
        nodes.append(nodes[-1])
    elif change == "hidden":
        nodes[-1]["offscreen"] = True
    elif change == "nested":
        nodes[-1]["parent"] = 2
    else:
        nodes[-1]["control_type"] = 50000
    with pytest.raises(CaptureError):
        witness.list_layout(geometry)


def test_footer_witness_body_excludes_horizontal_scrollbar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retained displacement witnesses must be visible content above the footer."""
    automation = Mock()
    item = _item(Rect(350, 670, 50, 20))
    monkeypatch.setattr(witness, "_Interface", Mock(return_value=automation))
    target = Mock(_owners=ExitStack())
    reader = witness.ListItemWitness(target, _footer_geometry())
    assert reader._body == Rect(343, 334, 620, 339)
    reader._deadline = float("inf")
    with pytest.raises(CaptureError, match="fully visible"):
        reader._visible(item)
    reader.close()
    target._owners.close()


def _overflow_reader() -> witness.ListItemWitness:
    reader = _reader()
    reader._body = Rect(343, 334, 620, 339)
    reader._right_overflow = True
    return reader


def test_measured_overflow_seeds_raw_items_with_interior_visible_hits() -> None:
    """The five native39 raw rows yield two visible hits without altering raw bounds."""
    reader = _overflow_reader()
    items = [_item(Rect(371, y, 1350, 48)) for y in (346, 402, 458, 514, 570)]
    for item in items:
        item.pointer_result.return_value = item
    _mock(reader._walker.relative).side_effect = [*items, None]
    _mock(reader._automation.pointer_result).side_effect = items[-2:]
    reader._seed()
    assert [anchor.bounds for anchor in reader._anchors] == [
        Rect(371, 514, 1350, 48),
        Rect(371, 570, 1350, 48),
    ]
    points = [
        call.args[2][1]
        for call in _mock(reader._automation.pointer_result).call_args_list
    ]
    assert [(point.x, point.y) for point in points] == [(667, 538), (667, 594)]
    reader.close()


@pytest.mark.parametrize("horizontal", [0.0, 1.0, -1.0])
def test_overflow_capability_requires_original_zero_horizontal(
    monkeypatch: pytest.MonkeyPatch,
    horizontal: float,
) -> None:
    """Only the independently proven two-rail layout at original H=0 may clip hits."""
    monkeypatch.setattr(witness, "_Interface", Mock(return_value=Mock()))
    target = Mock(_owners=ExitStack())
    target.position.return_value = ScrollPosition(horizontal, 0, 3.7)
    reader = witness.ListItemWitness(target, _footer_geometry())
    assert reader._right_overflow is (horizontal == 0.0)
    assert (reader._visible_bounds(Rect(371, 570, 1350, 48)) is not None) is (
        horizontal == 0.0
    )
    reader.close()
    target._owners.close()


@pytest.mark.parametrize(
    "bounds",
    [
        Rect(342, 570, 1350, 48),
        Rect(371, 333, 1350, 48),
        Rect(371, 630, 1350, 48),
        Rect(963, 570, 100, 48),
        Rect(962.9, 570, 100, 48),
        Rect(962, 570, 100, 48),
        Rect(371, 570, 1350, 0),
        Rect(371, 570, 1350, 1),
    ],
)
def test_overflow_rejects_clipped_edges_empty_slivers_and_rounded_edge_hits(
    bounds: Rect,
) -> None:
    """Right clipping cannot hide vertical/left loss or produce a boundary hit."""
    reader = _overflow_reader()
    assert reader._visible_bounds(bounds) is None
    with pytest.raises(CaptureError, match="supported visible hit area"):
        reader._confirm(_item(bounds), bounds)
    _mock(reader._automation.pointer_result).assert_not_called()


def test_overflow_without_two_bar_evidence_remains_unsupported() -> None:
    """The old fully-contained witness path cannot infer horizontal cropping."""
    reader = _reader()
    assert reader._visible_bounds(Rect(15, 70, 100, 20)) is None


@pytest.mark.parametrize(
    "change", ["none", "identity", "parent", "width", "left", "height"]
)
def test_overflow_fresh_hit_requires_same_raw_geometry_and_identity(
    change: str,
) -> None:
    """Equal visible rectangles cannot conceal a changed raw item or parent."""
    reader = _overflow_reader()
    raw = Rect(371, 570, 1350, 48)
    retained, fresh = _item(raw), _item(raw)
    _mock(reader._automation.pointer_result).return_value = fresh
    _mock(reader._automation.same_element).side_effect = lambda a, b: (
        a is b or (change != "identity" and a is retained and b is fresh)
    )
    if change == "parent":
        _mock(reader._walker.parent).return_value = None
    elif change in ("width", "left", "height"):
        values = {
            "width": Rect(371, 570, 1351, 48),
            "left": Rect(372, 570, 1349, 48),
            "height": Rect(371, 570, 1350, 47),
        }
        fresh.bounds.return_value = values[change]
    if change == "none":
        reader._confirm(retained, raw)
    else:
        with pytest.raises(CaptureError):
            reader._confirm(retained, raw)


@pytest.mark.parametrize(
    "change", ["none", "horizontal", "late-horizontal", "raw-width"]
)
def test_overflow_observation_emits_raw_and_visible_but_preserves_original_h(
    change: str,
) -> None:
    """Displacement is raw vertical motion, never the clipped hit rectangle."""
    reader = _overflow_reader()
    reader._index = 1
    items = [_item(Rect(371, y, 1350, 48)) for y in (402, 458)]
    reader._anchors = [
        witness._Anchor(item, Rect(371, y, 1350, 48))
        for item, y in zip(items, (514, 570), strict=True)
    ]
    reader._confirm = Mock()
    position = ScrollPosition(0.0, 100.0, 3.7)
    _mock(reader.target.position).return_value = position
    if change == "horizontal":
        position = ScrollPosition(1.0, 100.0, 3.7)
        _mock(reader.target.position).return_value = position
    elif change == "late-horizontal":
        _mock(reader.target.position).side_effect = [
            position,
            ScrollPosition(1, 100, 3.7),
        ]
    elif change == "raw-width":
        items[0].bounds.return_value = Rect(371, 402, 1351, 48)
    if change != "none":
        with pytest.raises(CaptureError):
            reader.observe(position)
    else:
        result = reader.observe(position)
        records = result["items"]
        assert isinstance(records, list)
        assert records[0]["before"] == {"x": 371, "y": 514, "width": 1350, "height": 48}
        assert records[0]["visible_before"] == {
            "x": 371,
            "y": 514,
            "width": 592,
            "height": 48,
        }
        assert records[0]["after"] == {"x": 371, "y": 402, "width": 1350, "height": 48}
        assert records[0]["visible_after"] == {
            "x": 371,
            "y": 402,
            "width": 592,
            "height": 48,
        }


@pytest.mark.parametrize("cycle", [False, True])
def test_failed_hit_receipt_uses_only_existing_bounded_role_reads(
    *, cycle: bool
) -> None:
    """No names, identities, provider bounds or extra reads enter the receipt."""
    reader = _reader()
    bounds = Rect(15, 100, 50, 20)
    node = _item(bounds)
    node.control_type.return_value = 50020
    _mock(reader._automation.pointer_result).return_value = node
    _mock(reader._walker.parent).return_value = node if cycle else None
    with pytest.raises(CaptureError, match="no current hit-tested ListItem"):
        reader._confirm(node, bounds)
    count = 8 if cycle else 1
    assert reader.confirmation_failure == {
        "frame_index": 0,
        "selected_bounds": {"x": 15, "y": 100, "width": 50, "height": 20},
        "visible_bounds": {"x": 15, "y": 100, "width": 50, "height": 20},
        "point": {"x": 40, "y": 110},
        "hit_control_types": [50020] * count,
        "stage": "ancestor_limit" if cycle else "root_without_item",
    }
    node.control_type.assert_called()
    assert node.control_type.call_count == count
    assert _mock(reader._walker.parent).call_count == count
    _mock(reader._automation.pointer_result).assert_called_once()
    _mock(reader._automation.same_element).assert_not_called()
    node.bounds.assert_not_called()
    node.offscreen.assert_not_called()


def test_success_clears_previous_hit_failure_receipt() -> None:
    """A subsequent successful check cannot publish an obsolete failure."""
    reader = _reader()
    bounds = Rect(15, 100, 50, 20)
    item = _item(bounds)
    reader.confirmation_failure = {"stage": "old"}
    _mock(reader._automation.pointer_result).return_value = item
    reader._confirm(item, bounds)
    assert reader.confirmation_failure is None


def test_hit_provider_exception_keeps_identity_and_partial_receipt() -> None:
    """Failure recording never substitutes an exception or inspects its text."""
    reader = _reader()
    item = _item(Rect(15, 100, 50, 20))
    error = OSError("private provider detail")
    _mock(reader._automation.pointer_result).return_value = item
    item.control_type.side_effect = error
    with pytest.raises(OSError, match="private provider detail") as caught:
        reader._confirm(item, Rect(15, 100, 50, 20))
    assert caught.value is error
    assert reader.confirmation_failure is not None
    assert reader.confirmation_failure["stage"] == "control_type"
    assert reader.confirmation_failure["hit_control_types"] == []
    assert "private" not in str(reader.confirmation_failure)
    item.control_type.assert_called_once()
    _mock(reader._walker.parent).assert_not_called()


@pytest.mark.parametrize("columns", [2, 3])
@pytest.mark.parametrize("reverse", [False, True])
def test_multicolumn_seed_uses_two_freshly_hit_tested_identities(
    columns: int, *, reverse: bool
) -> None:
    """Independent evidence permits side-by-side items without trusting production."""
    reader = _reader()
    items = [
        _item(Rect(12 + column * 20, y, 18, 20))
        for y in (70, 100, 130)
        for column in range(columns)
    ]
    if reverse:
        items.reverse()
    _mock(reader._walker.relative).side_effect = [*items, None]
    releases = Mock()

    def retain(item: Mock, _slot: int, owners: ExitStack, *_args: object) -> Mock:
        owners.callback(releases)
        return item

    def hit(_slot: int, _owners: ExitStack, *args: object) -> Mock:
        argument = args[0]
        assert isinstance(argument, tuple)
        point = argument[1]
        return next(
            item
            for item in items
            if item.bounds().left < point.x < item.bounds().right
            and item.bounds().top < point.y < item.bounds().bottom
        )

    _mock(reader._automation.pointer_result).side_effect = hit
    for item in items:
        item.pointer_result.side_effect = lambda slot, owners, *args, item=item: retain(
            item, slot, owners, *args
        )
    reader._seed()
    assert len(reader._anchors) == 2
    assert all(anchor.bounds.top == 130 for anchor in reader._anchors)
    assert reader._anchors[0].element is not reader._anchors[1].element
    assert reader.confirmation_failure is None
    assert _mock(reader._automation.pointer_result).call_count == 2
    releases.assert_not_called()
    reader.close()
    assert releases.call_count == 2


@pytest.mark.parametrize("reason", ["overlap", "alias"])
def test_pair_failure_receipt_distinguishes_geometry_from_identity(reason: str) -> None:
    """Bounded numeric evidence diagnoses aliases without names or extra UIA reads."""
    reader = _reader()
    first = _item(Rect(15, 100, 50, 20))
    last = _item(Rect(15, 110 if reason == "overlap" else 130, 50, 20))
    _mock(reader._walker.relative).side_effect = [first, last, None]
    if reason == "alias":
        _mock(reader._automation.same_element).side_effect = None
        _mock(reader._automation.same_element).return_value = True
    with pytest.raises(CaptureError, match="pair is ambiguous"):
        reader._seed()
    receipt = reader.confirmation_failure
    assert receipt is not None
    assert receipt["stage"] == "pair_selection"
    assert receipt["candidate_bounds"] == [
        {"x": 15, "y": 100, "width": 50, "height": 20},
        {"x": 15, "y": 110 if reason == "overlap" else 130, "width": 50, "height": 20},
    ]
    assert receipt["comparisons"] == [
        {
            "bounds": {"x": 15, "y": 100, "width": 50, "height": 20},
            "disjoint": reason == "alias",
            "same_identity": True if reason == "alias" else None,
        }
    ]
    first.pointer_result.assert_not_called()
    last.pointer_result.assert_not_called()
    assert _mock(reader._automation.same_element).call_count == (reason == "alias")
