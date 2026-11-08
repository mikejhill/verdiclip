"""The independent diagnostic oracle must detect altered or missing output rows."""

from __future__ import annotations

import ctypes as c
import ctypes.wintypes as w
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from PySide6.QtGui import QColor, QImage, QPainter
from scripts import simple_scroll_native as native
from scripts.scrolling_diagnostics import _inspect_result, _verify_restored
from scripts.scrolling_oracle import verify_sequence

from verdiclip.capture.scrolling import ScrollPosition, ScrollResult
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Point, Rect


@pytest.mark.parametrize(
    "mode", ["explorer-click", "explorer-enter", "edge-panel-enter", "edge"]
)
def test_trace_native_scroll_info_is_explorer_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str
) -> None:
    """Frame metadata reuses the opened target's probe without changing old cases."""
    frame = QImage(24, 60, QImage.Format.Format_RGB32)
    frame.fill(QColor("white"))
    target = Mock(_window=17, _input_tick=12, area=Rect(0, 0, 24, 60))
    position = ScrollPosition(0.0, 0.0, 50.0)
    target.position.return_value = position
    geometry: dict[str, object] = {"uia": {"nodes": []}}
    metadata = [{"native_handle": 17, "bar": 1, "success": True, "pos": 0}]
    witness_record = {"from_frame": None, "items": []}
    evidence = Mock(target=target, geometry=geometry)
    evidence.observe.return_value = witness_record
    read = Mock(return_value=metadata)
    monkeypatch.setattr(native.ScrollTransaction, "_frame", lambda *_: frame)
    monkeypatch.setattr(native.UiaScrollTarget, "_open", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(native, "_target_evidence", Mock(return_value=evidence))
    monkeypatch.setattr(native, "scroll_info", read)
    monkeypatch.setattr("sys.argv", ["-c", str(tmp_path), mode])

    def capture() -> None:
        native.UiaScrollTarget._open(target, Point(0, 0))
        native.ScrollTransaction._frame(Mock(_target=target), position)

    monkeypatch.setattr(native, "helper_main", capture)
    native._trace_helper()
    observation = json.loads((tmp_path / "frames.json").read_text())[0]
    assert observation["vertical"] == 0.0
    if mode.startswith("explorer-"):
        assert observation["native_scroll_info"] == metadata
        assert observation["native_item_witness"] == witness_record
        evidence.observe.assert_called_once_with(position)
        read.assert_called_once_with(target, geometry)
        assert target.position.call_count == 2
        assert target.check.call_count == 2
    else:
        assert "native_scroll_info" not in observation
        read.assert_not_called()


@pytest.mark.parametrize("when", ["before", "during"])
def test_frame_native_scroll_info_rejects_changed_position(
    monkeypatch: pytest.MonkeyPatch, when: str
) -> None:
    """Unsettled native observations cannot be attached to a prior frame."""
    position = ScrollPosition(0.0, 0.0, 50.0)
    changed = ScrollPosition(0.0, 1.0, 50.0)
    target = Mock()
    target.position.side_effect = [changed] if when == "before" else [position, changed]
    read = Mock(return_value=[])
    monkeypatch.setattr(native, "scroll_info", read)
    with pytest.raises(CaptureError, match=f"moved {when} native metadata"):
        native._frame_scroll_info(target, position, {})
    assert read.call_count == int(when == "during")


def test_explorer_frame_rejects_missing_or_different_target_evidence() -> None:
    """Metadata from another target must not be reused for a capture."""
    with pytest.raises(CaptureError, match="target was not retained"):
        native._frame_observation(
            Mock(_target=Mock()),
            ScrollPosition(0.0, 0.0, 50.0),
            Mock(target=Mock()),
            "explorer-enter",
        )


@pytest.mark.parametrize("failure", [None, "before", "read", "after"])
def test_failure_position_is_guarded_read_only_evidence(failure: str | None) -> None:
    """A diagnostic sample cannot hide failure or request another provider write."""
    target = Mock()
    observed = ScrollPosition(-1.0, 100.0, 11.2)
    target.position.return_value = observed
    if failure == "read":
        target.position.side_effect = OSError("Unavailable")
    elif failure in ("before", "after"):
        target.check.side_effect = (
            [CaptureError("Changed")]
            if failure == "before"
            else [None, CaptureError("Changed")]
        )
    selected: dict[str, object] = {}
    native._record_failure_position(target, selected)
    target.move.assert_not_called()
    assert target.position.call_count == int(failure != "before")
    assert target.check.call_count == (1 if failure in ("before", "read") else 2)
    if failure is not None:
        assert selected == {
            "final_observation_error": "Unavailable" if failure == "read" else "Changed"
        }
    else:
        assert selected == {
            "final_observed_position": {
                "horizontal": -1.0,
                "vertical": 100.0,
                "view_size": 11.2,
            }
        }


@pytest.mark.parametrize("mode", ["explorer-enter", "edge-panel-enter", "edge"])
def test_trace_failure_keeps_original_result_and_adds_only_final_observation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str
) -> None:
    """Only representative failures gain evidence in the existing target record."""
    target = Mock()
    target.position.return_value = ScrollPosition(-1.0, 100.0, 11.2)
    result = ScrollResult(None, "Original failure", "not restored: unresolved", 16)
    monkeypatch.setattr(native.ScrollTransaction, "run", lambda _: result)
    monkeypatch.setattr("sys.argv", ["-c", str(tmp_path), mode])
    results: list[ScrollResult] = []
    monkeypatch.setattr(
        native,
        "helper_main",
        lambda: results.append(native.ScrollTransaction.run(Mock(_target=target))),
    )
    native._trace_helper()
    assert results == [result]
    assert results[0] is result
    target.move.assert_not_called()
    selected = json.loads((tmp_path / "target.json").read_text())
    if mode == "edge":
        assert selected == {}
        target.position.assert_not_called()
    else:
        assert selected["final_observed_position"]["vertical"] == 100.0
        target.position.assert_called_once_with()
    assert not (tmp_path / "restoration.json").exists()


