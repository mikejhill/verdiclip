"""The independent diagnostic oracle must detect altered or missing output rows."""

from __future__ import annotations

import ctypes as c
import ctypes.wintypes as w
import json
import math
from collections.abc import Callable
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest
from PySide6.QtGui import QColor, QImage, QPainter
from scripts import simple_scroll_native as native
from scripts.scrolling_diagnostics import _inspect_result, _verify_restored
from scripts.scrolling_oracle import _native_displacement, verify_sequence

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


def _footer_sequence(output: Path) -> QImage:
    """Add a full-width exact footer/corner to the independent raw-item sequence."""
    source = _header_sequence(output)
    path = output / "header-geometry.json"
    geometry = json.loads(path.read_text())
    geometry["captured_bounds"]["height"] = 69
    nodes = geometry["uia"]["nodes"]
    nodes[1]["bounds"]["width"] = 40  # Raw header extends past the viewport.
    nodes.append(
        {
            "parent": 0,
            "control_type": 50014,
            "offscreen": False,
            "bounds": {"x": 10, "y": 85, "width": 20, "height": 4},
        }
    )
    path.write_text(json.dumps(geometry))
    footer = QImage(24, 4, QImage.Format.Format_RGB32)
    for y in range(4):
        for x in range(24):
            footer.setPixelColor(x, y, QColor(40 + x, 170 + y, 80))
    for index in range(3):
        before = QImage(str(output / f"frame-{index}.png"))
        frame = QImage(24, 69, QImage.Format.Format_RGB32)
        painter = QPainter(frame)
        painter.drawImage(0, 0, before)
        painter.drawImage(0, 65, footer)
        painter.end()
        assert frame.save(str(output / f"frame-{index}.png"))
    result = QImage(24, 129, QImage.Format.Format_RGB32)
    painter = QPainter(result)
    painter.drawImage(0, 0, source)
    painter.drawImage(0, 125, footer)
    painter.end()
    return result


@pytest.mark.parametrize("normalized", [False, True])
def test_footer_oracle_covers_full_width_and_retains_footer_once(
    tmp_path: Path,
    *,
    normalized: bool,
) -> None:
    """Footer/corner rows are exact and both normalized extents count fixed bands."""
    image = _footer_sequence(tmp_path)
    if normalized:
        path = tmp_path / "frames.json"
        frames = json.loads(path.read_text())
        frames[-1]["view_size"] = 49.5
        path.write_text(json.dumps(frames))
    result = verify_sequence(image, tmp_path)
    assert result["oracle_verified_rows"] == 129
    assert result["oracle_frames"] == 3


@pytest.mark.parametrize(
    "change",
    [
        "output-footer",
        "output-corner",
        "middle-footer",
        "last-corner",
        "duplicate",
        "first-body",
        "last-body",
        "footer-witness",
    ],
)
def test_footer_oracle_rejects_one_pixel_or_displaced_band(
    tmp_path: Path,
    change: str,
) -> None:
    """Every footer/corner pixel and all moving rows remain independently proved."""
    image = _footer_sequence(tmp_path)
    if change in ("middle-footer", "last-corner"):
        index = 1 if change == "middle-footer" else 2
        path = tmp_path / f"frame-{index}.png"
        frame = QImage(str(path))
        frame.setPixelColor(
            23 if change == "last-corner" else 0, 68, QColor(255, 0, 255)
        )
        assert frame.save(str(path))
    elif change == "duplicate":
        duplicated = QImage(24, 133, QImage.Format.Format_RGB32)
        painter = QPainter(duplicated)
        painter.drawImage(0, 0, image)
        painter.drawImage(0, 129, image.copy(0, 125, 24, 4))
        painter.end()
        image = duplicated
    elif change == "footer-witness":
        path = tmp_path / "frames.json"
        positions = json.loads(path.read_text())
        positions[1]["native_item_witness"]["items"][0]["before"]["y"] = 85
        path.write_text(json.dumps(positions))
    else:
        x, y = {
            "output-footer": (0, 125),
            "output-corner": (23, 128),
            "first-body": (0, 5),
            "last-body": (0, 124),
        }[change]
        image.setPixelColor(x, y, QColor(255, 0, 255))
    with pytest.raises(RuntimeError):
        verify_sequence(image, tmp_path)


def _overflow_sequence(output: Path) -> QImage:
    """Raw right-overflow items retain exact visible intersections and raw deltas."""
    image = _footer_sequence(output)
    path = output / "frames.json"
    positions = json.loads(path.read_text())
    for position in positions:
        for item in position["native_item_witness"]["items"]:
            for phase in ("before", "after"):
                item[phase]["width"] = 100
                item[f"visible_{phase}"] = {**item[phase], "width": 18}
    path.write_text(json.dumps(positions))
    return image


def test_overflow_oracle_uses_raw_displacement_and_exact_fullwidth_pixels(
    tmp_path: Path,
) -> None:
    """Hidden columns never enter coverage; all output pixels still come from frames."""
    result = verify_sequence(_overflow_sequence(tmp_path), tmp_path)
    assert result["oracle_verified_rows"] == 129
    assert result["oracle_frames"] == 3


@pytest.mark.parametrize(
    "change",
    [
        "left",
        "top",
        "bottom",
        "empty",
        "sliver",
        "edge-hit",
        "forged-visible",
        "missing-visible",
        "raw-width",
        "raw-x",
        "raw-height",
    ],
)
def test_overflow_oracle_rejects_forged_clipping(
    tmp_path: Path,
    change: str,
) -> None:
    """Visible rectangles cannot hide forbidden clipping or altered raw shape."""
    image = _overflow_sequence(tmp_path)
    path = tmp_path / "frames.json"
    positions = json.loads(path.read_text())
    item = positions[1]["native_item_witness"]["items"][0]
    if change == "forged-visible":
        item["visible_before"]["width"] -= 1
    elif change == "missing-visible":
        del item["visible_before"]
    else:
        phase = "after" if change.startswith("raw-") else "before"
        if phase == "after":
            field = {"raw-width": "width", "raw-x": "x", "raw-height": "height"}[change]
            item[phase][field] += 1
        else:
            field, value = {
                "left": ("x", 9),
                "top": ("y", 24),
                "bottom": ("y", 84),
                "empty": ("x", 30),
                "sliver": ("x", 29.9),
                "edge-hit": ("x", 29),
            }[change]
            item[phase][field] = value
        clipped = Rect(**item[phase]).intersected(Rect(10, 25, 20, 60))
        item[f"visible_{phase}"] = {
            "x": clipped.x,
            "y": clipped.y,
            "width": clipped.width,
            "height": clipped.height,
        }
    path.write_text(json.dumps(positions))
    with pytest.raises(RuntimeError):
        verify_sequence(image, tmp_path)


@pytest.mark.parametrize(
    "change",
    [
        "identity",
        "parent",
        "overlap",
        "duplicate",
        "unequal",
        "zero",
        "negative",
    ],
)
def test_overflow_oracle_rejects_bad_identity_pair_or_raw_delta(
    tmp_path: Path,
    change: str,
) -> None:
    """Two distinct raw item witnesses must agree on positive vertical motion."""
    image = _overflow_sequence(tmp_path)
    path = tmp_path / "frames.json"
    positions = json.loads(path.read_text())
    pair = positions[1]["native_item_witness"]["items"]
    item = pair[0]
    if change in ("identity", "parent"):
        item[f"same_{change}"] = False
    elif change in ("overlap", "duplicate"):
        pair[1] = json.loads(json.dumps(item))
        if change == "overlap":
            for phase in ("before", "after", "visible_before", "visible_after"):
                pair[1][phase]["y"] += 1
    else:
        value = {"unequal": 1, "zero": 30, "negative": 31}[change]
        item["after"]["y"] += value
        item["visible_after"]["y"] += value
    path.write_text(json.dumps(positions))
    with pytest.raises(RuntimeError):
        verify_sequence(image, tmp_path)


@pytest.mark.parametrize(
    "change",
    [
        "no-footer",
        "header",
        "footer",
        "rail",
        "horizontal",
        "changed-horizontal",
        "first-row",
        "last-row",
    ],
)
def test_overflow_oracle_preserves_layout_horizontal_and_fullwidth_pixel_guards(
    tmp_path: Path,
    change: str,
) -> None:
    """Overflow evidence cannot weaken original layout, H or exact pixel coverage."""
    image = _overflow_sequence(tmp_path)
    if change in ("no-footer", "header", "footer", "rail"):
        path = tmp_path / "header-geometry.json"
        geometry = json.loads(path.read_text())
        nodes = geometry["uia"]["nodes"]
        if change == "no-footer":
            nodes.pop()
        else:
            index, field, delta = {
                "header": (1, "x", 1),
                "footer": (-1, "width", -1),
                "rail": (2, "height", -1),
            }[change]
            nodes[index]["bounds"][field] += delta
        path.write_text(json.dumps(geometry))
    elif change in ("horizontal", "changed-horizontal"):
        path = tmp_path / "frames.json"
        positions = json.loads(path.read_text())
        for position in positions if change == "horizontal" else positions[1:2]:
            position["horizontal"] = 1
        path.write_text(json.dumps(positions))
    else:
        image.setPixelColor(
            23, 5 if change == "first-row" else 124, QColor(255, 0, 255)
        )
    with pytest.raises((CaptureError, RuntimeError)):
        verify_sequence(image, tmp_path)


