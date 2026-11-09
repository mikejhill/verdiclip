"""The independent diagnostic oracle must detect altered or missing output rows."""

from __future__ import annotations

import ctypes as c
import ctypes.wintypes as w
import hashlib
import json
import math
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest
from PySide6.QtGui import QColor, QImage, QPainter
from scripts import simple_scroll_native as native
from scripts.scrolling_diagnostics import _inspect_result, _verify_restored
from scripts.scrolling_oracle import (
    _native_displacement,
    _oracle_header,
    verify_sequence,
)

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
    evidence = Mock(target=target, confirmation_failure=None, geometry=geometry)
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


@pytest.mark.parametrize(
    "mode",
    ["explorer-enter", "edge-panel-enter", "edge", "edge-nested", "edge-app-click"],
)
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
    if mode == "edge-app-click":
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


@pytest.mark.parametrize(
    "mode",
    [
        "edge",
        "edge-nested",
        "edge-panel-enter",
        "edge-cancel",
        "edge-input",
        "edge-covered",
        "edge-app-escape",
        "edge-app-click",
        "edge-select",
        "edge-unqualified",
    ],
)
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
    complete = mode in ("edge", "edge-nested", "edge-panel-enter")
    if complete and budget == 3000:
        with pytest.raises(CaptureError, match="Diagnostic frame evidence"):
            native._trace_helper()
    else:
        native._trace_helper()
    expected = 3 if complete and budget == 10000 else 2
    assert len(list(tmp_path.glob("*.png"))) == expected


@pytest.mark.parametrize("mode", ["edge", "edge-nested"])
def test_edge_trace_retains_independent_initial_and_final_dpi(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str
) -> None:
    """Use the native probe twice and no List witness or product DPI evidence."""
    frame = QImage(24, 60, QImage.Format.Format_RGB32)
    frame.fill(QColor("white"))
    target = Mock(_window=17, _input_tick=12, area=Rect(0, 0, 24, 60))
    target.position.return_value = ScrollPosition(35.0, 37.0, 50.0)
    dpi = Mock(side_effect=[120, 96])
    witness, probe = Mock(), Mock()
    result = ScrollResult(frame, "", "restored", 1)
    monkeypatch.setattr(native, "window_dpi", dpi)
    monkeypatch.setattr(native, "ListItemWitness", witness)
    monkeypatch.setattr(native, "probe_target", probe)
    monkeypatch.setattr(native.UiaScrollTarget, "_open", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(native.ScrollTransaction, "run", lambda _: result)
    monkeypatch.setattr("sys.argv", ["-c", str(tmp_path), mode])
    returned = []

    def capture() -> None:
        native.UiaScrollTarget._open(target, Point(0, 0))
        returned.append(native.ScrollTransaction.run(Mock(_target=target)))

    monkeypatch.setattr(native, "helper_main", capture)
    native._trace_helper()
    assert returned == [result]
    selected = json.loads((tmp_path / "target.json").read_text())
    assert selected["mode"] == mode
    assert selected["window_dpi"] == 120
    assert selected["final_window_dpi"] == 96
    assert selected["initial_position"] == {
        "horizontal": 35.0,
        "vertical": 37.0,
        "view_size": 50.0,
    }
    assert [call.args for call in dpi.call_args_list] == [(target,), (target,)]
    witness.assert_not_called()
    probe.assert_not_called()
    target.move.assert_not_called()
    monkeypatch.setenv("DISPLAY_SCALE", "125")
    with pytest.raises(RuntimeError, match="target DPI"):
        native._edge_row_height(tmp_path, {"application": mode})


@pytest.mark.parametrize(
    "mode",
    [
        "edge-cancel",
        "edge-input",
        "edge-covered",
        "edge-app-escape",
        "edge-app-click",
        "edge-select",
        "edge-unqualified",
    ],
)
def test_edge_target_probe_does_not_extend_other_modes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str
) -> None:
    """Broad Edge-prefix matching must not alter interruption or tray semantics."""
    target, read = Mock(), Mock()
    monkeypatch.setattr(native, "window_dpi", read)
    selected: dict[str, object] = {}
    assert native._target_evidence(target, tmp_path, mode, selected) is None
    assert selected == {}
    read.assert_not_called()
    target.position.assert_not_called()


