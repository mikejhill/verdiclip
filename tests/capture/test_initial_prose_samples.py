"""Optional initial pixels must not become capture or restoration authority."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest
from PySide6.QtGui import QColor, QImage
from scripts import simple_scroll_native as native

from verdiclip.capture.scrolling import ScrollPosition, ScrollResult, ScrollTransaction
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Point, Rect

POSITION = ScrollPosition(-1.0, 0.0, 17.0)
SELECTED: dict[str, object] = {
    "mode": "edge-prose-click",
    "window": 17,
    "input_tick": 12,
}


def _image(value: int, width: int = 24, height: int = 60) -> QImage:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(value, value, value))
    return image


def _operation(
    monkeypatch: pytest.MonkeyPatch, images: list[QImage], calls: list[str]
) -> ScrollTransaction:
    target = object.__new__(native.UiaScrollTarget)
    target._area = Rect(0, 0, images[0].width(), images[0].height())
    target._window, target._input_tick = 17, 12
    frozen = Mock(bounds=target.area)

    def crop(_area: Rect) -> QImage:
        calls.append("crop")
        return images[min(calls.count("crop") - 1, len(images) - 1)]

    frozen.crop.side_effect = crop
    target._screens = Mock(
        freeze=Mock(side_effect=lambda: (calls.append("freeze"), frozen)[1])
    )
    monkeypatch.setattr(
        native.UiaScrollTarget, "check", lambda _: calls.append("check")
    )
    monkeypatch.setattr(
        native.UiaScrollTarget,
        "position",
        lambda _: (calls.append("position"), POSITION)[1],
    )
    return ScrollTransaction(
        target,
        lambda: (calls.append("cancel"), False)[1],
        clock=lambda: 0.0,
        pause=lambda _: calls.append("pause"),
    )


@pytest.mark.parametrize(
    "outcome", ["stable", "changing", "source-error", "position-error"]
)
def test_initial_samples_preserve_real_frame_guards_counts_and_outcome(
    monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    """Unchanged production frame loops make exactly the same underlying calls."""
    images = [_image(i) for i in range(9)] if outcome == "changing" else [_image(1)] * 2
    original_error = CaptureError("Original provider error")
    results: list[tuple[list[str], int, QImage | BaseException]] = []
    receipt: dict[str, object] = {}
    samples: list[tuple[dict[str, object], QImage]] = []
    for retained in (False, True):
        calls: list[str] = []
        with monkeypatch.context() as patcher:
            operation = _operation(patcher, images, calls)
            if outcome == "source-error":
                operation._target._screens.freeze.side_effect = original_error  # ty: ignore[unresolved-attribute]
            elif outcome == "position-error":
                patcher.setattr(
                    native.UiaScrollTarget, "position", Mock(side_effect=original_error)
                )
            original = native.UiaScrollTarget.frame
            scope = (
                native._retain_initial_samples(
                    receipt, samples, [], position=POSITION, selected=SELECTED
                )
                if retained
                else nullcontext()
            )
            try:
                with scope:
                    result = operation._frame(POSITION)
            except CaptureError as error:
                result = error
            assert native.UiaScrollTarget.frame is original
            results.append((calls, operation._frames, result))
    assert results[0][:2] == results[1][:2]
    if outcome == "stable":
        assert results[0][2] is results[1][2] is images[-1]
        assert results[1][1] == 1
        assert receipt["first_frame"] == "returned"
        assert receipt["attempted"] == receipt["retained"] == 2
        assert results[1][0].count("check") == 6
        assert results[1][0].count("position") == 2
        assert results[1][0].count("pause") == 1
    else:
        assert type(results[0][2]) is type(results[1][2]) is CaptureError
        assert str(results[0][2]) == str(results[1][2])
        if outcome != "changing":
            assert results[1][2] is original_error
        assert receipt["first_frame"] == "raised"
    if outcome == "changing":
        assert receipt["attempted"] == 9
        assert receipt["retained"] == 8
        assert receipt["omitted"] == 1
        assert receipt["truncated"]
        assert receipt["incomplete"]
        assert results[1][0].count("check") == 27
        assert results[1][0].count("position") == 9
        assert results[1][0].count("pause") == 8


@pytest.mark.parametrize("when", ["before", "after"])
def test_initial_samples_copy_only_after_original_guards(
    monkeypatch: pytest.MonkeyPatch, when: str
) -> None:
    """An image acquired before a failing post-capture guard is never retained."""
    calls: list[str] = []
    operation = _operation(monkeypatch, [_image(1)], calls)
    error = CaptureError("Original target guard")
    check = Mock(side_effect=[error] if when == "before" else [None, error])
    monkeypatch.setattr(native.UiaScrollTarget, "check", check)
    receipt: dict[str, object] = {}
    samples: list[tuple[dict[str, object], QImage]] = []
    original = native.UiaScrollTarget.frame
    with (
        pytest.raises(CaptureError) as caught,
        native._retain_initial_samples(
            receipt, samples, [], position=POSITION, selected=SELECTED
        ),
    ):
        operation._target.frame()
    assert caught.value is error
    assert native.UiaScrollTarget.frame is original
    assert samples == []
    assert receipt["first_frame"] == "raised"
    assert receipt["first_frame_error"] == "CaptureError: Original target guard"
    assert receipt["attempted"] == receipt["omitted"] == 1
    assert receipt["incomplete"]
    assert calls.count("crop") == int(when == "after")


def test_initial_samples_enforce_bytes_and_detach_without_limiting_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Eight copies and 32 MiB are separate caps; retained pixels cannot mutate."""
    image = _image(31, 2048, 1024)
    original = Mock(return_value=image)
    monkeypatch.setattr(native.UiaScrollTarget, "frame", original)
    target = object.__new__(native.UiaScrollTarget)
    receipt: dict[str, object] = {}
    samples: list[tuple[dict[str, object], QImage]] = []
    with native._retain_initial_samples(
        receipt, samples, [0.0], position=POSITION, selected=SELECTED
    ):
        for _ in range(9):
            assert target.frame() is image
    assert original.call_count == 9
    assert all(call.args == (target,) for call in original.call_args_list)
    assert native.UiaScrollTarget.frame is original
    assert receipt["retained"] == 4
    assert receipt["retained_bytes"] == 32 * 1024 * 1024
    assert receipt["omitted"] == 5
    assert receipt["truncated"]
    assert receipt["incomplete"]
    image.fill(QColor("red"))
    assert all(copy.pixelColor(0, 0) == QColor(31, 31, 31) for _, copy in samples)
    assert receipt["requested_position"] == {
        "horizontal": -1.0,
        "vertical": 0.0,
        "view_size": 17.0,
    }
    assert receipt["original_input_tick"] == 12
    assert receipt["original_window"] == 17
    assert receipt["setter_writes"] == 1
    for index, row in enumerate(cast("list[dict[str, int]]", receipt["samples"])):
        assert row["index"] == index
        assert row["setter_writes"] == 1
        assert row["start_monotonic_ns"] <= row["return_monotonic_ns"]