def _native_list_sequence(output: Path, *, interior_page: int = 492) -> QImage:
    """A complete raw sequence whose two extent estimates straddle actual height."""
    source = _overflow_sequence(output)
    geometry_path = output / "header-geometry.json"
    geometry = json.loads(geometry_path.read_text())
    geometry["captured_bounds"]["height"] = 129
    geometry["position"] = {
        "horizontal": 0.0,
        "vertical": 0.0,
        "view_size": 100 * 492 / 999,
    }
    nodes = geometry["uia"]["nodes"]
    nodes[0]["bounds"]["height"] = 129
    nodes[2]["bounds"]["height"] = 125
    nodes[-1]["bounds"]["y"] = 145
    for index, node in enumerate(nodes):
        node["depth"] = int(index > 0)
        node["native_handle"] = 17 + index
    geometry_path.write_text(json.dumps(geometry))
    path = output / "frames.json"
    positions = json.loads(path.read_text())
    vertical = {
        "bar": 2,
        "native_handle": 19,
        "success": True,
        "window_valid": True,
        "root_matches": True,
        "min": 0,
        "max": 999,
        "page": 492,
        "pos": 0,
    }
    horizontal = {**vertical, "native_handle": 20, "max": 99, "page": 20}
    initial = [
        {**row, "parent": row["native_handle"] + 100, "root": 42, "pid": 123}
        for row in (vertical, horizontal)
    ]
    (output / "target.json").write_text(
        json.dumps(
            {
                "area": geometry["captured_bounds"],
                "window": 42,
                "initial_native": initial,
                "initial_position": geometry["position"],
            }
        )
    )
    for index, position in enumerate(positions):
        frame = QImage(str(output / f"frame-{index}.png"))
        enlarged = QImage(24, 129, QImage.Format.Format_RGB32)
        painter = QPainter(enlarged)
        painter.drawImage(0, 0, frame.copy(0, 0, 24, 5))
        painter.drawImage(0, 5, frame.copy(0, 5, 24, 60).scaled(24, 120))
        painter.drawImage(0, 125, frame.copy(0, 65, 24, 4))
        painter.end()
        assert enlarged.save(str(output / f"frame-{index}.png"))
        page = (492, interior_page, 508)[index]
        native_position = (0, 254, 492)[index]
        position["view_size"] = 100 * page / 999
        position["vertical"] = 100 * native_position / (1000 - page)
        position["native_scroll_info"] = [
            {**vertical, "page": page, "pos": native_position},
            horizontal,
        ]
        if index == 2 or (index == 1 and interior_page != 492):
            position["native_scroll_info_after"] = [
                {**row, "parent": identity["parent"], "root": 42, "pid": 123}
                for row, identity in zip(
                    position["native_scroll_info"], initial, strict=True
                )
            ]
        for item in position["native_item_witness"]["items"]:
            for phase in ("before", "after", "visible_before", "visible_after"):
                item[phase]["y"] = 25 + 2 * (item[phase]["y"] - 25)
                item[phase]["height"] *= 2
    path.write_text(json.dumps(positions))
    image = QImage(24, 249, QImage.Format.Format_RGB32)
    painter = QPainter(image)
    painter.drawImage(0, 0, source.copy(0, 0, 24, 5))
    painter.drawImage(0, 5, source.copy(0, 5, 24, 120).scaled(24, 240))
    painter.drawImage(0, 245, source.copy(0, 125, 24, 4))
    painter.end()
    return image


def _native_page_changes(output: Path, pages: tuple[int, ...]) -> QImage:
    """Split the known complete pixels into five independently witnessed frames."""
    image = _native_list_sequence(output)
    path = output / "frames.json"
    original = json.loads(path.read_text())
    positions = []
    for index, (page, current) in enumerate(
        zip(pages, (0, 120, 240, 360, 492), strict=True)
    ):
        position = deepcopy(original[1] if index else original[0])
        state = position["native_scroll_info"][0]
        state.update(page=page, pos=current)
        position.update(
            view_size=100 * page / 999, vertical=100 * current / (1000 - page)
        )
        if index == 4 or (index and page != pages[index - 1]):
            repeated = deepcopy(original[-1]["native_scroll_info_after"])
            repeated[0].update(state)
            position["native_scroll_info_after"] = repeated
        witness = position["native_item_witness"]
        witness["from_frame"] = index - 1 if index else None
        for item in witness["items"]:
            for phase in ("after", "visible_after"):
                item[phase]["y"] += 30  # Two retained items both moved 30px.
        frame = QImage(24, 129, QImage.Format.Format_RGB32)
        painter = QPainter(frame)
        painter.drawImage(0, 0, image.copy(0, 0, 24, 5))
        painter.drawImage(0, 5, image.copy(0, 5 + index * 30, 24, 120))
        painter.drawImage(0, 125, image.copy(0, 245, 24, 4))
        painter.end()
        assert frame.save(str(output / f"frame-{index}.png"))
        positions.append(position)
    path.write_text(json.dumps(positions))
    return image


@pytest.mark.parametrize(
    "pages",
    [(492, 500, 500, 497, 508), (492, 480, 480, 492, 508), (492, 500, 480, 497, 508)],
)
def test_native_oracle_requires_fresh_proof_for_each_page_change(
    tmp_path: Path, pages: tuple[int, ...]
) -> None:
    """Increases, decreases and returns to the original page need current receipts."""
    image = _native_page_changes(tmp_path, pages)
    assert verify_sequence(image, tmp_path)["oracle_verified_rows"] == 249
    path = tmp_path / "frames.json"
    positions = json.loads(path.read_text())
    for index in range(1, len(positions)):
        if "native_scroll_info_after" not in positions[index]:
            continue
        incomplete = deepcopy(positions)
        del incomplete[index]["native_scroll_info_after"]
        path.write_text(json.dumps(incomplete))
        with pytest.raises(RuntimeError, match="native frame evidence"):
            verify_sequence(image, tmp_path)


@pytest.mark.parametrize("index", [1, 3])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("min", 1),
        ("max", 1000),
        ("page", 501),
        ("pos", 121),
        ("native_handle", 1000),
        ("parent", 120),
        ("root", 43),
        ("pid", 124),
        ("bar", 1),
        ("success", False),
        ("window_valid", False),
        ("root_matches", False),
        ("parent", None),
        ("page", True),
    ],
)
def test_native_oracle_rejects_inconsistent_page_change_repeat(
    tmp_path: Path, index: int, field: str, value: object
) -> None:
    """Every changed page independently repeats native scalars and original identity."""
    image = _native_page_changes(tmp_path, (492, 500, 500, 497, 508))
    path = tmp_path / "frames.json"
    positions = json.loads(path.read_text())
    positions[index]["native_scroll_info_after"][0][field] = value
    path.write_text(json.dumps(positions))
    with pytest.raises(RuntimeError, match="Whole-image oracle"):
        verify_sequence(image, tmp_path)


@pytest.mark.parametrize("progress", [0, -1])
def test_native_page_change_requires_native_progress(
    tmp_path: Path, progress: int
) -> None:
    """A larger percentage and matching repeat cannot conceal stalled/reversed nPos."""
    image = _native_page_changes(tmp_path, (492, 500, 500, 505, 508))
    path = tmp_path / "frames.json"
    positions = json.loads(path.read_text())
    position = positions[3]
    position["native_scroll_info"][0]["pos"] = 240 + progress
    position["native_scroll_info_after"][0]["pos"] = 240 + progress
    position["vertical"] = 100 * (240 + progress) / 495
    assert position["vertical"] > positions[2]["vertical"]
    path.write_text(json.dumps(positions))
    with pytest.raises(RuntimeError, match="native continuity"):
        verify_sequence(image, tmp_path)


def test_native_oracle_repeats_even_an_adjacent_mapped_view_change(
    tmp_path: Path,
) -> None:
    """The mapping's existing one-ULP allowance never omits a changed-view receipt."""
    image = _native_list_sequence(tmp_path)
    path = tmp_path / "frames.json"
    positions = json.loads(path.read_text())
    positions[1]["view_size"] = math.nextafter(positions[1]["view_size"], math.inf)
    path.write_text(json.dumps(positions))
    with pytest.raises(RuntimeError, match="native frame evidence"):
        verify_sequence(image, tmp_path)
    repeated = deepcopy(positions[-1]["native_scroll_info_after"])
    repeated[0].update(positions[1]["native_scroll_info"][0])
    positions[1]["native_scroll_info_after"] = repeated
    path.write_text(json.dumps(positions))
    assert verify_sequence(image, tmp_path)["oracle_verified_rows"] == 249