def _sequence(output: Path) -> QImage:
    source = QImage(24, 120, QImage.Format.Format_RGB32)
    for y in range(source.height()):
        for x in range(source.width()):
            source.setPixelColor(x, y, QColor(y, x, 180))
    positions = []
    for index, top in enumerate((0, 30, 60)):
        assert source.copy(0, top, 24, 60).save(str(output / f"frame-{index}.png"))
        positions.append(
            {"horizontal": 0.0, "vertical": top / 60 * 100, "view_size": 50.0}
        )
    (output / "frames.json").write_text(json.dumps(positions), encoding="utf-8")
    return source


def _header_sequence(output: Path) -> QImage:
    """Known full-width pixels, a fixed Header and independently witnessed offsets."""
    source = _sequence(output)
    positions = json.loads((output / "frames.json").read_text())
    area = {"x": 10, "y": 20, "width": 24, "height": 65}
    header = {"x": 10, "y": 20, "width": 20, "height": 5}
    nodes = [
        {"parent": None, "control_type": 50008, "offscreen": False, "bounds": area},
        {"parent": 0, "control_type": 50034, "offscreen": False, "bounds": header},
        {
            "parent": 0,
            "control_type": 50014,
            "offscreen": False,
            "bounds": {"x": 30, "y": 20, "width": 4, "height": 65},
        },
    ]
    (output / "header-geometry.json").write_text(
        json.dumps(
            {"captured_bounds": area, "uia": {"nodes": nodes, "truncated": False}}
        ),
        encoding="utf-8",
    )
    image = QImage(24, 125, QImage.Format.Format_RGB32)
    for index, top in enumerate((0, 30, 60)):
        frame = QImage(24, 65, QImage.Format.Format_RGB32)
        frame.fill(QColor(100, 100, 100))
        painter = QPainter(frame)
        painter.fillRect(20, 0, 4, 5, QColor(index * 30, 80, 160))
        painter.drawImage(0, 5, source.copy(0, top, 24, 60))
        painter.end()
        assert frame.save(str(output / f"frame-{index}.png"))
        if index == 0:
            painter = QPainter(image)
            painter.drawImage(0, 0, frame.copy(0, 0, 24, 5))
            painter.drawImage(0, 5, source)
            painter.end()
        items = (
            [
                {
                    "before": {"x": 12, "y": y, "width": 16, "height": 5},
                    "after": {"x": 12, "y": y - 30, "width": 16, "height": 5},
                    "same_identity": True,
                    "same_parent": True,
                }
                for y in (60, 75)
            ]
            if index
            else []
        )
        positions[index]["native_item_witness"] = {
            "from_frame": index - 1 if index else None,
            "items": items,
        }
    # Exact pixel evidence must not be rounded from imprecise ScrollPattern percentages.
    positions[1]["vertical"] = 49.0
    (output / "frames.json").write_text(json.dumps(positions), encoding="utf-8")
    return image


