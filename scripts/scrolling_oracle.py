"""Offline pixel coverage check for owned native diagnostic sequences only."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import cast

from PySide6.QtGui import QImage
from scripts.scrolling_item_witness import list_layout, raw_bounds

from verdiclip.capture.scrolling import MAX_FRAMES, MAX_PIXELS
from verdiclip.geometry import Rect

_FULL_PERCENT = 100.0
_PAIR_SIZE = 2
_NATIVE_BAR = 2
_SCROLLBAR_ROLE = 50014
_BAND_ROWS = 32
_TEXTURE_ROWS = 8


class _NativeListOracle:
    """Check raw native observations, never retained production model objects.

    Frame samples establish rail HWNDs/root membership and native values.
    Parent/root/PID identity is separately observed initially and at repeated
    metric-change/terminal samples, not freshly reconstructed at every frame.
    """

    def __init__(self, geometry: dict[str, object], target: dict[str, object]) -> None:
        area, _header, footer, rail = list_layout(geometry)
        tree = cast("dict[str, object]", geometry["uia"])
        nodes = cast("list[dict[str, object]]", tree["nodes"])
        bars = [
            node
            for node in nodes
            if node["parent"] == 0
            and node["control_type"] == _SCROLLBAR_ROLE
            and node["offscreen"] is False
        ]
        self.handles: list[int] = []
        for bounds in (rail, footer):
            if bounds is None:
                continue
            matches = [node for node in bars if raw_bounds(node["bounds"]) == bounds]
            if len(matches) != 1 or matches[0].get("depth") != 1:
                raise RuntimeError("Whole-image oracle lacks raw native rail identity")
            handle = matches[0].get("native_handle")
            if type(handle) is not int or handle <= 0 or handle in self.handles:
                raise RuntimeError("Whole-image oracle lacks raw native rail identity")
            self.handles.append(handle)
        if rail is None or nodes[0].get("offscreen") is not False:
            raise RuntimeError("Whole-image oracle lacks raw native List geometry")
        if raw_bounds(nodes[0]["bounds"]) != area:
            raise RuntimeError("Whole-image oracle native List bounds changed")
        if not isinstance(target, dict):
            raise TypeError("Whole-image oracle has malformed raw native target")
        self.horizontal = cast("dict[str, object]", target["initial_position"])[
            "horizontal"
        ]
        self.initial = self._rails(target.get("initial_native"), identity=True)
        if any(state["pos"] != state["min"] for state in self.initial[1:]):
            raise RuntimeError("Whole-image oracle native horizontal origin changed")
        if target.get("area") != geometry["captured_bounds"] or any(
            state["root"] != target.get("window") for state in self.initial
        ):
            raise RuntimeError(
                "Whole-image oracle native owner differs from the raw target"
            )
        self.original: list[dict[str, int]] = []
        self.previous: list[dict[str, int]] = []
        self.previous_view: object = None

    def _rails(self, value: object, *, identity: bool = False) -> list[dict[str, int]]:
        if not isinstance(value, list) or any(not isinstance(r, dict) for r in value):
            raise RuntimeError("Whole-image oracle lacks native frame evidence")
        rows = cast("list[dict[str, object]]", value)
        bars = [row for row in rows if row.get("bar") == _NATIVE_BAR]
        if (
            len(bars) != len(self.handles)
            or any(
                type(row.get("native_handle")) is not int
                or type(row.get("bar")) is not int
                for row in bars
            )
            or {row["native_handle"] for row in bars} != set(self.handles)
        ):
            raise RuntimeError("Whole-image oracle native rail identity changed")
        result = []
        keys = ("native_handle", "min", "max", "page", "pos")
        if identity:
            keys += ("parent", "root", "pid")
        for handle in self.handles:
            row = next(row for row in bars if row["native_handle"] == handle)
            if not (
                all(type(row.get(key)) is int for key in keys)
                and all(
                    row.get(key) is True
                    for key in ("success", "window_valid", "root_matches")
                )
            ):
                raise RuntimeError(
                    "Whole-image oracle has invalid native frame evidence"
                )
            state = {key: cast("int", row[key]) for key in keys}
            minimum, maximum, page, position = (
                state[key] for key in ("min", "max", "page", "pos")
            )
            if not (
                (
                    not identity
                    or all(state[key] > 0 for key in ("parent", "root", "pid"))
                )
                and 0 < page < maximum - minimum
                and minimum <= position <= maximum - page + 1
            ):
                raise RuntimeError("Whole-image oracle has invalid native range")
            result.append(state)
        return result

    def observe(self, position: dict[str, object], *, final: bool) -> None:
        """Require raw identity/ranges, independent mapping and a stable endpoint."""
        states = self._rails(position.get("native_scroll_info"))
        vertical = states[0]
        minimum, maximum, page, current = (
            vertical[key] for key in ("min", "max", "page", "pos")
        )
        percent = _FULL_PERCENT * (current - minimum) / (maximum - page + 1 - minimum)
        view = _FULL_PERCENT * page / (maximum - minimum)
        if not (
            _same_float(position["vertical"], percent)
            and _same_float(position["view_size"], view)
            and position["horizontal"] == self.horizontal
            and (len(states) == 1 or states[1]["pos"] == states[1]["min"])
        ):
            raise RuntimeError("Whole-image oracle native/UIA mapping differs")
        if not self.original:
            self.original = states
            if any(
                state[key] != initial[key]
                for state, initial in zip(states, self.initial, strict=True)
                for key in ("native_handle", "min", "max", "page")
            ):
                raise RuntimeError("Whole-image oracle native initial range changed")
            if current != minimum:
                raise RuntimeError(
                    "Whole-image oracle native sequence did not start at minimum"
                )
        else:
            fixed = ("native_handle", "min", "max")
            if (
                any(vertical[key] != self.original[0][key] for key in fixed)
                or states[1:] != self.original[1:]
                or current <= self.previous[0]["pos"]
            ):
                raise RuntimeError("Whole-image oracle native continuity changed")
        if final and (
            current != maximum - page + 1 or position["vertical"] != _FULL_PERCENT
        ):
            raise RuntimeError("Whole-image oracle lacks an exact native endpoint")
        changed = bool(self.previous) and (
            page != self.previous[0]["page"]
            or position["view_size"] != self.previous_view
        )
        if final or changed:
            repeated = self._rails(
                position.get("native_scroll_info_after"), identity=True
            )
            if any(
                state[key] != repeat[key]
                for state, repeat in zip(states, repeated, strict=True)
                for key in state
            ) or any(
                initial[key] != repeat[key]
                for initial, repeat in zip(self.initial, repeated, strict=True)
                for key in ("native_handle", "parent", "root", "pid")
            ):
                raise RuntimeError(
                    "Whole-image oracle native repeated observation changed"
                )
        elif "native_scroll_info_after" in position:
            raise RuntimeError(
                "Whole-image oracle has misplaced repeated native evidence"
            )
        self.previous = states
        self.previous_view = position["view_size"]


def _same_float(value: object, expected: float) -> bool:
    """Allow one adjacent float for operation ordering, not a measurement tolerance."""
    return type(value) in (int, float) and value in (
        expected,
        math.nextafter(expected, -math.inf),
        math.nextafter(expected, math.inf),
    )


def _textured_band(before: list[bytes], after: list[bytes]) -> bool:
    run: set[bytes] = set()
    length = 0
    for old, new in zip(before, after, strict=True):
        if old != new:
            run.clear()
            length = 0
        else:
            run.add(new)
            length += 1
            if length >= _BAND_ROWS and len(run) >= _TEXTURE_ROWS:
                return True
    return False


def _native_displacement(before: list[bytes], after: list[bytes], width: int) -> int:
    """Search every shift independently before consulting either raw item delta."""
    left = [row[: width * 4] for row in before]
    right = [row[: width * 4] for row in after]
    candidates = [
        shift
        for shift in range(1, len(before) - _BAND_ROWS + 1)
        if left[shift:] == right[:-shift]
        and _textured_band(before[shift:], after[:-shift])
    ]
    if len(candidates) != 1:
        raise RuntimeError(
            "Whole-image oracle lacks one unique native pixel displacement"
        )
    return candidates[0]


class _HeaderOracle:
    """Map raw Header geometry and independent retained-item displacement evidence."""

    def __init__(self, path: Path, *, native: bool) -> None:
        geometry = json.loads(path.read_text(encoding="utf-8"))
        self.area, self.header, footer, _rail = list_layout(geometry)
        self.native = (
            _NativeListOracle(
                geometry, json.loads((path.parent / "target.json").read_text())
            )
            if native
            else None
        )
        self._previous_body: list[bytes] = []
        self._top = 0
        self.height = round(self.header.height)
        self.footer_height = 0 if footer is None else round(footer.height)
        self._body = Rect(
            self.area.left,
            self.header.bottom,
            self.header.width,
            self.area.bottom - self.header.bottom - self.footer_height,
        )
        self._horizontal: object = None
        self._prefix: list[bytes] | None = None
        self._footer: list[bytes] | None = None

    def terminal_size(self, original: float, terminal: float, height: int) -> None:
        """Check both independent total extents without using stitcher decisions."""
        if not math.isfinite(terminal) or not 0.0 < terminal < _FULL_PERCENT:
            raise RuntimeError("Whole-image oracle terminal view size is invalid")
        fixed = self.height + self.footer_height
        first = fixed + self._body.height * _FULL_PERCENT / original
        last = fixed + self._body.height * _FULL_PERCENT / terminal
        # The prototype's existing extent tolerance, not an exact offset source.
        if (self.native is None and abs(last - first) > max(2.0, first * 0.02)) or any(
            abs(height - expected) > max(2.0, expected * 0.02)
            for expected in (first, last)
        ):
            raise RuntimeError("Whole-image oracle terminal extent changed")

    def _visible_item(self, raw: Rect, recorded: object, horizontal: object) -> bool:
        """Re-derive allowed visible hit geometry without the live witness helper."""
        if raw.is_empty:
            return False
        visible = raw.intersected(self._body)
        if visible == raw:
            if recorded is not None and raw_bounds(recorded) != visible:
                return False
        elif not (
            self.footer_height > 0
            and self._horizontal == horizontal == 0.0
            and self._body.left <= raw.left < self._body.right < raw.right
            and self._body.top <= raw.top < raw.bottom <= self._body.bottom
            and not visible.is_empty
            and recorded is not None
            and raw_bounds(recorded) == visible
        ):
            return False
        x, y = round(visible.center.x), round(visible.center.y)
        return visible.left < x < visible.right and visible.top < y < visible.bottom

    def offset(self, position: dict[str, object], index: int, previous: int) -> int:
        """Derive pixel offsets from two agreeing raw geometry deltas, never seams."""
        value = position.get("native_item_witness")
        if value is None:
            raise RuntimeError("Whole-image oracle lacks native item witnesses")
        witness = cast("dict[str, object]", value)
        items = cast("list[dict[str, object]]", witness["items"])
        if index == 0:
            self._horizontal = position["horizontal"]
            if witness["from_frame"] is not None or items:
                raise RuntimeError("Whole-image oracle has an invalid first witness")
            return 0
        if witness["from_frame"] != index - 1 or len(items) != _PAIR_SIZE:
            raise RuntimeError("Whole-image oracle has an incomplete witness pair")
        before = []
        deltas = []
        for item in items:
            first, last = raw_bounds(item["before"]), raw_bounds(item["after"])
            if (
                item["same_identity"] is not True
                or item["same_parent"] is not True
                or not self._visible_item(
                    first, item.get("visible_before"), position["horizontal"]
                )
                or not self._visible_item(
                    last, item.get("visible_after"), position["horizontal"]
                )
                or (first.x, first.width, first.height)
                != (last.x, last.width, last.height)
            ):
                raise RuntimeError(
                    "Whole-image oracle has invalid retained-item geometry"
                )
            before.append(first)
            deltas.append(first.y - last.y)
        first, second = before
        # Re-derive geometric separation independently of the production List
        # helper. Either axis can distinguish two retained rectangular items.
        separated = (
            first.right <= second.left
            or second.right <= first.left
            or first.bottom <= second.top
            or second.bottom <= first.top
        )
        if (
            not separated
            or deltas[0] != deltas[1]
            or not 0 < deltas[0] < self._body.height
            or deltas[0] != round(deltas[0])
        ):
            raise RuntimeError("Whole-image oracle witnesses disagree on displacement")
        return previous + round(deltas[0])

    def rows(
        self, frame: QImage, result: list[bytes], matched: bytearray, top: int
    ) -> list[bytes]:
        """Verify the original prefix exactly once and every actual Header pixel."""
        if frame.width() != self.area.width or frame.height() != self.area.height:
            raise RuntimeError("Whole-image oracle disagrees with raw Header viewport")
        rows = _rows(frame)
        prefix = [row[: round(self.header.width) * 4] for row in rows[: self.height]]
        if self._prefix is None:
            if result[: self.height] != rows[: self.height]:
                raise RuntimeError(
                    "Whole-image oracle did not preserve the original prefix"
                )
            matched[: self.height] = b"\1" * self.height
            self._prefix = prefix
        elif prefix != self._prefix:
            raise RuntimeError("Whole-image oracle Header changed during acquisition")
        if self.footer_height:
            suffix = rows[-self.footer_height :]
            if result[-self.footer_height :] != suffix or (
                self._footer is not None and self._footer != suffix
            ):
                raise RuntimeError("Whole-image oracle footer or corner changed")
            self._footer = suffix
            matched[-self.footer_height :] = b"\1" * self.footer_height
        bottom = len(rows) - self.footer_height
        body = rows[self.height : bottom]
        if self.native is not None and self._previous_body:
            measured = _native_displacement(
                self._previous_body, body, round(self.header.width)
            )
            if measured != top - self._top:
                raise RuntimeError(
                    "Whole-image oracle raw item and pixel displacements differ"
                )
        self._previous_body, self._top = body, top
        return body


def _rows(image: QImage) -> list[bytes]:
    converted = image.convertToFormat(QImage.Format.Format_RGB32)
    data = bytes(converted.constBits())
    stride = converted.bytesPerLine()
    return [data[y * stride : (y + 1) * stride] for y in range(image.height())]


def _verify_view_size(
    original: float,
    position: dict[str, object],
    header: _HeaderOracle | None,
    *,
    final: bool,
    height: int,
) -> None:
    current = cast("float", position["view_size"])
    if header is not None and header.native is not None:
        header.native.observe(position, final=final)
        if final:
            header.terminal_size(original, current, height)
        return
    if current == original:
        return
    if header is None or not final:
        raise RuntimeError("Whole-image oracle frame geometry is inconsistent")
    header.terminal_size(original, current, height)


def _native_observations(
    geometry: dict[str, object],
    target: dict[str, object],
    positions: list[dict[str, object]],
) -> list[object]:
    """Collect raw receipts; missing evidence does not mean unsupported."""
    sources = [(geometry, "scroll_info"), (target, "initial_native")]
    sources.extend(
        (position, key)
        for position in positions
        for key in ("native_scroll_info", "native_scroll_info_after")
    )
    return [source[key] for source, key in sources if key in source]


def _original_horizontal(
    geometry: dict[str, object],
    target: dict[str, object],
    positions: list[dict[str, object]],
) -> float:
    values = [position["horizontal"] for position in positions]
    for source, key in ((geometry, "position"), (target, "initial_position")):
        if key in source:
            observed = cast("dict[str, object]", source[key])
            values.append(observed["horizontal"])
    if any(
        type(value) not in (int, float)
        or not math.isfinite(cast("float", value))
        or (value != -1.0 and not 0.0 <= cast("float", value) <= _FULL_PERCENT)
        or value != values[0]
        for value in values
    ):
        raise RuntimeError("Whole-image oracle original horizontal evidence changed")
    return cast("float", values[0])


def _unsupported_native_rail(observations: list[object], allowed: set[int]) -> None:
    """An explicit absent V HWND is unsupported, not a failed or missing native read."""
    for value in observations:
        if not isinstance(value, list) or any(
            not isinstance(row, dict) for row in value
        ):
            raise RuntimeError(
                "Whole-image oracle has malformed native support evidence"
            )
        rows = cast("list[dict[str, object]]", value)
        if any(
            row.get("bar") == _NATIVE_BAR and row.get("native_handle") not in allowed
            for row in rows
        ):
            raise RuntimeError("Whole-image oracle raw rail contradicts native support")


def _native_selected(
    geometry: dict[str, object],
    target: dict[str, object],
    positions: list[dict[str, object]],
) -> bool:
    """Select from raw layout, original H and support, never product flags."""
    if not isinstance(target, dict):
        raise TypeError("Whole-image oracle has malformed raw native target")
    horizontal = _original_horizontal(geometry, target, positions)
    _area, _header, footer, rail = list_layout(geometry)
    if (
        horizontal not in (-1.0, 0.0)
        or rail is None
        or (horizontal == -1.0 and footer is not None)
    ):
        return False
    tree = cast("dict[str, object]", geometry["uia"])
    nodes = cast("list[dict[str, object]]", tree["nodes"])
    bars = [
        node
        for node in nodes
        if node["parent"] == 0
        and node["control_type"] == _SCROLLBAR_ROLE
        and node["offscreen"] is False
    ]
    vertical = next(node for node in bars if raw_bounds(node["bounds"]) == rail)
    observations = _native_observations(geometry, target, positions)
    if "native_handle" not in vertical:
        if observations or any("native_handle" in node for node in nodes):
            raise RuntimeError("Whole-image oracle lacks raw native support identity")
        return False  # Historical raw-geometry-only traces retain the strict oracle.
    handle = vertical["native_handle"]
    if handle is None or (type(handle) is int and handle == 0):
        allowed = {
            cast("int", node["native_handle"])
            for node in bars
            if footer is not None
            and raw_bounds(node["bounds"]) == footer
            and type(node.get("native_handle")) is int
            and cast("int", node["native_handle"]) > 0
        }
        _unsupported_native_rail(observations, allowed)
        return False
    if type(handle) is not int or handle < 0:
        raise RuntimeError(
            "Whole-image oracle has malformed raw native support identity"
        )
    if "position" not in geometry or "initial_position" not in target:
        raise RuntimeError(
            "Whole-image oracle lacks original native selection evidence"
        )
    return True


def _oracle_header(
    output: Path, positions: list[dict[str, object]]
) -> _HeaderOracle | None:
    path = output / "header-geometry.json"
    target_path = output / "target.json"
    try:
        target = json.loads(target_path.read_text()) if target_path.is_file() else {}
        if not path.is_file():
            return _without_header(target, positions)
        geometry = json.loads(path.read_text())
        return _HeaderOracle(path, native=_native_selected(geometry, target, positions))
    except (KeyError, TypeError, ValueError, OSError) as error:
        raise RuntimeError(
            "Whole-image oracle has malformed raw geometry evidence"
        ) from error


def _without_header(
    target: dict[str, object], positions: list[dict[str, object]]
) -> None:
    if _native_observations({}, target, positions):
        raise RuntimeError("Whole-image oracle lacks raw native Header geometry")


def verify_sequence(image: QImage, output: Path) -> dict[str, object]:
    """Check every output row against acquired pixels at UIA-derived positions.

    This deliberately does not call the stitcher, use its seam or inferred
    displacement, or treat image acceptance as proof. It can reject a sequence
    whose provider geometry is imprecise. It does not prove hidden semantics or
    make repeated native scrollbars disappear.
    """
    if image.isNull() or image.width() * image.height() > MAX_PIXELS:
        raise RuntimeError("Whole-image oracle output exceeds the image limit")
    positions = json.loads((output / "frames.json").read_text(encoding="utf-8"))
    if not 1 < len(positions) <= MAX_FRAMES:
        raise RuntimeError("Whole-image oracle needs the bounded complete sequence")
    if positions[0]["vertical"] != 0.0 or positions[-1]["vertical"] != _FULL_PERCENT:
        raise RuntimeError("Whole-image oracle did not observe both document ends")
    result = _rows(image)
    matched = bytearray(image.height())
    total_pixels, last_top, viewport = 0, -1, 0
    first = positions[0]
    header = _oracle_header(output, positions)
    for index, position in enumerate(positions):
        frame = QImage(str(output / f"frame-{index}.png"))
        viewport = viewport or frame.height()
        total_pixels += frame.width() * frame.height()
        _verify_view_size(
            first["view_size"],
            position,
            header,
            final=index == len(positions) - 1,
            height=image.height(),
        )
        if (
            frame.isNull()
            or frame.width() != image.width()
            or frame.height() != viewport
            or total_pixels > MAX_PIXELS
            or position["horizontal"] != first["horizontal"]
            or not 0 < first["view_size"] < _FULL_PERCENT
            or (index and position["vertical"] <= positions[index - 1]["vertical"])
        ):
            raise RuntimeError("Whole-image oracle frame geometry is inconsistent")
        # Independent absolute mapping, not accumulated stitch displacements.
        top = (
            header.offset(position, index, last_top)
            if header is not None
            else round(
                frame.height()
                * (_FULL_PERCENT / position["view_size"] - 1.0)
                * position["vertical"]
                / _FULL_PERCENT
            )
        )
        if top <= last_top or top + frame.height() > image.height():
            raise RuntimeError("Whole-image oracle positions disagree with output")
        last_top = top
        rows = (
            header.rows(frame, result, matched, top)
            if header is not None
            else _rows(frame)
        )
        for row, pixels in enumerate(
            rows, start=top + (header.height if header else 0)
        ):
            if pixels == result[row]:
                matched[row] = 1
    if last_top + viewport != image.height() or not all(matched):
        missing = len(matched) - sum(matched)
        raise RuntimeError(f"Whole-image oracle found {missing} unverified output rows")
    return {
        "oracle": (
            "all output rows match native frames at retained-item UIA positions"
            if header is not None
            else "all output rows match native frames at UIA-derived positions"
        ),
        "oracle_frames": len(positions),
        "oracle_verified_rows": len(matched),
    }