def test_native_oracle_never_imports_post_transaction_failure_frames(
    tmp_path: Path,
) -> None:
    """Unaccepted failure evidence is neither needed nor usable as an endpoint."""
    image = _native_list_sequence(tmp_path, interior_page=500)
    path = tmp_path / "frames.json"
    positions = json.loads(path.read_text())
    target_path = tmp_path / "target.json"
    target = json.loads(target_path.read_text())
    target["failure_observation"] = {"stable": True, "position": positions[-1]}
    target_path.write_text(json.dumps(target))
    (tmp_path / "failure-observation.json").write_text("not valid JSON")
    assert verify_sequence(image, tmp_path)["oracle_frames"] == 3
    path.write_text(json.dumps(positions[:-1]))
    with pytest.raises(RuntimeError, match="both document ends"):
        verify_sequence(image, tmp_path)


def test_native_oracle_replaces_only_the_early_estimate_comparison(
    tmp_path: Path,
) -> None:
    """Complete raw pixels/geometry place actual height within both estimate bounds."""
    image = _native_list_sequence(tmp_path)
    first, last = 9 + 120 * 999 / 492, 9 + 120 * 999 / 508
    assert abs(last - first) > first * 0.02
    assert all(
        abs(image.height() - expected) <= expected * 0.02 for expected in (first, last)
    )
    assert verify_sequence(image, tmp_path)["oracle_verified_rows"] == image.height()
    path = tmp_path / "frames.json"
    positions = json.loads(path.read_text())
    for position in positions:
        position.pop("native_scroll_info")
        position.pop("native_scroll_info_after", None)
    path.write_text(json.dumps(positions))
    geometry_path = tmp_path / "header-geometry.json"
    geometry = json.loads(geometry_path.read_text())
    for node in geometry["uia"]["nodes"]:
        node.pop("native_handle", None)
    geometry_path.write_text(json.dumps(geometry))
    (tmp_path / "target.json").unlink()
    with pytest.raises(RuntimeError, match="terminal extent"):
        verify_sequence(image, tmp_path)


@pytest.mark.parametrize("frame", [0, 1, 2])
@pytest.mark.parametrize("field", ["native_scroll_info", "native_item_witness"])
def test_native_oracle_rejects_evidence_lost_anywhere(
    tmp_path: Path,
    frame: int,
    field: str,
) -> None:
    """Missing first/interior/terminal evidence cannot downgrade a native sequence."""
    image = _native_list_sequence(tmp_path)
    path = tmp_path / "frames.json"
    positions = json.loads(path.read_text())
    del positions[frame][field]
    path.write_text(json.dumps(positions))
    with pytest.raises(RuntimeError, match="Whole-image oracle"):
        verify_sequence(image, tmp_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("min", 1),
        ("max", 1000),
        ("page", 493),
        ("pos", 255),
        ("native_handle", 1000),
        ("success", False),
        ("window_valid", False),
        ("root_matches", False),
        ("min", False),
        ("page", None),
    ],
)
@pytest.mark.parametrize("interior_page", [492, 500])
def test_native_oracle_rejects_changed_or_malformed_native_fields(
    tmp_path: Path,
    field: str,
    value: object,
    interior_page: int,
) -> None:
    """Each recorded native field must retain its identity, range and mapping."""
    image = _native_list_sequence(tmp_path, interior_page=interior_page)
    path = tmp_path / "frames.json"
    positions = json.loads(path.read_text())
    positions[1]["native_scroll_info"][0][field] = value
    path.write_text(json.dumps(positions))
    with pytest.raises(RuntimeError, match="Whole-image oracle"):
        verify_sequence(image, tmp_path)


@pytest.mark.parametrize(
    "change",
    [
        "duplicate",
        "missing",
        "extra",
        "null",
        "empty",
        "mapping",
        "horizontal",
        "horizontal-page",
        "horizontal-pos",
    ],
)
@pytest.mark.parametrize("interior_page", [492, 500])
def test_native_oracle_rejects_incomplete_or_inconsistent_native_frames(
    tmp_path: Path,
    change: str,
    interior_page: int,
) -> None:
    """A frame cannot substitute another rail or mapping or move horizontally."""
    image = _native_list_sequence(tmp_path, interior_page=interior_page)
    path = tmp_path / "frames.json"
    positions = json.loads(path.read_text())
    rows = positions[1]["native_scroll_info"]
    if change == "duplicate":
        rows.append(rows[0])
    elif change == "missing":
        rows.pop()
    elif change == "extra":
        rows.append({**rows[0], "native_handle": 1000})
    elif change in ("null", "empty"):
        positions[1]["native_scroll_info"] = None if change == "null" else []
    elif change == "mapping":
        positions[1]["vertical"] += 0.001
    elif change == "horizontal":
        positions[1]["horizontal"] = 1.0
    else:
        rows[1][change.removeprefix("horizontal-")] += 1
    path.write_text(json.dumps(positions))
    with pytest.raises(RuntimeError, match="Whole-image oracle"):
        verify_sequence(image, tmp_path)


@pytest.mark.parametrize(
    "change",
    [
        "false-endpoint",
        "missing-terminal",
        "terminal-page",
        "terminal-pos",
        "terminal-parent",
        "terminal-root",
        "terminal-pid",
        "premature-terminal",
        "initial-root",
        "initial-page",
    ],
)
@pytest.mark.parametrize("interior_page", [492, 500])
def test_native_oracle_rejects_unproved_initial_or_terminal_evidence(
    tmp_path: Path, change: str, interior_page: int
) -> None:
    """Initial identity/range and a repeated exact endpoint are mandatory."""
    image = _native_list_sequence(tmp_path, interior_page=interior_page)
    path = tmp_path / "frames.json"
    positions = json.loads(path.read_text())
    if change == "false-endpoint":
        positions[-1]["native_scroll_info"][0]["pos"] -= 1
    elif change == "missing-terminal":
        del positions[-1]["native_scroll_info_after"]
    elif change.startswith("terminal-"):
        positions[-1]["native_scroll_info_after"][0][
            change.removeprefix("terminal-")
        ] += 1
    elif change == "premature-terminal":
        positions[1]["native_scroll_info_after"] = positions[-1][
            "native_scroll_info_after"
        ]
    else:
        target_path = tmp_path / "target.json"
        target = json.loads(target_path.read_text())
        target["initial_native"][0][change.removeprefix("initial-")] += 1
        target_path.write_text(json.dumps(target))
    path.write_text(json.dumps(positions))
    with pytest.raises(RuntimeError, match="Whole-image oracle"):
        verify_sequence(image, tmp_path)


@pytest.mark.parametrize(
    "change",
    [
        "overlap",
        "header",
        "footer",
        "corner",
        "first-row",
        "last-row",
        "first-rail",
        "last-rail",
        "pair",
        "visible",
        "jump",
    ],
)
@pytest.mark.parametrize("interior_page", [492, 500])
def test_native_oracle_requires_all_pixels_and_continuous_raw_pairs(
    tmp_path: Path, change: str, interior_page: int
) -> None:
    """Any single fixed/moving pixel or a direct endpoint jump invalidates the proof."""
    image = _native_list_sequence(tmp_path, interior_page=interior_page)
    if change in ("pair", "visible", "jump"):
        path = tmp_path / "frames.json"
        positions = json.loads(path.read_text())
        if change == "pair":
            positions[1]["native_item_witness"]["items"][1]["same_identity"] = False
        elif change == "visible":
            positions[1]["native_item_witness"]["items"][0]["visible_before"][
                "width"
            ] -= 1
        else:
            positions[-1]["native_item_witness"] = {"from_frame": None, "items": []}
        path.write_text(json.dumps(positions))
    elif change in ("first-row", "last-row", "first-rail", "last-rail"):
        image.setPixelColor(
            23 if change.endswith("rail") else 0,
            5 if change.startswith("first") else 244,
            QColor("red"),
        )
    else:
        path = tmp_path / "frame-1.png"
        frame = QImage(str(path))
        x, y = {
            "overlap": (0, 10),
            "header": (0, 0),
            "footer": (0, 125),
            "corner": (23, 128),
        }[change]
        frame.setPixelColor(x, y, QColor("red"))
        assert frame.save(str(path))
    with pytest.raises(RuntimeError, match="Whole-image oracle"):
        verify_sequence(image, tmp_path)