def test_header_oracle_verifies_every_pixel_row_from_raw_item_geometry(
    tmp_path: Path,
) -> None:
    """Header once, full width, first/last rows and actual offsets are independent."""
    result = verify_sequence(_header_sequence(tmp_path), tmp_path)
    assert result["oracle_verified_rows"] == 125
    assert result["oracle_frames"] == 3
    assert "retained-item" in str(result["oracle"])


@pytest.mark.parametrize(
    "change",
    [
        "pixel",
        "first_row",
        "last_row",
        "header",
        "duplicate_header",
        "truncated",
        "native_header",
        "header_geometry",
    ],
)
def test_header_oracle_rejects_corrupt_or_missing_pixels(
    tmp_path: Path, change: str
) -> None:
    """No tolerance hides altered content, omitted ends or a duplicated prefix."""
    image = _header_sequence(tmp_path)
    if change == "truncated":
        image = image.copy(0, 0, 24, 124)
    elif change == "duplicate_header":
        altered = QImage(24, 130, QImage.Format.Format_RGB32)
        painter = QPainter(altered)
        painter.drawImage(0, 0, image.copy(0, 0, 24, 5))
        painter.drawImage(0, 5, image)
        painter.end()
        image = altered
    elif change == "native_header":
        path = tmp_path / "frame-1.png"
        frame = QImage(str(path))
        frame.setPixelColor(1, 1, QColor("red"))
        assert frame.save(str(path))
    elif change == "header_geometry":
        path = tmp_path / "header-geometry.json"
        geometry = json.loads(path.read_text())
        geometry["uia"]["nodes"][1]["bounds"]["height"] = 6
        path.write_text(json.dumps(geometry), encoding="utf-8")
    else:
        y = {"pixel": 72, "first_row": 5, "last_row": 124, "header": 1}[change]
        image.setPixelColor(1, y, QColor("red"))
    with pytest.raises(RuntimeError, match="Whole-image oracle"):
        verify_sequence(image, tmp_path)


@pytest.mark.parametrize(
    "change",
    [
        "delta",
        "disagree",
        "identity",
        "parent",
        "missing",
        "pair",
        "duplicate",
        "frame",
        "invisible",
        "dimensions",
    ],
)
def test_header_oracle_rejects_invalid_independent_witnesses(
    tmp_path: Path, change: str
) -> None:
    """Both complete, live, distinct item witnesses must certify the same step."""
    image = _header_sequence(tmp_path)
    path = tmp_path / "frames.json"
    positions = json.loads(path.read_text())
    record = positions[1]["native_item_witness"]
    items = record["items"]
    if change == "delta":
        for item in items:
            item["before"]["y"] += 1
    elif change == "disagree":
        items[0]["after"]["y"] += 1
    elif change in ("identity", "parent"):
        items[0][f"same_{change}"] = False
    elif change == "missing":
        del positions[1]["native_item_witness"]
    elif change == "pair":
        items.pop()
    elif change == "duplicate":
        items[1] = items[0]
    elif change == "frame":
        record["from_frame"] = 99
    elif change == "invisible":
        items[0]["before"]["y"] = 1
    else:
        items[0]["after"]["width"] -= 1
    path.write_text(json.dumps(positions), encoding="utf-8")
    with pytest.raises(RuntimeError, match="Whole-image oracle"):
        verify_sequence(image, tmp_path)