@pytest.mark.parametrize("mode", ["edge", "edge-nested"])
def test_edge_complete_trace_retains_existing_frame_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str
) -> None:
    """Additional raw coverage cannot grow the existing eighty-frame budget."""
    assert native.MAX_FRAMES == 80
    assert native.MAX_PIXELS == 40_000_000
    frame = QImage(24, 60, QImage.Format.Format_RGB32)
    frame.fill(QColor("white"))
    monkeypatch.setattr(native.ScrollTransaction, "_frame", lambda *_: frame)
    monkeypatch.setattr("sys.argv", ["-c", str(tmp_path), mode])

    def capture() -> None:
        for index in range(81):
            native.ScrollTransaction._frame(Mock(), ScrollPosition(0.0, index, 50.0))

    monkeypatch.setattr(native, "helper_main", capture)
    native._trace_helper()
    assert len(json.loads((tmp_path / "frames.json").read_text())) == 80
    assert len(list(tmp_path.glob("frame-*.png"))) == 80


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
    evidence = (
        Mock(target=target, confirmation_failure=None, geometry={})
        if kind == "explorer"
        else None
    )
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
    evidence = Mock(target=target, confirmation_failure=None, geometry={})
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
    evidence = Mock(target=target, confirmation_failure=None, geometry={})
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
    operation, evidence = (
        Mock(_target=target),
        Mock(target=target, confirmation_failure=None, geometry={}),
    )
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


def _one_rail_sequence(
    tmp_path: Path, *, horizontal: float, interior_page: int = 492
) -> QImage:
    """The optional-footer route needs contained raw items and the same full proof."""
    image = _native_list_sequence(tmp_path, interior_page=interior_page)
    geometry_path = tmp_path / "header-geometry.json"
    geometry = json.loads(geometry_path.read_text())
    geometry["position"]["horizontal"] = horizontal
    nodes = geometry["uia"]["nodes"]
    nodes.pop()
    nodes[1]["bounds"]["width"] = 20
    nodes[0]["bounds"]["height"] = geometry["captured_bounds"]["height"] = 125
    geometry_path.write_text(json.dumps(geometry))
    path = tmp_path / "frames.json"
    positions = json.loads(path.read_text())
    for index, position in enumerate(positions):
        position["horizontal"] = horizontal
        position["native_scroll_info"].pop()
        if "native_scroll_info_after" in position:
            position["native_scroll_info_after"].pop()
        for item in position["native_item_witness"]["items"]:
            for phase in ("before", "after", "visible_before", "visible_after"):
                item[phase]["width"] = 16
        frame = QImage(str(tmp_path / f"frame-{index}.png"))
        assert frame.copy(0, 0, 24, 125).save(str(tmp_path / f"frame-{index}.png"))
    path.write_text(json.dumps(positions))
    target_path = tmp_path / "target.json"
    target = json.loads(target_path.read_text())
    target["initial_position"]["horizontal"] = horizontal
    target["initial_native"].pop()
    target["area"]["height"] = 125
    target_path.write_text(json.dumps(target))
    return image.copy(0, 0, 24, 245)


@pytest.mark.parametrize("horizontal", [-1.0, 0.0])
@pytest.mark.parametrize("interior_page", [480, 492, 500])
def test_native_oracle_supports_one_rail_without_inventing_a_footer(
    tmp_path: Path, horizontal: float, interior_page: int
) -> None:
    """Whole raw items and fresh native proof qualify either exact original H."""
    image = _one_rail_sequence(
        tmp_path, horizontal=horizontal, interior_page=interior_page
    )
    report = verify_sequence(image, tmp_path)
    assert report["oracle_verified_rows"] == 245
    header = _oracle_header(
        tmp_path, json.loads((tmp_path / "frames.json").read_text())
    )
    assert header is not None
    assert header.native is not None
    assert header.native.horizontal == horizontal


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


def test_hminus_one_partial_native_evidence_cannot_fall_back(
    tmp_path: Path,
) -> None:
    """NoScroll with native support now needs complete owned List receipts."""
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
    with pytest.raises(RuntimeError, match="native owner"):
        verify_sequence(image, tmp_path)


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