@pytest.mark.parametrize(
    ("first_page", "last_page"), [(480, 508), (492, 520), (480, 480)]
)
@pytest.mark.parametrize("interior_page", [492, 500])
def test_native_oracle_keeps_both_actual_height_bounds(
    tmp_path: Path,
    first_page: int,
    last_page: int,
    interior_page: int,
) -> None:
    """Even globally proved pixels cannot override either original or final extent."""
    image = _native_list_sequence(tmp_path, interior_page=interior_page)
    path = tmp_path / "frames.json"
    positions = json.loads(path.read_text())
    for index, position in enumerate(positions):
        page = (first_page, 500 if interior_page == 500 else first_page, last_page)[
            index
        ]
        row = position["native_scroll_info"][0]
        row["page"] = page
        row["pos"] = 1000 - page if index == 2 else row["pos"]
        position["vertical"] = 100 * row["pos"] / (1000 - page)
        position["view_size"] = 100 * page / 999
        if "native_scroll_info_after" in position:
            position["native_scroll_info_after"][0].update(row)
    path.write_text(json.dumps(positions))
    target_path = tmp_path / "target.json"
    target = json.loads(target_path.read_text())
    target["initial_native"][0]["page"] = first_page
    target_path.write_text(json.dumps(target))
    with pytest.raises(RuntimeError, match="terminal extent"):
        verify_sequence(image, tmp_path)


@pytest.mark.parametrize("failure", [None, "read", "position", "input"])
@pytest.mark.parametrize("vertical", [52.0, 100.0])
def test_changed_or_terminal_frame_records_guarded_repeated_native_evidence(
    monkeypatch: pytest.MonkeyPatch,
    failure: str | None,
    vertical: float,
) -> None:
    """Changed/terminal frames get a fresh guarded receipt, not a restoration result."""
    target = Mock()
    position = ScrollPosition(0.0, vertical, 51.0)
    target.position.return_value = position
    evidence = Mock(target=target, geometry={})
    raw = [{"native_handle": 19, "bar": 2, "pos": 500}]
    terminal = [{**raw[0], "parent": 8, "root": 9, "pid": 123}]
    read = Mock(return_value=terminal)
    monkeypatch.setattr(native, "_frame_scroll_info", Mock(return_value=raw))
    monkeypatch.setattr(native, "_restoration_native", read)
    if failure == "read":
        read.side_effect = CaptureError("Native sample changed")
    elif failure == "position":
        target.position.return_value = ScrollPosition(0.0, 99.0, 50.0)
    elif failure == "input":
        target.check.side_effect = CaptureError("Input changed")
    if failure is not None:
        with pytest.raises(CaptureError):
            native._frame_observation(
                Mock(_target=target),
                position,
                evidence,
                "explorer-click",
                previous_view=50.0,
            )
    else:
        result = native._frame_observation(
            Mock(_target=target),
            position,
            evidence,
            "explorer-click",
            previous_view=50.0,
        )
        assert result["native_scroll_info"] == raw
        assert result["native_scroll_info_after"] == terminal
        read.assert_called_once_with(target, evidence.geometry)
        target.check.assert_called_once_with()
    target.move.assert_not_called()