def test_independent_oracle_verifies_every_output_row(tmp_path: Path) -> None:
    """The oracle uses source frames and provider positions, not the stitcher."""
    source = _sequence(tmp_path)
    assert verify_sequence(source, tmp_path)["oracle_verified_rows"] == 120


def test_representative_acceptance_requires_complete_oracle(tmp_path: Path) -> None:
    """The actual diagnostic acceptance path invokes the independent oracle."""
    source = _sequence(tmp_path)
    report: dict[str, object] = {}
    _inspect_result(ScrollResult(source, "", "restored", 3), tmp_path, report)
    assert report["oracle_frames"] == 3
    assert report["oracle_verified_rows"] == 120
    with pytest.raises(RuntimeError, match="every accepted frame"):
        _inspect_result(ScrollResult(source, "", "restored", 4), tmp_path, {})


@pytest.mark.parametrize(
    "change", ["none", "perimeter", "interior", "position", "other"]
)
def test_representative_restoration_separates_position_and_outline(
    tmp_path: Path, change: str
) -> None:
    """Only the measured panel fixture perimeter can differ after exact restore."""
    source = _sequence(tmp_path)
    after = source.copy(0, 0, 24, 60)
    state = {"horizontal": 0.0, "vertical": 0.0, "view_size": 50.0}
    final = state | ({"horizontal": 1.0} if change == "position" else {})
    (tmp_path / "restoration.json").write_text(
        json.dumps({"initial": state, "final": final}), encoding="utf-8"
    )
    if change in ("perimeter", "other"):
        after.setPixelColor(0, 0, QColor("red"))
    elif change == "interior":
        after.setPixelColor(8, 8, QColor("red"))
    capture = Mock(
        output=tmp_path,
        kind="edge-prose-click" if change == "other" else "edge-panel-click",
        report={},
    )
    if change in ("none", "perimeter"):
        _verify_restored(capture, after)
        assert capture.report["restored_scroll_state"] == {
            "initial": state,
            "final": final,
        }
    else:
        with pytest.raises(RuntimeError, match=r"restored|Restored"):
            _verify_restored(capture, after)


@pytest.mark.parametrize(
    "change", ["pixel", "duplicate", "truncated", "wrong_position"]
)
def test_independent_oracle_rejects_corrupt_output(tmp_path: Path, change: str) -> None:
    """Single changed pixels and lost/duplicated rows cannot become a pass."""
    source = _sequence(tmp_path)
    if change == "pixel":
        source.setPixelColor(8, 72, QColor("red"))
    elif change == "duplicate":
        for x in range(source.width()):
            source.setPixel(x, 60, source.pixel(x, 59))
    elif change == "truncated":
        source = source.copy(0, 0, 24, 119)
    else:
        path = tmp_path / "frames.json"
        positions = json.loads(path.read_text(encoding="utf-8"))
        positions[-1]["view_size"] = 52.0
        path.write_text(json.dumps(positions), encoding="utf-8")
    with pytest.raises(RuntimeError, match="Whole-image oracle"):
        verify_sequence(source, tmp_path)