@pytest.mark.parametrize(
    "fault", ["copy", "null-copy", "start-clock", "return-clock", "metadata"]
)
def test_initial_sample_observation_errors_preserve_original_return(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """Optional metadata, clocks and detachment failures never retry capture."""
    image = _image(1)
    original = Mock(return_value=image)
    monkeypatch.setattr(native.UiaScrollTarget, "frame", original)
    error = OSError("Optional sample error")
    if fault == "copy":
        monkeypatch.setattr(QImage, "copy", Mock(side_effect=error))
    elif fault == "null-copy":
        monkeypatch.setattr(QImage, "copy", Mock(return_value=QImage()))
    elif fault == "metadata":
        monkeypatch.setattr(native, "asdict", Mock(side_effect=error))
    else:
        ticks = [error, 2] if fault == "start-clock" else [1, error]
        monkeypatch.setattr(native.time, "perf_counter_ns", Mock(side_effect=ticks))
    receipt: dict[str, object] = {}
    samples: list[tuple[dict[str, object], QImage]] = []
    target = object.__new__(native.UiaScrollTarget)
    with native._retain_initial_samples(
        receipt, samples, [], position=POSITION, selected=SELECTED
    ):
        assert target.frame() is image
    original.assert_called_once_with(target)
    assert native.UiaScrollTarget.frame is original
    assert receipt["first_frame"] == "returned"
    assert receipt["errors"]
    assert receipt["incomplete"]


@pytest.mark.parametrize("fault", ["save-false", "save-error", "json"])
def test_initial_sample_flush_errors_are_optional(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fault: str
) -> None:
    """Artifact errors are recorded without replacing a result or retrying I/O."""
    image = _image(1)
    receipt: dict[str, object] = {"errors": [], "incomplete": False}
    row: dict[str, object] = {"index": 0}
    save = (
        Mock(return_value=False)
        if fault == "save-false"
        else Mock(side_effect=OSError("optional PNG"))
    )
    if fault == "json":
        write = Mock(side_effect=OSError("optional JSON"))
        monkeypatch.setattr(Path, "write_text", write)
    else:
        monkeypatch.setattr(QImage, "save", save)
    native._save_initial_samples(tmp_path, receipt, [(row, image)])
    assert receipt["errors"]
    assert receipt["incomplete"]
    if fault == "json":
        write.assert_called_once()
        assert row["file"] == "initial-sample-0.png"
    else:
        save.assert_called_once()
        assert "file" not in row
        assert json.loads((tmp_path / "initial-samples.json").read_text())["incomplete"]


@pytest.mark.parametrize(
    "mode",
    [
        "edge-prose-click",
        "edge-prose-enter",
        "edge-prose-other",
        "edge-panel-click",
        "edge",
        "edge-nested",
    ],
)
@pytest.mark.parametrize("first_fails", [False, True])
def test_trace_initial_samples_are_first_attempt_only_and_flushed_last(  # noqa: PLR0915
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str, *, first_fails: bool
) -> None:
    """Only two exact modes gain unaccepted samples after existing terminal work."""
    calls: list[str] = []
    images = [_image(1), _image(2), _image(2)]
    operation = _operation(monkeypatch, images, calls)
    target = operation._target
    original_frame = native.UiaScrollTarget.frame
    original_acquire = ScrollTransaction._frame
    error = CaptureError("First acquisition failed")
    accepted: list[QImage] = []
    attempts = 0

    def acquire(operation: ScrollTransaction, position: ScrollPosition) -> QImage:
        nonlocal attempts
        attempts += 1
        if first_fails and attempts == 1:
            operation._target.frame()
            raise error
        return original_acquire(operation, position)

    def move(
        _target: object,
        _percent: float,
        *,
        before_write: Callable[[], None] | None = None,
    ) -> None:
        assert before_write is not None
        before_write()

    def run(operation: ScrollTransaction) -> ScrollResult:
        native.UiaScrollTarget.move(target, 0.0)  # ty: ignore[invalid-argument-type]
        if first_fails:
            with pytest.raises(CaptureError) as caught:
                operation._frame(POSITION)
            assert caught.value is error
            assert native.UiaScrollTarget.frame is original_frame
        accepted.append(operation._frame(POSITION))
        assert native.UiaScrollTarget.frame is original_frame
        accepted.append(operation._frame(POSITION))
        assert not list(tmp_path.glob("initial-*"))
        return result

    result = ScrollResult(images[-1], "Original result", "restored", 2)

    def terminal(*_args: object) -> None:
        assert not list(tmp_path.glob("initial-*"))
        calls.append("terminal")

    save = native._save_initial_samples

    def flush(
        output: Path,
        receipt: dict[str, object],
        samples: list[tuple[dict[str, object], QImage]],
    ) -> None:
        if mode.startswith(native._REPRESENTATIVE_PREFIXES):
            assert calls[-1] == "terminal"
        assert native.UiaScrollTarget.frame is original_frame
        calls.append("flush")
        save(output, receipt, samples)

    def capture() -> None:
        native.UiaScrollTarget._open(target, Point(0, 0))  # ty: ignore[invalid-argument-type]
        assert operation.run() is result

    monkeypatch.setattr(native, "_save_initial_samples", flush)
    monkeypatch.setattr(native.UiaScrollTarget, "_open", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(native, "_target_evidence", Mock(return_value=None))
    monkeypatch.setattr(native, "_record_terminal_position", terminal)
    monkeypatch.setattr(
        native, "_frame_observation", lambda *_args, **_kwargs: {"view_size": 17.0}
    )
    monkeypatch.setattr(native.UiaScrollTarget, "move", move)
    monkeypatch.setattr(ScrollTransaction, "_frame", acquire)
    monkeypatch.setattr(ScrollTransaction, "run", run)
    monkeypatch.setattr(native, "helper_main", capture)
    monkeypatch.setattr("sys.argv", ["-c", str(tmp_path), mode])
    native._trace_helper()
    assert native.UiaScrollTarget.frame is original_frame
    assert ScrollTransaction._frame is acquire
    assert result.frames == 2
    assert accepted == [images[-1], images[-1]]
    _assert_trace_artifacts(tmp_path, mode, images[0], first_fails=first_fails)


def _assert_trace_artifacts(
    tmp_path: Path, mode: str, initial: QImage, *, first_fails: bool
) -> None:
    exact_mode = mode in ("edge-prose-click", "edge-prose-enter")
    assert (tmp_path / "initial-samples.json").exists() == exact_mode
    assert len(json.loads((tmp_path / "frames.json").read_text())) == 2
    assert sorted(path.name for path in tmp_path.glob("frame-*.png")) == [
        "frame-0.png",
        "frame-1.png",
    ]
    assert json.loads((tmp_path / "movement.json").read_text())["writes"] == 1
    if exact_mode:
        receipt = json.loads((tmp_path / "initial-samples.json").read_text())
        assert receipt["first_frame"] == ("raised" if first_fails else "returned")
        assert receipt["attempted"] == receipt["retained"] == (1 if first_fails else 3)
        assert receipt["accepted"] is receipt["product_capture_qualification"] is False
        assert receipt["phase"] == "target_frame_return_before_position_check"
        assert receipt["original_window"] == 17
        assert receipt["original_input_tick"] == 12
        assert receipt["setter_writes"] == 1
        assert receipt["samples"][0]["file"] == "initial-sample-0.png"
        assert QImage(str(tmp_path / "initial-sample-0.png")) == initial
    timing = json.loads((tmp_path / "phase-timing.json").read_text())
    assert timing["totals"]["frame"]["count"] == 2 + first_fails
    assert timing["totals"]["frame"]["exceptions"] == first_fails
    assert len(timing["frames"]) == 2 + first_fails


@pytest.mark.parametrize("original_fails", [False, True])
@pytest.mark.parametrize("sink", ["png", "json"])
def test_trace_sample_write_failure_keeps_helper_return_or_exception(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sink: str,
    *,
    original_fails: bool,
) -> None:
    """A failing final diagnostic write cannot escape instead of the helper."""
    operation = _operation(monkeypatch, [_image(1)], [])
    target = operation._target
    error = CaptureError("Original helper error")
    original_frame = native.UiaScrollTarget.frame
    attempts: list[str] = []
    image_save, json_save = QImage.save, Path.write_text

    def save_image(image: QImage, name: str) -> bool:
        if Path(name).name.startswith("initial-sample-"):
            attempts.append("png")
            raise OSError("Optional PNG failed")
        return image_save(image, name)

    def save_json(path: Path, data: str, *, encoding: str) -> int:
        if path.name == "initial-samples.json":
            attempts.append("json")
            raise OSError("Optional JSON failed")
        return json_save(path, data, encoding=encoding)

    def capture() -> None:
        native.UiaScrollTarget._open(target, Point(0, 0))  # ty: ignore[invalid-argument-type]
        assert operation._frame(POSITION).pixelColor(0, 0) == QColor(1, 1, 1)
        if original_fails:
            raise error

    if sink == "png":
        monkeypatch.setattr(QImage, "save", save_image)
    else:
        monkeypatch.setattr(Path, "write_text", save_json)
    monkeypatch.setattr(native.UiaScrollTarget, "_open", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(native, "_target_evidence", Mock(return_value=None))
    monkeypatch.setattr(
        native, "_frame_observation", lambda *_args, **_kwargs: {"view_size": 17.0}
    )
    monkeypatch.setattr(native, "helper_main", capture)
    monkeypatch.setattr("sys.argv", ["-c", str(tmp_path), "edge-prose-click"])
    if original_fails:
        with pytest.raises(CaptureError) as caught:
            native._trace_helper()
        assert caught.value is error
    else:
        assert native._trace_helper() is None
    assert attempts == (["png", "png"] if sink == "png" else ["json"])
    assert native.UiaScrollTarget.frame is original_frame
    assert (tmp_path / "movement.json").exists()