@pytest.mark.parametrize("failed", [False, True])
def test_trace_repeats_only_changed_pages_and_endpoint_in_existing_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, failed: bool
) -> None:
    """One extra observation per change preserves frame/write order and outcome."""
    target = Mock(_window=17, _input_tick=12, area=Rect(0, 0, 24, 60))
    operation, evidence = Mock(_target=target), Mock(target=target, geometry={})
    frame = QImage(24, 60, QImage.Format.Format_RGB32)
    frame.fill(QColor("white"))
    calls: list[str] = []
    error, result = (
        CaptureError("Original frame failure"),
        ScrollResult(None, "Original result", "unchanged", 5),
    )
    monkeypatch.setattr(native.UiaScrollTarget, "_open", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(native, "_target_evidence", Mock(return_value=evidence))

    def acquire(_operation: object, _position: ScrollPosition) -> QImage:
        calls.append("acquire")
        if failed and calls.count("acquire") == 3:
            raise error
        return frame

    def raw(*_args: object) -> list[dict[str, object]]:
        calls.append("native")
        return [{"pos": 1}]

    def witness(_position: ScrollPosition) -> dict[str, object]:
        calls.append("witness")
        return {"from_frame": None, "items": []}

    def repeat(*_args: object) -> list[dict[str, object]]:
        calls.append("repeat")
        return [{"pos": 1, "parent": 2}]

    monkeypatch.setattr(native.ScrollTransaction, "_frame", acquire)
    monkeypatch.setattr(native, "_frame_scroll_info", raw)
    monkeypatch.setattr(native, "_restoration_native", repeat)
    monkeypatch.setattr(native, "_record_terminal_position", Mock())
    evidence.observe.side_effect = witness
    target.check.side_effect = lambda: calls.append("guard")

    def run(_operation: object) -> ScrollResult:
        for vertical, view in zip(
            (0.0, 25.0, 50.0, 75.0, 100.0), (50, 51, 51, 50, 50), strict=True
        ):
            position = ScrollPosition(0.0, vertical, view)
            target.position.return_value = position
            assert native.ScrollTransaction._frame(operation, position) is frame
            saved = json.loads((tmp_path / "frames.json").read_text())
            assert saved[-1]["view_size"] == view
            calls.append("saved")
        return result

    monkeypatch.setattr(native.ScrollTransaction, "run", run)

    def capture() -> None:
        native.UiaScrollTarget._open(target, Point(0, 0))
        assert native.ScrollTransaction.run(operation) is result

    monkeypatch.setattr(native, "helper_main", capture)
    monkeypatch.setattr("sys.argv", ["-c", str(tmp_path), "explorer-click"])
    if failed:
        with pytest.raises(CaptureError) as caught:
            native._trace_helper()
        assert caught.value is error
    else:
        native._trace_helper()
    ordinary = ["acquire", "native", "witness", "saved"]
    changed = ["acquire", "native", "witness", "repeat", "guard", "saved"]
    assert calls == ordinary + changed + (
        ["acquire"] if failed else ordinary + changed * 2
    )
    saved = json.loads((tmp_path / "frames.json").read_text())
    assert [i for i, row in enumerate(saved) if "native_scroll_info_after" in row] == (
        [1] if failed else [1, 3, 4]
    )
    target.move.assert_not_called()


@pytest.mark.parametrize(
    "change", ["ambiguous", "no-texture", "nonrail-only", "wrong-delta"]
)
@pytest.mark.parametrize("interior_page", [492, 500])
def test_native_oracle_cannot_select_pixels_using_raw_expected_delta(
    tmp_path: Path,
    change: str,
    interior_page: int,
) -> None:
    """Global uniqueness and full-width texture precede raw-pair agreement."""
    image = _native_list_sequence(tmp_path, interior_page=interior_page)
    if change == "wrong-delta":
        path = tmp_path / "frames.json"
        positions = json.loads(path.read_text())
        for item in positions[1]["native_item_witness"]["items"]:
            item["after"]["y"] += 2
            item["visible_after"]["y"] += 2
        path.write_text(json.dumps(positions))
    elif change == "nonrail-only":
        path = tmp_path / "frame-1.png"
        frame = QImage(str(path))
        for y in range(5, 125):
            frame.setPixelColor(23, y, QColor(255, 0, 255))
        assert frame.save(str(path))
    else:
        for y in range(240):
            for x in range(24):
                image.setPixelColor(
                    x, y + 5, QColor(y % 12 if change == "ambiguous" else 0, x, 90)
                )
        for index in range(3):
            path = tmp_path / f"frame-{index}.png"
            frame = QImage(str(path))
            painter = QPainter(frame)
            painter.drawImage(0, 5, image.copy(0, 5 + index * 60, 24, 120))
            painter.end()
            assert frame.save(str(path))
    with pytest.raises(RuntimeError, match="displacement"):
        verify_sequence(image, tmp_path)


@pytest.mark.parametrize("overlap", [31, 32, 33])
def test_native_pixel_oracle_preserves_the_original_band_floor(overlap: int) -> None:
    """No calibrated 64-row threshold replaces the original 32-row rule."""
    rows = [bytes((y, 2, 3, 255, 4, 5, 6, 255)) for y in range(240)]
    before, after = rows[:120], rows[120 - overlap : 240 - overlap]
    if overlap < 32:
        with pytest.raises(RuntimeError, match="unique native pixel displacement"):
            _native_displacement(before, after, 1)
    else:
        assert _native_displacement(before, after, 1) == 120 - overlap


@pytest.mark.parametrize(
    "change",
    [
        "handle",
        "duplicate-handle",
        "depth",
        "header",
        "footer",
        "rail",
        "root",
        "offscreen",
        "truncated",
    ],
)
def test_native_oracle_requires_exact_raw_role_geometry(
    tmp_path: Path, change: str
) -> None:
    """Native telemetry cannot authorize malformed or mismatched raw layout roles."""
    image = _native_list_sequence(tmp_path)
    path = tmp_path / "header-geometry.json"
    geometry = json.loads(path.read_text())
    nodes = geometry["uia"]["nodes"]
    if change == "handle":
        nodes[2]["native_handle"] += 100
    elif change == "duplicate-handle":
        nodes[-1]["native_handle"] = nodes[2]["native_handle"]
    elif change == "depth":
        nodes[2]["depth"] = 2
    elif change == "offscreen":
        nodes[0]["offscreen"] = True
    elif change == "truncated":
        geometry["uia"]["truncated"] = True
    else:
        index = {"root": 0, "header": 1, "rail": 2, "footer": -1}[change]
        nodes[index]["bounds"]["x" if change == "header" else "width"] += 1
    path.write_text(json.dumps(geometry))
    with pytest.raises((RuntimeError, CaptureError)):
        verify_sequence(image, tmp_path)


def test_native_oracle_supports_one_rail_without_inventing_a_footer(
    tmp_path: Path,
) -> None:
    """The optional-footer route needs contained raw items and the same full proof."""
    image = _native_list_sequence(tmp_path)
    geometry_path = tmp_path / "header-geometry.json"
    geometry = json.loads(geometry_path.read_text())
    nodes = geometry["uia"]["nodes"]
    nodes.pop()
    nodes[1]["bounds"]["width"] = 20
    nodes[0]["bounds"]["height"] = geometry["captured_bounds"]["height"] = 125
    geometry_path.write_text(json.dumps(geometry))
    path = tmp_path / "frames.json"
    positions = json.loads(path.read_text())
    for index, position in enumerate(positions):
        position["native_scroll_info"].pop()
        for item in position["native_item_witness"]["items"]:
            for phase in ("before", "after", "visible_before", "visible_after"):
                item[phase]["width"] = 16
        frame = QImage(str(tmp_path / f"frame-{index}.png"))
        assert frame.copy(0, 0, 24, 125).save(str(tmp_path / f"frame-{index}.png"))
    positions[-1]["native_scroll_info_after"].pop()
    path.write_text(json.dumps(positions))
    target_path = tmp_path / "target.json"
    target = json.loads(target_path.read_text())
    target["initial_native"].pop()
    target["area"]["height"] = 125
    target_path.write_text(json.dumps(target))
    assert (
        verify_sequence(image.copy(0, 0, 24, 245), tmp_path)["oracle_verified_rows"]
        == 245
    )


@pytest.mark.parametrize("field", ["min", "max", "page"])
@pytest.mark.parametrize("index", [1, 2])
def test_native_oracle_rejects_changed_ranges_even_with_matching_uia(
    tmp_path: Path,
    field: str,
    index: int,
) -> None:
    """A matching mapping alone proves neither fixed ranges nor a page change."""
    image = _native_list_sequence(tmp_path)
    path = tmp_path / "frames.json"
    positions = json.loads(path.read_text())
    position = positions[index]
    state = position["native_scroll_info"][0]
    state[field] += 1
    if index == 2:
        state["pos"] = state["max"] - state["page"] + 1
        position["native_scroll_info_after"][0].update(state)
    position["vertical"] = (
        100
        * (state["pos"] - state["min"])
        / (state["max"] - state["page"] + 1 - state["min"])
    )
    position["view_size"] = 100 * state["page"] / (state["max"] - state["min"])
    path.write_text(json.dumps(positions))
    if field == "page" and index == 2:
        assert verify_sequence(image, tmp_path)["oracle_verified_rows"] == 249
    else:
        message = "native frame evidence" if field == "page" else "native continuity"
        with pytest.raises(RuntimeError, match=message):
            verify_sequence(image, tmp_path)


@pytest.mark.parametrize("filename", ["target.json", "header-geometry.json"])
@pytest.mark.parametrize("content", [None, "null", "{}", "["])
def test_native_oracle_cannot_fall_back_after_missing_or_malformed_raw_geometry(
    tmp_path: Path,
    filename: str,
    content: str | None,
) -> None:
    """Native observations require their raw identity/layout sources throughout."""
    image = _native_list_sequence(tmp_path)
    path = tmp_path / filename
    if content is None:
        path.unlink()
    else:
        path.write_text(content)
    with pytest.raises(RuntimeError, match="Whole-image oracle"):
        verify_sequence(image, tmp_path)


@pytest.mark.parametrize("mode", ["edge", "edge-panel-enter"])
def test_non_list_endpoint_does_not_gain_native_observations(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    """The terminal native receipt is confined to the existing Explorer tracer."""
    read = Mock()
    monkeypatch.setattr(native, "_restoration_native", read)
    position = ScrollPosition(0, 100, 50)
    assert native._frame_observation(Mock(), position, None, mode) == {
        "horizontal": 0,
        "vertical": 100,
        "view_size": 50,
    }
    read.assert_not_called()


@pytest.mark.parametrize("erase_initial", [False, True])
def test_selected_h0_route_cannot_disappear_when_all_frame_telemetry_is_erased(
    tmp_path: Path,
    *,
    erase_initial: bool,
) -> None:
    """Positive raw V support and original H0 select proof even with no frame keys."""
    image = _native_list_sequence(tmp_path)
    path = tmp_path / "frames.json"
    positions = json.loads(path.read_text())
    for position in positions:
        position.pop("native_scroll_info")
        position.pop("native_scroll_info_after", None)
    path.write_text(json.dumps(positions))
    if erase_initial:
        target_path = tmp_path / "target.json"
        target = json.loads(target_path.read_text())
        del target["initial_native"]
        target_path.write_text(json.dumps(target))
    with pytest.raises(RuntimeError, match="native frame evidence"):
        verify_sequence(image, tmp_path)


@pytest.mark.parametrize("handle", [None, 0])
def test_explicit_absent_native_handle_retains_the_strict_legacy_oracle(
    tmp_path: Path,
    handle: int | None,
) -> None:
    """A measured absent HWND is unsupported; a failed read of a real HWND is not."""
    image = _native_list_sequence(tmp_path)
    path = tmp_path / "header-geometry.json"
    geometry = json.loads(path.read_text())
    geometry["uia"]["nodes"][2]["native_handle"] = handle
    path.write_text(json.dumps(geometry))
    path = tmp_path / "target.json"
    target = json.loads(path.read_text())
    target["initial_native"].pop(0)
    path.write_text(json.dumps(target))
    path = tmp_path / "frames.json"
    positions = json.loads(path.read_text())
    for position in positions:
        position["native_scroll_info"].pop(0)
        position["view_size"] = 50.0
    positions[-1]["native_scroll_info_after"].pop(0)
    path.write_text(json.dumps(positions))
    assert verify_sequence(image, tmp_path)["oracle_verified_rows"] == 249
    positions[-1]["view_size"] = 52.0
    path.write_text(json.dumps(positions))
    with pytest.raises(RuntimeError, match="terminal extent"):
        verify_sequence(image, tmp_path)


@pytest.mark.parametrize(
    "change",
    [
        "null-handle",
        "zero-handle",
        "missing-handle",
        "bool-handle",
        "negative-handle",
        "failed-native",
        "missing-original",
        "missing-probe",
    ],
)
def test_h0_support_failure_cannot_be_reclassified_as_unsupported(
    tmp_path: Path,
    change: str,
) -> None:
    """Contradictory or incomplete raw support is a failure, never a legacy switch."""
    image = _native_list_sequence(tmp_path)
    if change == "failed-native":
        path = tmp_path / "target.json"
        record = json.loads(path.read_text())
        record["initial_native"][0].update(
            success=False, min=None, max=None, page=None, pos=None
        )
    elif change == "missing-original":
        path = tmp_path / "target.json"
        record = json.loads(path.read_text())
        del record["initial_position"]
    else:
        path = tmp_path / "header-geometry.json"
        record = json.loads(path.read_text())
        if change == "missing-probe":
            del record["position"]
        elif change == "missing-handle":
            del record["uia"]["nodes"][2]["native_handle"]
        else:
            record["uia"]["nodes"][2]["native_handle"] = {
                "null-handle": None,
                "zero-handle": 0,
                "bool-handle": False,
                "negative-handle": -1,
            }[change]
    path.write_text(json.dumps(record))
    with pytest.raises(RuntimeError, match="Whole-image oracle"):
        verify_sequence(image, tmp_path)


@pytest.mark.parametrize(
    "value", [False, None, "0", float("nan"), float("inf"), -2.0, 101.0, -1.0, 1.0]
)
def test_native_selection_rejects_malformed_or_forged_frame_horizontal(
    tmp_path: Path,
    value: object,
) -> None:
    """Changing every frame's H cannot contradict the independently saved original H."""
    image = _native_list_sequence(tmp_path)
    path = tmp_path / "frames.json"
    positions = json.loads(path.read_text())
    for position in positions:
        position["horizontal"] = value
    path.write_text(json.dumps(positions))
    with pytest.raises(RuntimeError, match="horizontal"):
        verify_sequence(image, tmp_path)


@pytest.mark.parametrize(
    ("filename", "key"),
    [("target.json", "initial_position"), ("header-geometry.json", "position")],
)
@pytest.mark.parametrize("value", [-1.0, 1.0, None, False])
def test_native_selection_cross_checks_each_original_horizontal_observation(
    tmp_path: Path,
    filename: str,
    key: str,
    value: object,
) -> None:
    """Neither the probe nor the original target record may silently switch routes."""
    image = _native_list_sequence(tmp_path)
    path = tmp_path / filename
    record = json.loads(path.read_text())
    record[key]["horizontal"] = value
    path.write_text(json.dumps(record))
    with pytest.raises(RuntimeError, match="horizontal"):
        verify_sequence(image, tmp_path)


def test_hminus_one_native_telemetry_does_not_select_the_h0_model(
    tmp_path: Path,
) -> None:
    """A one-rail generic List keeps legacy coverage without a new terminal receipt."""
    image = _header_sequence(tmp_path)
    geometry_path = tmp_path / "header-geometry.json"
    geometry = json.loads(geometry_path.read_text())
    for index, node in enumerate(geometry["uia"]["nodes"]):
        node.update(native_handle=17 + index, depth=int(index > 0))
    geometry["position"] = {"horizontal": -1.0, "vertical": 0.0, "view_size": 50.0}
    geometry_path.write_text(json.dumps(geometry))
    path = tmp_path / "frames.json"
    positions = json.loads(path.read_text())
    state = {
        "native_handle": 19,
        "bar": 2,
        "success": True,
        "window_valid": True,
        "root_matches": True,
        "min": 0,
        "max": 200,
        "page": 100,
        "pos": 0,
    }
    for index, position in enumerate(positions):
        position["horizontal"] = -1.0
        position["native_scroll_info"] = [{**state, "pos": (0, 49, 101)[index]}]
    path.write_text(json.dumps(positions))
    (tmp_path / "target.json").write_text(
        json.dumps(
            {
                "initial_position": geometry["position"],
                "initial_native": [{**state, "parent": 8, "root": 9, "pid": 123}],
            }
        )
    )
    assert verify_sequence(image, tmp_path)["oracle_verified_rows"] == 125


@pytest.mark.parametrize("change", ["missing-geometry", "missing-role-handle"])
def test_erased_frame_telemetry_cannot_hide_lost_selected_route_geometry(
    tmp_path: Path,
    change: str,
) -> None:
    """Other raw support receipts still identify a partially erased native trace."""
    image = _native_list_sequence(tmp_path)
    path = tmp_path / "frames.json"
    positions = json.loads(path.read_text())
    for position in positions:
        position.pop("native_scroll_info")
        position.pop("native_scroll_info_after", None)
    path.write_text(json.dumps(positions))
    path = tmp_path / "header-geometry.json"
    if change == "missing-geometry":
        path.unlink()
    else:
        geometry = json.loads(path.read_text())
        del geometry["uia"]["nodes"][2]["native_handle"]
        path.write_text(json.dumps(geometry))
        target_path = tmp_path / "target.json"
        target = json.loads(target_path.read_text())
        del target["initial_native"]
        target_path.write_text(json.dumps(target))
    with pytest.raises(RuntimeError, match="Whole-image oracle"):
        verify_sequence(image, tmp_path)


def test_phase_timing_nested_totals_do_not_double_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inclusive spans overlap; exclusive time subtracts each direct timed child."""
    clock = iter([100, 110, 130, 160])
    monkeypatch.setattr(native.time, "perf_counter_ns", lambda: next(clock))
    timing = native._PhaseTiming()
    timing.next_frame()
    result = object()
    inner = timing.timed("target_check", lambda: result)
    assert timing.timed("list_begin", inner, phase=True)() is result
    assert timing.totals == {
        "list_begin.target_check": {
            "count": 1,
            "inclusive_ns": 20,
            "exclusive_ns": 20,
            "max_ns": 20,
            "exceptions": 0,
        },
        "list_begin": {
            "count": 1,
            "inclusive_ns": 60,
            "exclusive_ns": 40,
            "max_ns": 60,
            "exceptions": 0,
        },
    }
    assert timing.frames == [timing.totals]
    assert timing.errors == 0
    assert not timing._stack
    assert timing._phase == "outside_transaction"


@pytest.mark.parametrize("exception", [CaptureError("provider"), OSError("native")])
def test_phase_timing_counts_failure_without_changing_exception(
    monkeypatch: pytest.MonkeyPatch, exception: Exception
) -> None:
    """The original exception identity propagates after one observed invocation."""
    clock = iter([100, 170])
    monkeypatch.setattr(native.time, "perf_counter_ns", lambda: next(clock))
    timing = native._PhaseTiming()
    callback = Mock(side_effect=exception)
    with pytest.raises(type(exception)) as caught:
        timing.timed("list_finish", callback, phase=True)(3, terminal=True)
    assert caught.value is exception
    callback.assert_called_once_with(3, terminal=True)
    assert timing.totals["list_finish"] == {
        "count": 1,
        "inclusive_ns": 70,
        "exclusive_ns": 70,
        "max_ns": 70,
        "exceptions": 1,
    }
    assert timing.errors == 0
    assert not timing._stack


@pytest.mark.parametrize("when", ["start", "end"])
@pytest.mark.parametrize("fails", [False, True])
def test_phase_clock_failure_never_changes_callback_outcome(
    monkeypatch: pytest.MonkeyPatch, when: str, *, fails: bool
) -> None:
    """Even broken optional clocks cannot skip, retry or replace capture work."""
    ticks = [RuntimeError("clock")] if when == "start" else [100, RuntimeError("clock")]
    monkeypatch.setattr(native.time, "perf_counter_ns", Mock(side_effect=ticks))
    timing = native._PhaseTiming()
    original = CaptureError("Original provider failure")
    result = object()
    callback = Mock(side_effect=original) if fails else Mock(return_value=result)
    wrapped = timing.timed("frame", callback, phase=True)
    if fails:
        with pytest.raises(CaptureError) as caught:
            wrapped(7, value=9)
        assert caught.value is original
    else:
        assert wrapped(7, value=9) is result
    callback.assert_called_once_with(7, value=9)
    assert timing.errors == 1
    assert not timing._stack
    assert timing._phase == "outside_transaction"


def test_phase_frame_storage_is_bounded_without_limiting_callbacks() -> None:
    """The existing80frame cap bounds samples, never calls or capture semantics."""
    timing = native._PhaseTiming()
    callback = Mock(return_value=17)
    wrapped = timing.timed("frame", callback, phase=True)
    for _ in range(native.MAX_FRAMES + 4):
        timing.next_frame()
        assert wrapped() == 17
    assert len(timing.frames) == native.MAX_FRAMES
    assert callback.call_count == native.MAX_FRAMES + 4
    assert timing.totals["frame"]["count"] == native.MAX_FRAMES + 4
    assert sum(row["frame"]["count"] for row in timing.frames) == native.MAX_FRAMES


def test_phase_call_counts_separate_product_and_independent_tracer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Count existing tree/COM calls without another call, arguments or call rows."""
    call = Mock()
    monkeypatch.setattr(native._Interface, "call", call)
    returned = Mock()

    def relative(interface: object, _child: object, _owners: object, slot: int) -> Mock:
        native._Interface.call(interface, slot, ())  # ty: ignore[invalid-argument-type]
        return returned

    monkeypatch.setattr(native._Interface, "relative", relative)
    timing = native._PhaseTiming()
    interface = Mock()
    original_relative = native._Interface.relative
    with ExitStack() as wrappers:
        native._time_helper_calls(timing, wrappers)
        for phase in ("list_begin", "trace_witness"):
            observed = timing.timed(phase, native._Interface.relative, phase=True)
            for slot in (3, 4, 6):
                assert observed(interface, interface, wrappers, slot) is returned
    assert native._Interface.call is call
    assert native._Interface.relative is original_relative
    assert call.call_count == 6
    assert [entry.args[1] for entry in call.call_args_list] == [3, 4, 6, 3, 4, 6]
    assert timing.calls == {
        phase: {
            "interface_calls": 3,
            "parent_reads": 1,
            "first_child_reads": 1,
            "next_sibling_reads": 1,
        }
        for phase in ("list_begin", "trace_witness")
    }


@pytest.mark.parametrize("failed", [False, True])
def test_trace_timing_persists_after_exit_and_restores_every_wrapper(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, failed: bool
) -> None:
    """Success and exceptions preserve exact work and restore every patched method."""
    frame = QImage(24, 60, QImage.Format.Format_RGB32)
    frame.fill(QColor("white"))
    position = ScrollPosition(0.0, 0.0, 50.0)
    target, operation = Mock(), Mock()
    callbacks: list[str] = []
    original_error = CaptureError("Original source failure")
    result = ScrollResult(None, "Original result", "unchanged", 1)
    monkeypatch.setattr(native.ScrollTransaction, "_frame", lambda *_: frame)

    def move(*_args: object, before_write: Callable[[], None] | None = None) -> None:
        if before_write is not None:
            before_write()
        callbacks.append("write")

    monkeypatch.setattr(native.UiaScrollTarget, "move", move)
    monkeypatch.setattr(
        native.UiaScrollTarget, "check", lambda *_: callbacks.append("guard")
    )

    def run(_operation: object) -> ScrollResult:
        assert not (tmp_path / "phase-timing.json").exists()
        native.UiaScrollTarget.check(target)
        native.UiaScrollTarget.move(target, 12.5)
        assert native.ScrollTransaction._frame(operation, position) is frame
        assert not (tmp_path / "phase-timing.json").exists()
        native.UiaScrollTarget.check(target)
        if failed:
            raise original_error
        return result

    monkeypatch.setattr(native.ScrollTransaction, "run", run)
    originals = [
        (owner, name, getattr(owner, name))
        for owner, names in (
            (
                native.ScrollTransaction,
                ("_frame", "run", "_move", "_settle", "_restore"),
            ),
            (native.UiaScrollTarget, ("move", "_open", "check", "scrollbar_state")),
            (native._ListSteps, ("begin", "finish")),
            (native._Interface, ("call", "relative")),
            (native.VerticalStitch, ("append",)),
            (native.ListItemWitness, ("observe",)),
            (native, ("_frame_scroll_info",)),
        )
        for name in names
    ]
    results = []
    monkeypatch.setattr(
        native,
        "helper_main",
        lambda: results.append(native.ScrollTransaction.run(operation)),
    )
    monkeypatch.setattr("sys.argv", ["-c", str(tmp_path), "edge"])
    if failed:
        with pytest.raises(CaptureError) as caught:
            native._trace_helper()
        assert caught.value is original_error
    else:
        native._trace_helper()
        assert results == [result]
        assert results[0] is result
    assert callbacks == ["guard", "write", "guard"]
    assert all(getattr(owner, name) is original for owner, name, original in originals)
    evidence = json.loads((tmp_path / "phase-timing.json").read_text())
    assert evidence["errors"] == 0
    assert evidence["totals"]["transaction"]["exceptions"] == int(failed)
    assert evidence["totals"]["transaction.target_check"]["count"] == 2
    assert evidence["totals"]["frame"]["count"] == 1
    assert evidence["totals"]["trace_png"]["count"] == 1
    assert evidence["totals"]["trace_json"]["count"] == 1
    assert len(evidence["frames"]) == 1
    assert json.loads((tmp_path / "movement.json").read_text()) == {
        "writes": 1,
        "last_vertical": 12.5,
    }


@pytest.mark.parametrize("failed", [False, True])
def test_timing_write_failure_does_not_replace_original_outcome(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, failed: bool
) -> None:
    """Optional timing storage may fail without changing result or exception."""
    write = Path.write_text

    def fail_timing(path: Path, data: str, *args: object, **kwargs: object) -> int:
        if path.name == "phase-timing.json":
            raise OSError("Timing destination unavailable")
        return write(path, data, *args, **kwargs)  # ty: ignore[invalid-argument-type]

    monkeypatch.setattr(Path, "write_text", fail_timing)
    result = ScrollResult(None, "Original result", "unchanged", 0)
    error = CaptureError("Original source failure")
    run = Mock(side_effect=error) if failed else Mock(return_value=result)
    monkeypatch.setattr(native.ScrollTransaction, "run", run)
    results = []
    monkeypatch.setattr(
        native,
        "helper_main",
        lambda: results.append(native.ScrollTransaction.run(Mock())),
    )
    monkeypatch.setattr("sys.argv", ["-c", str(tmp_path), "edge"])
    if failed:
        with pytest.raises(CaptureError) as caught:
            native._trace_helper()
        assert caught.value is error
    else:
        native._trace_helper()
        assert results == [result]
        assert results[0] is result
    run.assert_called_once()
    assert not (tmp_path / "phase-timing.json").exists()
    assert (tmp_path / "movement.json").exists()


@pytest.fixture
def failure_snapshot(monkeypatch: pytest.MonkeyPatch) -> tuple[Mock, Mock, Mock]:
    """Fake only existing read APIs; leave failure snapshot guards real."""
    position = ScrollPosition(0.0, 94.0, 18.5)
    frame = QImage(24, 60, QImage.Format.Format_RGB32)
    frame.fill(QColor("white"))
    target = Mock(area=Rect(0, 0, 24, 60))
    target.position.return_value = position
    target.frame.return_value = frame
    evidence = Mock(target=target, geometry={"retained": True})
    evidence.observe_retained.return_value = {"retained_from_frame": 12, "items": []}
    row = {
        "native_handle": 17,
        "bar": 2,
        "parent": 8,
        "root": 9,
        "pid": 123,
        "success": True,
        "window_valid": True,
        "root_matches": True,
        "min": 0,
        "max": 999,
        "page": 185,
        "pos": 766,
    }
    read = Mock(return_value=[row])
    monkeypatch.setattr(native, "_restoration_native", read)
    monkeypatch.setattr(native.time, "monotonic", lambda: 0.0)
    return target, evidence, read


@pytest.mark.parametrize("restoration", ["restored", "not restored: unresolved"])
def test_failure_snapshot_is_unaccepted_and_preserves_failed_result(
    tmp_path: Path, failure_snapshot: tuple[Mock, Mock, Mock], restoration: str
) -> None:
    """Exact post-result evidence can never become a capture/restoration receipt."""
    target, evidence, read = failure_snapshot
    initial_native = [{**read.return_value[0], "page": 180, "pos": 0}]
    selected: dict[str, object] = {"initial_native": initial_native}
    result = ScrollResult(None, "Original view size failure", restoration, 13)
    frames = tmp_path / "frames.json"
    frames.write_text("accepted frames remain unchanged")
    native._record_terminal_position(target, result, selected, tmp_path, evidence)
    receipt = selected["failure_observation"]
    assert isinstance(receipt, dict)
    assert receipt["status"] == "stable_read_only_snapshot"
    assert receipt["accepted"] is False
    assert receipt["product_capture_qualification"] is False
    assert receipt["phase"] == "after_transaction"
    assert receipt["original_result"] == {
        "message": result.message,
        "restoration": restoration,
        "frames": 13,
        "has_image": False,
    }
    assert receipt["initial_native"] == initial_native
    assert receipt["native_before"] == receipt["native_after"] == read.return_value
    assert receipt["items_before"] == receipt["items_after"]
    assert receipt["position"] == {
        "horizontal": 0.0,
        "vertical": 94.0,
        "view_size": 18.5,
    }
    assert receipt["pixels_equal"] is True
    assert (
        QImage(str(tmp_path / "failure-observation.png")) == target.frame.return_value
    )
    assert result == ScrollResult(None, "Original view size failure", restoration, 13)
    assert frames.read_text() == "accepted frames remain unchanged"
    assert not (tmp_path / "restoration.json").exists()
    assert target.frame.call_count == read.call_count == 2
    assert evidence.observe_retained.call_count == 2
    assert all(
        call.kwargs == {"deadline": 1.0}
        for call in evidence.observe_retained.call_args_list
    )
    target.move.assert_not_called()
    evidence.observe.assert_not_called()
    evidence._seed.assert_not_called()


@pytest.mark.parametrize(
    "change",
    [
        "target",
        "input-before",
        "input-final",
        "position-final",
        "native",
        "native-owner",
        "native-unavailable",
        "items",
        "items-unavailable",
        "pixels",
        "pixel-size",
        "read-error",
        "save-error",
        "budget",
    ],
)
def test_failed_snapshot_never_labels_raced_or_unavailable_evidence_stable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_snapshot: tuple[Mock, Mock, Mock],
    change: str,
) -> None:
    """Any incomplete guard/read leaves an unaccepted receipt and original failure."""
    target, evidence, read = failure_snapshot
    selected: dict[str, object] = {"initial_native": read.return_value}
    if change == "target":
        evidence.target = Mock()
    elif change == "input-before":
        target.check.side_effect = CaptureError("Input changed")
    elif change == "input-final":
        target.check.side_effect = [None] * 16 + [CaptureError("Input changed")]
    elif change == "position-final":
        target.position.side_effect = [target.position.return_value] * 8 + [
            ScrollPosition(0.0, 95.0, 18.5)
        ]
    elif change == "budget":
        # Start and all guard entries pass; the final post-guard budget expires.
        ticks = iter([0.0] * 14 + [1.0])
        monkeypatch.setattr(native.time, "monotonic", lambda: next(ticks))
    else:
        _change_failure_reads(monkeypatch, target, evidence, read, change)
    result = ScrollResult(None, "Original failure", "restored", 13)
    native._record_terminal_position(target, result, selected, tmp_path, evidence)
    receipt = selected["failure_observation"]
    assert isinstance(receipt, dict)
    assert receipt["status"] == "unavailable"
    assert receipt["error"]
    assert receipt["accepted"] is False
    assert receipt["original_result"] == {
        "message": "Original failure",
        "restoration": "restored",
        "frames": 13,
        "has_image": False,
    }
    assert not (tmp_path / "frames.json").exists()
    assert not (tmp_path / "restoration.json").exists()
    assert target.frame.call_count <= 2
    assert read.call_count <= 2
    assert evidence.observe_retained.call_count <= 2
    target.move.assert_not_called()
    evidence.observe.assert_not_called()
    evidence._seed.assert_not_called()
    if change in ("target", "input-before"):
        target.frame.assert_not_called()
        read.assert_not_called()


def test_optional_failure_position_exception_cannot_replace_original_result(
    tmp_path: Path,
) -> None:
    """Unexpected diagnostic exceptions cannot escape even without item evidence."""
    target = Mock()
    target.check.side_effect = RuntimeError("Optional guard failed")
    result = ScrollResult(None, "Original failure", "restored", 3)
    selected: dict[str, object] = {}
    native._record_terminal_position(target, result, selected, tmp_path)
    assert selected == {"final_observation_error": "Optional guard failed"}
    assert result.message == "Original failure"
    assert list(tmp_path.iterdir()) == []
    target.move.assert_not_called()


def _change_failure_reads(
    monkeypatch: pytest.MonkeyPatch,
    target: Mock,
    evidence: Mock,
    read: Mock,
    change: str,
) -> None:
    """Perturb one optional read, without replacing the snapshot implementation."""
    if change == "native":
        read.side_effect = [read.return_value, [{**read.return_value[0], "page": 186}]]
    elif change == "native-owner":
        read.return_value = [{**read.return_value[0], "parent": 99}]
    elif change == "native-unavailable":
        read.return_value = [{**read.return_value[0], "success": False}]
    elif change == "items":
        evidence.observe_retained.side_effect = [{"items": [1]}, {"items": [2]}]
    elif change == "items-unavailable":
        evidence.observe_retained.side_effect = CaptureError("Pair no longer visible")
    elif change == "pixels":
        changed = target.frame.return_value.copy()
        changed.setPixelColor(0, 0, QColor("red"))
        target.frame.side_effect = [target.frame.return_value, changed]
    elif change == "pixel-size":
        target.area = Rect(0, 0, 25, 60)
    elif change == "read-error":
        read.side_effect = RuntimeError("Broken optional read")
    elif change == "save-error":
        monkeypatch.setattr(QImage, "save", Mock(return_value=False))


@pytest.mark.parametrize("unavailable", [False, True])
def test_trace_returns_identical_failure_with_optional_retained_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_snapshot: tuple[Mock, Mock, Mock],
    *,
    unavailable: bool,
) -> None:
    """Actual tracer plumbing cannot promote or replace the failed product result."""
    target, evidence, read = failure_snapshot
    target._window, target._input_tick = 9, 12
    result = ScrollResult(None, "Original view size failure", "not restored", 13)
    returned: list[ScrollResult] = []

    def retain(
        opened: object, _output: Path, _mode: str, selected: dict[str, object]
    ) -> Mock:
        assert opened is target
        selected["initial_native"] = read.return_value
        return evidence

    def capture() -> None:
        native.UiaScrollTarget._open(target, Point(0, 0))
        returned.append(native.ScrollTransaction.run(Mock(_target=target)))

    if unavailable:
        read.side_effect = RuntimeError("Optional native read failed")
    monkeypatch.setattr(native.ScrollTransaction, "run", lambda _: result)
    monkeypatch.setattr(native.UiaScrollTarget, "_open", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(native, "_target_evidence", retain)
    monkeypatch.setattr(native, "helper_main", capture)
    monkeypatch.setattr("sys.argv", ["-c", str(tmp_path), "explorer-enter"])
    native._trace_helper()
    assert returned == [result]
    assert returned[0] is result
    receipt = json.loads((tmp_path / "target.json").read_text())["failure_observation"]
    assert receipt["status"] == (
        "unavailable" if unavailable else "stable_read_only_snapshot"
    )
    assert receipt["accepted"] is False
    assert receipt["original_result"]["message"] == result.message
    assert not (tmp_path / "frames.json").exists()
    assert not (tmp_path / "restoration.json").exists()
    target.move.assert_not_called()


@pytest.mark.parametrize("vertical_success", [False, True])
def test_failure_native_requires_vertical_control_not_just_any_success(
    tmp_path: Path,
    failure_snapshot: tuple[Mock, Mock, Mock],
    *,
    vertical_success: bool,
) -> None:
    """Unsupported List SB_VERT cannot conceal a missing retained vertical SB_CTL."""
    target, evidence, read = failure_snapshot
    base = read.return_value[0]
    read.return_value = [
        {**base, "native_handle": 18, "bar": 1, "success": False, "page": None},
        {**base, "native_handle": 19, "bar": 2, "success": True},
        {
            **base,
            "native_handle": 20,
            "bar": 2,
            "success": vertical_success,
            "page": 185 if vertical_success else None,
        },
    ]
    selected: dict[str, object] = {"initial_native": read.return_value}
    result = ScrollResult(None, "Original failure", "not restored", 13)
    native._record_terminal_position(target, result, selected, tmp_path, evidence)
    receipt = selected["failure_observation"]
    assert isinstance(receipt, dict)
    assert receipt["status"] == (
        "stable_read_only_snapshot" if vertical_success else "unavailable"
    )
    assert receipt["native_before"] == read.return_value
    assert receipt["accepted"] is False
    target.move.assert_not_called()


@pytest.mark.parametrize("change", ["none", "partial", "final-input", "budget"])
def test_edge_ancestor_failure_receipt_keeps_final_guard_and_original_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    change: str,
) -> None:
    """Ancestry has no boundary authority, including on partial/error paths."""
    target = Mock()
    target.position.return_value = ScrollPosition(-1.0, 85.0, 22.4)
    result = ScrollResult(None, "Original Edge failure", "not restored", 9)
    selected: dict[str, object] = {"mode": "edge-prose-enter"}
    now = 0.0

    def ancestors(
        observed_target: object,
        receipt: dict[str, object],
        guard: object,
    ) -> None:
        nonlocal now
        assert observed_target is target
        assert callable(guard)
        guard()
        receipt["semantic_boundary"] = "unavailable"
        receipt["ancestor_nodes"] = [{"native_handle": None}]
        if change == "partial":
            raise CaptureError("Parent read unavailable")
        if change == "final-input":
            target.check.side_effect = CaptureError("Input changed at final guard")
        if change == "budget":
            now = 1.0

    monkeypatch.setattr(native, "probe_ancestors", ancestors)
    monkeypatch.setattr(native.time, "monotonic", lambda: now)
    native._record_terminal_position(target, result, selected, tmp_path)
    receipt = selected["failure_observation"]
    assert isinstance(receipt, dict)
    assert receipt["semantic_boundary"] == "unavailable"
    assert receipt["scope"] == "selected_provider_ancestor_provenance"
    assert receipt["status"] == (
        "stable_read_only_snapshot" if change == "none" else "unavailable"
    )
    assert receipt["original_result"] == {
        "message": "Original Edge failure",
        "restoration": "not restored",
        "frames": 9,
        "has_image": False,
    }
    assert receipt["accepted"] is False
    assert (
        target.check.call_count
        == {"none": 7, "partial": 7, "final-input": 6, "budget": 5}[change]
    )
    if change == "partial":
        assert receipt["ancestor_read_error"] == "CaptureError: Parent read unavailable"
    target.move.assert_not_called()
    target.frame.assert_not_called()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "mode", ["edge-prose-click", "edge-prose-enter", "edge-panel-enter"]
)
def test_trace_routes_only_prose_failures_to_same_ancestor_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mode: str,
) -> None:
    """Existing prose jobs gain failure provenance without a new case or protocol."""
    target = Mock(_window=17, _input_tick=12, area=Rect(24, 110, 973, 647))
    target.position.return_value = ScrollPosition(-1.0, 85.0, 22.4)
    result = ScrollResult(None, "Original failure", "not restored", 9)
    returned: list[ScrollResult] = []

    def ancestors(_target: object, receipt: dict[str, object], _guard: object) -> None:
        receipt.update(semantic_boundary="unavailable", ancestor_nodes=[])

    def capture() -> None:
        native.UiaScrollTarget._open(target, Point(100, 150))
        returned.append(native.ScrollTransaction.run(Mock(_target=target)))

    probe = Mock(side_effect=ancestors)
    monkeypatch.setattr(native, "probe_ancestors", probe)
    monkeypatch.setattr(native.ScrollTransaction, "run", lambda _: result)
    monkeypatch.setattr(native.UiaScrollTarget, "_open", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(native, "_target_evidence", Mock(return_value=None))
    monkeypatch.setattr(native, "helper_main", capture)
    monkeypatch.setattr(native.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr("sys.argv", ["-c", str(tmp_path), mode])
    native._trace_helper()
    assert returned[0] is result
    selected = json.loads((tmp_path / "target.json").read_text())
    assert selected["mode"] == mode
    assert selected["area"] == {"x": 24, "y": 110, "width": 973, "height": 647}
    assert probe.call_count == int(mode.startswith("edge-prose-"))
    assert ("failure_observation" in selected) == mode.startswith("edge-prose-")
    assert not (tmp_path / "frames.json").exists()
    assert not (tmp_path / "restoration.json").exists()
    target.move.assert_not_called()
    target.frame.assert_not_called()