@pytest.mark.parametrize("mode", ["edge", "edge-panel-enter"])
@pytest.mark.parametrize("budget", [3000, 10000])
def test_trace_retention_is_explicit_and_bounded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str, budget: int
) -> None:
    """Only representative tests retain all frames, within the existing pixel cap."""
    frame = QImage(24, 60, QImage.Format.Format_RGB32)
    frame.fill(QColor("white"))
    monkeypatch.setattr(native.ScrollTransaction, "_frame", lambda *_: frame)
    monkeypatch.setattr(native, "MAX_PIXELS", budget)
    monkeypatch.setattr("sys.argv", ["-c", str(tmp_path), mode])

    def capture() -> None:
        for percent in (0.0, 50.0, 100.0):
            native.ScrollTransaction._frame(Mock(), ScrollPosition(0.0, percent, 50.0))

    monkeypatch.setattr(native, "helper_main", capture)
    if mode == "edge-panel-enter" and budget == 3000:
        with pytest.raises(CaptureError, match="Diagnostic frame evidence"):
            native._trace_helper()
    else:
        native._trace_helper()
    expected = 3 if mode == "edge-panel-enter" and budget == 10000 else 2
    assert len(list(tmp_path.glob("*.png"))) == expected


@pytest.mark.parametrize(
    "change", ["none", "pixel", "witness", "interior", "horizontal"]
)
def test_header_endpoint_metadata_never_replaces_exact_pixel_and_identity_proof(
    tmp_path: Path, change: str
) -> None:
    """A bounded terminal metric change still needs every original exact row."""
    image = _header_sequence(tmp_path)
    path = tmp_path / "frames.json"
    positions = json.loads(path.read_text())
    positions[-1]["view_size"] = 50.5
    if change == "pixel":
        image.setPixelColor(3, 123, QColor("red"))
    elif change == "witness":
        positions[-1]["native_item_witness"]["items"][0]["same_identity"] = False
    elif change == "interior":
        positions[1]["view_size"] = 50.5
    elif change == "horizontal":
        positions[-1]["horizontal"] = 1.0
    path.write_text(json.dumps(positions), encoding="utf-8")
    if change == "none":
        assert verify_sequence(image, tmp_path)["oracle_verified_rows"] == 125
    else:
        with pytest.raises(RuntimeError, match="Whole-image oracle"):
            verify_sequence(image, tmp_path)


@pytest.mark.parametrize(
    "terminal", [0.0, -1.0, 100.0, float("nan"), float("inf"), 55.0]
)
def test_header_oracle_rejects_invalid_or_unbounded_endpoint_metrics(
    tmp_path: Path, terminal: float
) -> None:
    """The metadata exception cannot authorize invalid or widely changed extents."""
    image = _header_sequence(tmp_path)
    path = tmp_path / "frames.json"
    positions = json.loads(path.read_text())
    positions[-1]["view_size"] = terminal
    path.write_text(json.dumps(positions), encoding="utf-8")
    with pytest.raises(RuntimeError, match="terminal"):
        verify_sequence(image, tmp_path)


@pytest.mark.parametrize(("initial", "terminal"), [(51.1, 50.2), (50.2, 51.2)])
def test_header_oracle_independently_enforces_both_total_extents(
    tmp_path: Path, initial: float, terminal: float
) -> None:
    """Each image fits one total estimate but not both, despite exact raw offsets."""
    image = _header_sequence(tmp_path)
    path = tmp_path / "frames.json"
    positions = json.loads(path.read_text())
    positions[0]["view_size"] = positions[1]["view_size"] = initial
    positions[-1]["view_size"] = terminal
    path.write_text(json.dumps(positions), encoding="utf-8")
    with pytest.raises(RuntimeError, match="terminal extent"):
        verify_sequence(image, tmp_path)


def _native_restoration_target() -> Mock:
    target = Mock()
    target.position.return_value = ScrollPosition(-1.0, 50.0, 12.0)
    target._user.GetParent.return_value = 8
    target._user.GetAncestor.return_value = 9

    def pid(_handle: int, output: c.c_void_p) -> int:
        c.cast(output, c.POINTER(w.DWORD)).contents.value = 123
        return 5

    target._user.GetWindowThreadProcessId.side_effect = pid
    return target