@pytest.mark.parametrize("failed", [False, True])
def test_trace_keeps_evidence_and_restores_every_wrapper(
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
    monkeypatch.setattr(
        native._ListSteps, "begin", lambda *_: callbacks.append("begin")
    )
    monkeypatch.setattr(
        native._ListSteps, "finish", lambda *_: callbacks.append("finish")
    )

    def move(*_args: object, before_write: Callable[[], None] | None = None) -> None:
        if before_write is not None:
            before_write()
        callbacks.append("write")

    monkeypatch.setattr(native.UiaScrollTarget, "move", move)
    monkeypatch.setattr(
        native.UiaScrollTarget, "check", lambda *_: callbacks.append("guard")
    )

    def run(_operation: object) -> ScrollResult:
        assert not (tmp_path / "list-calls.json").exists()
        native.UiaScrollTarget.check(target)
        native.UiaScrollTarget.move(target, 12.5)
        assert native.ScrollTransaction._frame(operation, position) is frame
        assert not (tmp_path / "list-calls.json").exists()
        native.UiaScrollTarget.check(target)
        native._ListSteps.begin(Mock(), position)
        native._ListSteps.finish(Mock(), position)
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
    assert callbacks == ["guard", "write", "guard", "begin", "finish"]
    assert all(getattr(owner, name) is original for owner, name, original in originals)
    evidence = json.loads((tmp_path / "list-calls.json").read_text())
    assert evidence == {
        name: {"count": 1, "exceptions": 0} for name in ("begin", "finish")
    }
    assert (tmp_path / "frame-0.png").exists()
    assert len(json.loads((tmp_path / "frames.json").read_text())) == 1
    assert json.loads((tmp_path / "movement.json").read_text()) == {
        "writes": 1,
        "last_vertical": 12.5,
    }


@pytest.mark.parametrize("failed", [False, True])
def test_list_counter_write_failure_does_not_replace_original_outcome(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, failed: bool
) -> None:
    """Optional counter storage may fail without changing result or exception."""
    write = Path.write_text

    def fail_counters(path: Path, data: str, *args: object, **kwargs: object) -> int:
        if path.name == "list-calls.json":
            raise OSError("Counter destination unavailable")
        return write(path, data, *args, **kwargs)  # ty: ignore[invalid-argument-type]

    monkeypatch.setattr(Path, "write_text", fail_counters)
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
    assert not (tmp_path / "list-calls.json").exists()
    assert (tmp_path / "movement.json").exists()


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


@pytest.mark.parametrize(
    "failure",
    [
        "initial-native",
        "frame-native",
        "terminal-repeat",
        "owner",
        "range",
        "mapping",
        "right-clipping",
        "hit-sliver",
        "rail",
        "horizontal",
        "pixels",
    ],
)
def test_no_hbar_oracle_never_downgrades_failed_native_or_raw_proof(
    tmp_path: Path, failure: str
) -> None:
    """Failed native, item and pixel proof cannot downgrade to the generic route."""
    image = _one_rail_sequence(tmp_path, horizontal=-1.0, interior_page=500)
    assert verify_sequence(image, tmp_path)["oracle_verified_rows"] == 245
    path = tmp_path / "frames.json"
    positions = json.loads(path.read_text())
    if failure == "initial-native":
        target_path = tmp_path / "target.json"
        target = json.loads(target_path.read_text())
        target.pop("initial_native")
        target_path.write_text(json.dumps(target))
    elif failure == "rail":
        geometry_path = tmp_path / "header-geometry.json"
        geometry = json.loads(geometry_path.read_text())
        geometry["uia"]["nodes"][2]["native_handle"] = None
        geometry_path.write_text(json.dumps(geometry))
    elif failure == "frame-native":
        positions[1].pop("native_scroll_info")
    elif failure == "terminal-repeat":
        positions[-1].pop("native_scroll_info_after")
    elif failure == "owner":
        positions[1]["native_scroll_info_after"][0]["root"] += 1
    elif failure in ("range", "mapping"):
        positions[1]["native_scroll_info"][0][
            "max" if failure == "range" else "pos"
        ] += 1
    elif failure in ("right-clipping", "hit-sliver"):
        item = positions[1]["native_item_witness"]["items"][0]
        for phase in ("before", "after", "visible_before", "visible_after"):
            item[phase]["width"] = 40 if failure == "right-clipping" else 0.25
    elif failure == "horizontal":
        positions[1]["horizontal"] = 0.0
    else:
        image.setPixelColor(0, 80, QColor("red"))
    path.write_text(json.dumps(positions))
    with pytest.raises(RuntimeError):
        verify_sequence(image, tmp_path)


def _content_table(offset: int, *, rail: bool = False) -> QImage:
    image = QImage(50, 210, QImage.Format.Format_RGB32)
    for y in range(image.height()):
        row = (offset + y) // 30
        for x in range(image.width()):
            color = (
                QColor("black")
                if 5 <= x < 25 and 10 <= (offset + y) % 30 < 13
                else QColor(row + 30, 80, 160)
            )
            image.setPixel(x, y, color.rgb())
        if rail:
            image.setPixel(0, y, QColor("#eeeeee").rgb())
    return image


def _mutate_witnessed_edge(
    before: QImage, after: QImage, frames: list[QImage], mutation: str
) -> None:
    """Probe paint-envelope ambiguity and destructive restored-content changes."""
    if mutation == "clipped_paint":
        before.setPixelColor(0, 45, QColor(20, 50, 120))
        after.setPixelColor(0, 45, QColor(21, 51, 121))
    elif mutation == "deleted_glyphs":
        for y in range(after.height()):
            for x in range(after.width()):
                if before.pixelColor(x, y) == QColor("black"):
                    after.setPixelColor(x, y, QColor((90 + y) // 30 + 30, 80, 160))
    elif mutation == "red_viewport":
        painter = QPainter(after)
        painter.fillRect(0, 0, 20, after.height(), QColor("red"))
        painter.end()
    elif mutation == "direct_edge_paint":
        before.setPixel(0, 45, frames[0].pixel(0, 135))


@pytest.mark.parametrize(
    "mutation",
    [
        "none",
        "glyph",
        "background",
        "new_nonpaint",
        "footprint",
        "island",
        "missing_witness",
        "disagreeing_witness",
        "row_phase",
        "row_id",
        "order",
        "clipped_paint",
        "deleted_glyphs",
        "red_viewport",
        "direct_edge_paint",
    ],
)
def test_table_content_restoration_requires_source_paint_and_two_witnesses(
    mutation: str,
) -> None:
    """Never turn unknown chrome, shifted rows or direct content changes into a pass."""
    before = _content_table(90, rail=True)
    after = before.copy()
    after.setPixelColor(0, 45, QColor("#dddddd"))
    frames = [_content_table(offset) for offset in (0, 30, 60)]
    paints = {
        "glyph": [(before, 5, "black"), (after, 5, "#010101")],
        "background": [(before, 5, "black"), (after, 5, "#2250a0")],
        "new_nonpaint": [(after, 5, "white")],
        "footprint": [(after, 0, "#2250a0")],
        "island": [(before, 10, "white"), (after, 10, "white")],
    }
    for image, x, color in paints.get(mutation, []):
        image.setPixelColor(x, 45, QColor(color))
    _mutate_witnessed_edge(before, after, frames, mutation)
    if mutation == "missing_witness":
        frames = frames[:1]
    elif mutation == "disagreeing_witness":
        frames[0].setPixelColor(0, 135, QColor("black"))
    elif mutation == "row_phase":
        frames[0] = _content_table(1)
        frames[0].setPixelColor(0, 134, QColor("black"))
    elif mutation == "row_id":
        for x in range(frames[0].width()):
            frames[0].setPixelColor(x, 60, QColor(38, 80, 160))
    elif mutation == "order":
        frames.reverse()
    if mutation in ("none", "clipped_paint"):
        proof = native._verify_table_content(before, after, frames, height=30)
        assert proof["document_offset"] == 90
        assert proof["raw_frame_offsets"] == [0, 30, 60]
        assert proof["nonpaint_pixels"] == 210
        assert (
            native._fixture_difference(before, after)["whole_viewport_equal"] is False
        )
    else:
        with pytest.raises(RuntimeError):
            native._verify_table_content(before, after, frames, height=30)


def test_table_offset_uses_all_exact_integer_authored_boundaries() -> None:
    """A wrong physical span or ambiguous ID transition cannot fit a displacement."""
    frame = _content_table(17)
    assert native._fixture_row_offset(frame, 30) == 17
    with pytest.raises(RuntimeError, match="offset or phase"):
        native._fixture_row_offset(frame, 29)


@pytest.mark.parametrize(
    "mutation",
    [
        "none",
        "source",
        "mode",
        "dpi",
        "final_dpi",
        "horizontal",
        "vertical",
        "view_size",
        "clipping",
        "size",
        "missing_geometry",
    ],
)
@pytest.mark.parametrize("mode", ["edge", "edge-nested"])
def test_fixture_context_requires_exact_owned_source_state_and_geometry(
    tmp_path: Path,
    mutation: str,
    mode: str,
) -> None:
    """The semantic oracle cannot apply to unknown documents or changed native state."""
    image = _content_table(0)
    area = {"x": 10, "y": 20, "width": 50, "height": 210}
    position = {
        "horizontal": 35.0 if mode == "edge-nested" else -1.0,
        "vertical": 0.0,
        "view_size": 10.0,
    }
    target = {
        "mode": mode,
        "window_dpi": 120,
        "final_window_dpi": 120,
        "area": area,
        "provider_bounds": area.copy(),
        "initial_position": position,
    }
    state = {"initial": position, "final": position.copy()}
    report: dict[str, object] = {
        "owned_fixture_sha256": native._CONTENT_FIXTURE_SOURCES[mode]
    }
    if mutation == "source":
        report["owned_fixture_sha256"] = "unknown"
    elif mutation == "mode":
        target["mode"] = "unknown"
    elif mutation in ("dpi", "final_dpi"):
        target["window_dpi" if mutation == "dpi" else "final_window_dpi"] = 192
    elif mutation in ("horizontal", "vertical", "view_size"):
        state["final"][mutation] += 1
    elif mutation == "clipping":
        target["provider_bounds"] = {**area, "x": 0}
    elif mutation == "missing_geometry":
        del target["provider_bounds"]
    elif mutation == "size":
        image = image.copy(0, 0, 49, 210)
    (tmp_path / "target.json").write_text(json.dumps(target))
    (tmp_path / "restoration.json").write_text(json.dumps(state))
    if mutation == "none":
        native._fixture_context(tmp_path, report, image, image, mode=mode)
    else:
        with pytest.raises(RuntimeError, match=r"unproved|fixture source"):
            native._fixture_context(tmp_path, report, image, image, mode=mode)


@pytest.mark.parametrize("nested", [False, True])
def test_table_source_hash_pins_the_plain_owned_fixture(*, nested: bool) -> None:
    """A source/layout change requires independent review, not automatic admission."""
    source = native._document(nested=nested).encode("utf-8")
    mode = "edge-nested" if nested else "edge"
    assert hashlib.sha256(source).hexdigest() == native._CONTENT_FIXTURE_SOURCES[mode]


def test_content_fallback_cannot_bypass_complete_output_oracle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Even eligible restoration differences cannot admit an incomplete output."""
    before = _content_table(90, rail=True)
    after = before.copy()
    after.setPixelColor(0, 45, QColor("#dddddd"))
    for name, image in (
        ("before.png", before),
        ("after.png", after),
        ("frame-0.png", _content_table(0)),
    ):
        assert image.save(str(tmp_path / name))
    oracle = Mock(side_effect=RuntimeError("Incomplete output"))
    content = Mock()
    monkeypatch.setattr(native, "verify_sequence", oracle)
    monkeypatch.setattr(native, "_verify_table_content", content)
    with pytest.raises(RuntimeError, match="Incomplete output"):
        native._verify_edge_sequence(
            ScrollResult(before, "", "restored", 3),
            tmp_path,
            {"application": "edge"},
        )
    content.assert_not_called()


def test_fixture_difference_retains_raw_pixels_and_rejects_alpha() -> None:
    """Raw differences cannot discard a changed channel while converting formats."""
    before = _content_table(0)
    after = before.copy()
    after.setPixelColor(4, 8, QColor("black"))
    report = native._fixture_difference(before, after)
    assert report["viewport_differences_argb"] == [
        {"x": 4, "y": 8, "before": before.pixel(4, 8), "after": after.pixel(4, 8)}
    ]
    transparent = after.convertToFormat(QImage.Format.Format_ARGB32)
    transparent.setPixel(4, 8, 0x01000000)
    with pytest.raises(RuntimeError, match="not opaque"):
        native._fixture_difference(before, transparent)


@pytest.mark.parametrize("failed", [False, True])
def test_list_counter_preserves_exact_call_result_and_exception(
    *, failed: bool
) -> None:
    """Counters preserve one callback invocation and its exact outcome."""
    counts = {"count": 0, "exceptions": 0}
    error = CaptureError("Original provider error")
    result = object()
    callback = Mock(side_effect=error) if failed else Mock(return_value=result)
    observed = native._count_list_call(callback, counts)
    if failed:
        with pytest.raises(CaptureError) as caught:
            observed(3, terminal=True)
        assert caught.value is error
    else:
        assert observed(3, terminal=True) is result
    callback.assert_called_once_with(3, terminal=True)
    assert counts == {"count": 1, "exceptions": int(failed)}


@pytest.mark.parametrize(
    "change", ["none", "missing", "failed", "owner", "invalid", "root", "bar"]
)
def test_helper_native_identity_remains_strict(change: str) -> None:
    """Shared helper comparison needs every original owner and real control read."""
    original: list[dict[str, object]] = [
        {
            "native_handle": 17,
            "bar": 2,
            "parent": 8,
            "root": 9,
            "pid": 123,
            "success": True,
            "window_valid": True,
            "root_matches": True,
        }
    ]
    rows = [dict(original[0])]
    if change == "missing":
        rows = []
    elif change == "failed":
        rows[0]["success"] = False
    elif change == "owner":
        rows[0]["pid"] = 124
    elif change == "invalid":
        rows[0]["window_valid"] = False
    elif change == "root":
        rows[0]["root_matches"] = False
    elif change == "bar":
        rows[0]["bar"] = 1
    if change == "none":
        native._failure_native_identity(rows, original)
    else:
        with pytest.raises(CaptureError, match="native identity"):
            native._failure_native_identity(rows, original)


@pytest.mark.parametrize("storage_failure", [False, True])
@pytest.mark.parametrize("receipt", [None, {"stage": "root_without_item"}])
def test_hit_failure_receipt_saved_after_helper_without_changing_exception(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    receipt: dict[str, object] | None,
    *,
    storage_failure: bool,
) -> None:
    """A bounded in-memory receipt is optional storage, never a new verdict."""
    target = Mock(_window=17, _input_tick=12, area=Rect(0, 0, 24, 60))
    evidence = Mock(confirmation_failure=receipt)
    monkeypatch.setattr(native, "_target_evidence", Mock(return_value=evidence))
    opening = Mock()
    monkeypatch.setattr(native.UiaScrollTarget, "_open", opening)
    original = CaptureError("Original hit-test failure")
    helper_finished = False
    write = Path.write_text

    def write_receipt(path: Path, data: str, *args: object, **kwargs: object) -> int:
        if path.name == "item-witness-failure.json":
            assert helper_finished
            if storage_failure:
                raise OSError("Storage unavailable")
        return write(path, data, *args, **kwargs)  # ty: ignore[invalid-argument-type]

    def helper() -> None:
        nonlocal helper_finished
        native.UiaScrollTarget._open(target, Point(12, 30))
        assert not (tmp_path / "item-witness-failure.json").exists()
        helper_finished = True
        raise original

    monkeypatch.setattr(Path, "write_text", write_receipt)
    monkeypatch.setattr(native, "helper_main", helper)
    monkeypatch.setattr("sys.argv", ["-c", str(tmp_path), "explorer-bottom-click"])
    with pytest.raises(CaptureError) as caught:
        native._trace_helper()
    assert caught.value is original
    opening.assert_called_once()
    evidence.assert_not_called()
    assert evidence.method_calls == []
    path = tmp_path / "item-witness-failure.json"
    if receipt is not None and not storage_failure:
        assert json.loads(path.read_text()) == receipt
    else:
        assert not path.exists()


@pytest.mark.parametrize("second_x", [18, 20])
@pytest.mark.parametrize("reverse", [False, True])
def test_header_oracle_accepts_horizontally_disjoint_item_pair(
    tmp_path: Path, second_x: int, *, reverse: bool
) -> None:
    """Independent rectangles may share a vertical band without overlapping."""
    image = _header_sequence(tmp_path)
    path = tmp_path / "frames.json"
    positions = json.loads(path.read_text())
    for position in positions[1:]:
        pair = position["native_item_witness"]["items"]
        for item, x in zip(pair, (12, second_x), strict=True):
            item["before"].update(x=x, y=60, width=6)
            item["after"].update(x=x, y=30, width=6)
        if reverse:
            pair.reverse()
    path.write_text(json.dumps(positions), encoding="utf-8")
    result = verify_sequence(image, tmp_path)
    assert result["oracle_verified_rows"] == image.height()


@pytest.mark.parametrize("second_x", [12, 13, 17])
@pytest.mark.parametrize("second_y", [60, 62])
def test_header_oracle_rejects_actual_rectangle_overlap(
    tmp_path: Path, second_x: int, second_y: int
) -> None:
    """Equal positive deltas do not authorize duplicated or intersecting witnesses."""
    image = _header_sequence(tmp_path)
    path = tmp_path / "frames.json"
    positions = json.loads(path.read_text())
    pair = positions[1]["native_item_witness"]["items"]
    for item, x, y in zip(pair, (12, second_x), (60, second_y), strict=True):
        item["before"].update(x=x, y=y, width=6)
        item["after"].update(x=x, y=y - 30, width=6)
    path.write_text(json.dumps(positions), encoding="utf-8")
    with pytest.raises(RuntimeError, match="witnesses disagree on displacement"):
        verify_sequence(image, tmp_path)