@pytest.mark.parametrize(
    "failure", [None, "native", "identity", "position", "late_position", "input"]
)
def test_native_restoration_metadata_is_independent_and_guarded(
    monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    """Read-only initial/final evidence never calls the product restore adapter."""
    target = _native_restoration_target()
    row = {
        "native_handle": 77,
        "bar": 2,
        "success": True,
        "window_valid": True,
        "root_matches": True,
        "min": 0,
        "max": 15999,
        "page": 1787,
        "pos": 7106,
    }
    second = dict(row)
    if failure == "native":
        second["pos"] += 1
    elif failure == "identity":
        target._user.GetParent.side_effect = [8, 99]
    elif failure == "position":
        original = target.position.return_value
        target.position.side_effect = [
            original,
            original,
            original,
            ScrollPosition(-1, 51, 12),
        ]
    elif failure == "late_position":
        original = target.position.return_value
        target.position.side_effect = [original] * 4 + [ScrollPosition(-1, 51, 12)]
    elif failure == "input":
        target.check.side_effect = [None, None, CaptureError("Input changed")]
    read = Mock(side_effect=[[row], [second]])
    monkeypatch.setattr(native, "scroll_info", read)
    if failure is not None:
        with pytest.raises(CaptureError):
            native._restoration_native(target, {"independent": "geometry"})
    else:
        assert native._restoration_native(target, {"independent": "geometry"}) == [
            {**row, "parent": 8, "root": 9, "pid": 123}
        ]
        assert read.call_count == 2
    target.move.assert_not_called()
    target.scrollbar_state.assert_not_called()


@pytest.mark.parametrize("kind", ["explorer", "edge"])
def test_restoration_record_keeps_independent_native_metadata_explorer_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str
) -> None:
    """Native observations extend the diagnostic file, not the product result."""
    target = Mock()
    position = ScrollPosition(-1.0, 50.0, 12.0)
    target.position.return_value = position
    initial = {"horizontal": -1.0, "vertical": 50.0, "view_size": 12.0}
    selected: dict[str, object] = {
        "initial_position": initial,
        "initial_native": [{"pos": 7106}],
    }
    evidence = Mock(target=target, geometry={}) if kind == "explorer" else None
    read = Mock(return_value=[{"pos": 7106, "page": 1817}])
    monkeypatch.setattr(native, "_restoration_native", read)
    result = ScrollResult(QImage(), "", "restored", 17)
    native._record_terminal_position(target, result, selected, tmp_path, evidence)
    state = json.loads((tmp_path / "restoration.json").read_text())
    assert state["initial"] == state["final"] == initial
    if evidence is not None:
        assert state["initial_native"] == [{"pos": 7106}]
        assert state["final_native"] == [{"pos": 7106, "page": 1817}]
        read.assert_called_once_with(target, evidence.geometry)
    else:
        assert set(state) == {"initial", "final"}
        read.assert_not_called()
    target.move.assert_not_called()


@pytest.mark.parametrize("failure", ["target", "position", "input"])
def test_restoration_record_rejects_races_after_native_observation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str
) -> None:
    """No persisted restoration claim survives a changed final read or guard."""
    target = Mock()
    position = ScrollPosition(-1.0, 50.0, 12.0)
    target.position.return_value = position
    selected: dict[str, object] = {"initial_position": {}, "initial_native": []}
    evidence = Mock(target=target, geometry={})
    if failure == "target":
        evidence.target = Mock()
    elif failure == "position":
        target.position.side_effect = [position, ScrollPosition(-1.0, 51.0, 12.0)]
    else:
        target.check.side_effect = [None, None, CaptureError("Input changed")]
    monkeypatch.setattr(native, "_restoration_native", Mock(return_value=[]))
    with pytest.raises(CaptureError):
        native._record_terminal_position(
            target,
            ScrollResult(QImage(), "", "restored", 17),
            selected,
            tmp_path,
            evidence,
        )
    assert not (tmp_path / "restoration.json").exists()
    target.move.assert_not_called()
