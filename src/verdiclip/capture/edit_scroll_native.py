"""Isolated vertical-only EDIT research adapter, never application dispatch.

Only a retained own-HWND ScrollPattern may write. All window messages are fixed,
read-only, bounded SendMessageTimeoutW calls. The existing ownership primitives
retain native outputs before any post-call failure can escape. An external
owned-process watchdog remains mandatory because COM cannot be interrupted here.
"""

from __future__ import annotations

import copy
import ctypes as c
import hashlib
import json
import math
import sys
import time
from collections.abc import Callable
from contextlib import suppress
from typing import Any, cast, override

from verdiclip.capture.edit_geometry import DPI, contains, rectangle
from verdiclip.capture.edit_geometry import Rect as Rectangle
from verdiclip.capture.edit_native import EditObservation, ScrollBarInfo, ScrollInfo
from verdiclip.capture.edit_scroll_contract import TARGET_STYLE as STYLE
from verdiclip.capture.edit_scroll_contract import (
    TEXT,
    character_point,
    validate_font_attestation,
)
from verdiclip.capture.observe_native import LastInput, NativeObservation, Point, Rect
from verdiclip.capture.observe_protocol import ObserveTarget

CLIENT = (0, 0, 493, 413)
CLIENT_SCREEN = (60, 60, 553, 473)
ROOT_CLIENT = (40, 40, 573, 701)
LINES = 38
LINE_LENGTH = 43
LINE_STRIDE = LINE_LENGTH + 2
PITCH = 16
BOTTOM = 13
PERCENTAGES = (0.0, 100 * 8 / 13, 100.0, 100 * 3 / 13)
CALL_SECONDS = 3.0
MESSAGE_FLAGS = 0x01 | 0x02 | 0x20
FULL_PERCENT = 100
MAX_SNAPSHOT_BYTES = 16_384
TEXT_DIGEST = hashlib.sha256(TEXT.encode("utf-16-le")).hexdigest()
_WM_GETTEXT = 0x000D
_WM_GETTEXTLENGTH = 0x000E
_WM_GETFONT = 0x0031
_EM_GETSEL = 0x00B0
_EM_GETLINECOUNT = 0x00BA
_EM_LINEINDEX = 0x00BB
_EM_GETFIRSTVISIBLELINE = 0x00CE
_EM_GETMARGINS = 0x00D4
_EM_POSFROMCHAR = 0x00D6
_MESSAGES = frozenset(
    (
        _WM_GETTEXT,
        _WM_GETTEXTLENGTH,
        _WM_GETFONT,
        _EM_GETSEL,
        _EM_GETLINECOUNT,
        _EM_LINEINDEX,
        _EM_GETFIRSTVISIBLELINE,
        _EM_GETMARGINS,
        _EM_POSFROMCHAR,
    )
)


_DIAGNOSTIC_MAX_BYTES = 24_576
_DIAGNOSTIC_DEPTH = 12
_DIAGNOSTIC_STRING = 256
_DIAGNOSTIC_SEQUENCE = 64
_DIAGNOSTIC_MAPPING = 128
_PATTERN_NAMES = (
    "horizontal_percent",
    "vertical_percent",
    "horizontal_view",
    "vertical_view",
    "horizontal_scrollable",
    "vertical_scrollable",
)
_DIAGNOSTIC_FIELDS = (
    "process_guard",
    "association",
    "panel_runtime_id",
    "root_runtime_id",
    "panel_structure",
    "root_structure",
    "compare_elements",
    "foreground",
    "visible",
    "root_dpi",
    "child_thread",
    "child_pid",
    "parent",
    "gui",
    "class_name",
    "style",
    "ex_style",
    "child_dpi",
    "client_local",
    "window",
    "client_screen",
    "root_client",
    "occlusion",
    "hit_tests",
    "font_attestation",
    "font_token",
    "text_length",
    "text_result",
    "text_buffer",
    "text_terminator",
    "line_count",
    "margins",
    "selection",
    "vertical_bar",
    "scroll_info",
    "pattern_presence",
    "input_tick",
    "monitors",
    "fixed",
    "first_visible_line",
    "characters",
    "character_points",
    "message",
    "native_call",
    "seek",
)


def _diagnostic_value(value: object, depth: int = 0) -> object:  # noqa: C901, PLR0911 - bounded failure-only scalar conversion
    if depth > _DIAGNOSTIC_DEPTH:
        return {"status": "unavailable", "reason": "depth-bound"}
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float:
        result = {"type": "float", "repr": repr(value), "hex": value.hex()}
        if not math.isfinite(value):
            result["nonfinite"] = repr(value)
        return result
    if type(value) is str:
        return (
            value
            if len(value) <= _DIAGNOSTIC_STRING
            else {
                "type": "str",
                "prefix": value[:_DIAGNOSTIC_STRING],
                "length": len(value),
            }
        )
    if type(value) is bytes:
        return {
            "type": "bytes",
            "prefix_hex": value[:_DIAGNOSTIC_SEQUENCE].hex(),
            "length": len(value),
        }
    if type(value) in (list, tuple):
        sequence = cast("list[object] | tuple[object, ...]", value)
        if len(sequence) > _DIAGNOSTIC_SEQUENCE:
            return {"status": "unavailable", "reason": "sequence-bound"}
        return [_diagnostic_value(item, depth + 1) for item in sequence]
    if type(value) is dict:
        mapping = cast("dict[str, object]", value)
        if len(mapping) > _DIAGNOSTIC_MAPPING or any(
            type(key) is not str or len(key) > _DIAGNOSTIC_MAPPING for key in mapping
        ):
            return {"status": "unavailable", "reason": "mapping-bound"}
        return {
            key: _diagnostic_value(item, depth + 1) for key, item in mapping.items()
        }
    return {"status": "unavailable", "type": type(value).__name__[:64]}


def retain_native_diagnostic(error: BaseException) -> None:
    """Format a constructor/snapshot failure after the caller's cutoff admission.

    The frozen source is independent of the live object, cleanup and any later
    restoration. The caller must check its original phase/reservation clock
    before and after this failure-only bounded conversion.
    """
    if hasattr(error, "native_diagnostic"):
        return
    frozen = getattr(error, "_native_diagnostic_frozen", None)
    if frozen is None:
        return
    try:
        diagnostic = cast("dict[str, object]", _diagnostic_value(frozen))
        for section in ("frozen_baseline", "last_successful_snapshot", "current"):
            if (
                len(
                    json.dumps(
                        diagnostic, allow_nan=False, separators=(",", ":")
                    ).encode("utf-8")
                )
                <= _DIAGNOSTIC_MAX_BYTES
            ):
                break
            diagnostic[section] = {
                "status": "unavailable",
                "reason": "diagnostic-size-bound",
            }
        cast("Any", error).native_diagnostic = diagnostic
    except Exception as diagnostic_error:  # noqa: BLE001 - diagnostics never replace the original error
        with suppress(Exception):
            error.add_note(
                f"Native diagnostic unavailable: {type(diagnostic_error).__name__}"
            )


class GuiThreadInfo(c.Structure):
    """GUITHREADINFO explicitly distinguishes focus and caret HWNDs."""

    _fields_ = [
        ("size", c.c_uint32),
        ("flags", c.c_uint32),
        *[
            (name, c.c_void_p)
            for name in (
                "active",
                "focus",
                "capture",
                "menu_owner",
                "move_size",
                "caret",
            )
        ],
        ("caret_rect", Rect),
    ]


class NativeStateMismatchError(ValueError):
    """Fully read scroll state disagrees while all frozen guards remain healthy."""


class ScrollObservation(EditObservation):
    """Retain one exact vertical EDIT and one frozen target ownership epoch."""

    def __init__(
        self,
        target: ObserveTarget,
        child: int,
        budget: Any,  # noqa: ANN401 - original native ownership budget protocol
        *,
        font_attestation: object,
        epoch: str,
    ) -> None:
        self._begin_diagnostic()
        budget.check()
        attestation = validate_font_attestation(
            font_attestation,
            epoch=epoch,
            pid=target.pid,
            creation=target.process_created_100ns,
            role="target",
            root=target.hwnd,
            child=child,
        )
        self._font_attestation_json = json.dumps(
            attestation, sort_keys=True, allow_nan=False, separators=(",", ":")
        )
        budget.check()
        self.unsafe_write_reason: str | None = None
        self._write_admission: Callable[[], None] | None = None
        super().__init__(target, child, budget)

    def _begin_diagnostic(self) -> None:
        self._diagnostic_current: dict[str, Any] = {
            field: {"status": "unattempted"}
            for field in (
                *_DIAGNOSTIC_FIELDS,
                *(f"pattern.{name}" for name in _PATTERN_NAMES),
                *(f"line_index.{line}" for line in range(LINES)),
            )
        }
        self._diagnostic_predicate: str | None = None
        self._diagnostic_operation: str | None = None
        self._observed(
            "expected",
            {
                "style": STYLE,
                "ex_style": 0,
                "dpi": DPI,
                "client_local": CLIENT,
                "client_screen": CLIENT_SCREEN,
                "root_client": ROOT_CLIENT,
                "scroll_range_page": (0, 37, 25),
                "position_bound": (0, BOTTOM),
                "pitch": PITCH,
                "text_length": len(TEXT),
                "line_count": LINES,
            },
        )
        if hasattr(self, "_last_seek_request"):
            self._observed("seek", self._last_seek_request)

    def _attempt(self, field: str, request: object = None) -> None:
        if not hasattr(self, "_diagnostic_current"):
            self._begin_diagnostic()
        self._diagnostic_operation = field
        self._diagnostic_current[field] = {"status": "attempted-no-output"}
        if request is not None:
            self._diagnostic_current[field]["request"] = request

    def _observed(self, field: str, value: object) -> None:
        if not hasattr(self, "_diagnostic_current"):
            self._begin_diagnostic()
        self._diagnostic_current[field] = {"status": "observed", "value": value}

    def _read[T](self, field: str, operation: Callable[[], T]) -> T:
        self._attempt(field)
        value = operation()
        self._observed(field, value)
        return value

    def _reject(self, predicate: str, rejected: Any) -> Any:  # noqa: ANN401 - preserve original boolean or native integer truth value
        if rejected and self._diagnostic_predicate is None:
            self._diagnostic_predicate = predicate
        return rejected

    def _freeze_diagnostic(self, error: BaseException) -> None:
        # Failure-only bounded detachment. No native calls, repr/hex formatting,
        # serialization or hashing runs before the outer phase-cutoff admission.
        if hasattr(error, "_native_diagnostic_frozen"):
            return
        try:
            unavailable = {"status": "unavailable"}
            cast("Any", error)._native_diagnostic_frozen = copy.deepcopy(
                {
                    "schema": "owned-edit-scroll-native-diagnostic-v1",
                    "first_failed_predicate": getattr(
                        self, "_diagnostic_predicate", None
                    )
                    or unavailable,
                    "interrupted_operation": getattr(
                        self, "_diagnostic_operation", None
                    )
                    or unavailable,
                    "current": getattr(self, "_diagnostic_current", unavailable),
                    "last_successful_snapshot": getattr(
                        self, "_last_native_snapshot", unavailable
                    ),
                    "frozen_baseline": getattr(self, "_fixed_baseline", unavailable),
                }
            )
        except Exception as diagnostic_error:  # noqa: BLE001 - diagnostics never replace the original error
            with suppress(Exception):
                error.add_note(
                    f"Native diagnostic unavailable: {type(diagnostic_error).__name__}"
                )

    def retain_diagnostic(self, error: BaseException) -> None:
        """Format the first frozen evidence only after the caller admits its budget."""
        self._freeze_diagnostic(error)
        retain_native_diagnostic(error)

    def _unsafe(self, error: BaseException | str) -> None:
        if self.unsafe_write_reason is None:
            self.unsafe_write_reason = str(error) or type(error).__name__

    def _latch(self, error: Exception) -> None:
        self._unsafe(error)
        self.budget.failure = self.budget.failure or error

    @override
    def raw(self, function: Any, *args: object) -> Any:
        """Keep acquisitions retainable even when synchronous postchecks fail."""
        try:
            self.budget.check()
            started = time.monotonic()
            self._call_deadline = started + CALL_SECONDS
            name = getattr(function, "__name__", "COM")
            self._attempt("native_call", {"name": name})
            self.edit_emit({"event": "call_start", "name": name})
            self.budget.check()
            if time.monotonic() - started >= CALL_SECONDS:
                raise RuntimeError("Native start logging >=3s")  # noqa: TRY301 - latch elapsed logging failure
            if self._write_admission is not None:
                admit, self._write_admission = self._write_admission, None
                admit()
                self.budget.check()
            result = NativeObservation.raw(self, function, *args)
        except Exception as error:
            self._latch(error)
            raise
        try:
            # Diagnostic storage is a post-call step too: a returned owner must
            # still reach its acquisition caller if this recording ever fails.
            self._observed("native_call", {"name": name, "result": result})
            self.edit_emit({"event": "call_end", "seconds": time.monotonic() - started})
            if time.monotonic() - started >= CALL_SECONDS:
                raise RuntimeError("Native call/logging >=3s")  # noqa: TRY301 - retain acquired native output
            self.budget.check()
        except Exception as error:  # noqa: BLE001 - retain ownership before surfacing post-call failure
            # A HANDLE, interface, SAFEARRAY, DC or selected object may have
            # arrived. The caller MUST first register its cleanup ownership.
            self._latch(error)
        return result

    @override
    def _edit_apis(self) -> None:
        super()._edit_apis()
        # No gdi32 is loaded here. The foreign WM_GETFONT token must never be
        # queried, selected, copied or otherwise consumed by this process's GDI.
        signatures = {
            "SendMessageTimeoutW": (
                [
                    c.c_void_p,
                    c.c_uint32,
                    c.c_size_t,
                    c.c_ssize_t,
                    c.c_uint32,
                    c.c_uint32,
                    c.POINTER(c.c_size_t),
                ],
                c.c_ssize_t,
            ),
            "GetGUIThreadInfo": ([c.c_uint32, c.POINTER(GuiThreadInfo)], c.c_int32),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.user, name)
            function.argtypes, function.restype = arguments, result

    def _message(self, message: int, wp: int = 0, lp: int = 0) -> int:
        """Private fixed read-only ABI; never a caller-selected HWND or write."""
        if message not in _MESSAGES:
            raise ValueError("Message is outside fixed read-only EDIT protocol")
        self.budget.check()
        self._attempt("message", {"message": message, "index": wp})
        timeout = 0
        # The result buffer is DWORD_PTR, not LRESULT, BOOL or c_ulong.
        result = c.c_size_t()

        def send() -> int:
            nonlocal timeout
            # Derive the timeout AFTER call-start logging, at actual dispatch.
            self.budget.check()
            timeout = math.floor(
                (min(self._call_deadline, self.budget.deadline) - time.monotonic())
                * 1000
            )
            if timeout <= 0:
                raise RuntimeError("No whole millisecond remains for EDIT read")
            return int(
                self.user.SendMessageTimeoutW(
                    self.child, message, wp, lp, MESSAGE_FLAGS, timeout, c.byref(result)
                )
            )

        send.__name__ = "SendMessageTimeoutW"
        success = self.raw(send)
        if success:
            self._observed(
                "message",
                {
                    "message": message,
                    "index": wp,
                    "result": int(result.value),
                    "timeout_ms": timeout,
                },
            )
        if not success:
            error = RuntimeError("EDIT read timeout/failure; native outcome uncertain")
            self._latch(error)
            raise error
        try:
            self.budget.check()
            value = int(result.value)
            self.edit_emit(
                {
                    "event": "edit_read",
                    "message": message,
                    "index": wp,
                    "result": value,
                    "timeout_ms": timeout,
                }
            )
            self.budget.check()
        except Exception as error:
            self._freeze_diagnostic(error)
            self._latch(error)
            raise
        return value

    def _line_index(self, line: int) -> int:
        if type(line) is not int or not 0 <= line < LINES:
            raise ValueError("Invalid fixed line index")
        raw = self._read(
            f"line_index.{line}", lambda: self._message(_EM_LINEINDEX, line)
        )
        signed = c.c_ssize_t(raw).value
        if self._reject("line_index.negative", signed < 0) or self._reject(
            "line_index.frozen_index", raw != line * LINE_STRIDE
        ):
            raise ValueError("Native EDIT line index disagrees with frozen text")
        return raw

    def _point(self, character: int) -> tuple[int, int]:
        if type(character) is not int or not 0 <= character < len(TEXT):
            raise ValueError("Invalid bounded character index")
        if not hasattr(self, "_diagnostic_points"):
            self._diagnostic_points: list[dict[str, Any]] = []
        entry: dict[str, Any] = {"index": character, "status": "attempted-no-output"}
        self._diagnostic_points.append(entry)
        self._observed("character_points", self._diagnostic_points)
        value = self._message(_EM_POSFROMCHAR, character)
        entry.update(status="observed", packed=value)
        point = character_point(value)
        entry["decoded"] = point
        # A packed signed16 value cannot establish geometry outside this frozen
        # positive client. Reject before arithmetic could conceal an overflow.
        if self._reject(
            "character.x_visible", not 0 <= point[0] < CLIENT[2]
        ) or self._reject("character.y_visible", not 0 <= point[1] < CLIENT[3]):
            raise ValueError("Character coordinate outside frozen visible client")
        return point

    def _text(self) -> dict[str, object]:
        if self._reject(
            "text.length",
            self._read("text_length", lambda: self._message(_WM_GETTEXTLENGTH))
            != len(TEXT),
        ):
            raise ValueError("Frozen EDIT text length changed")
        buffer = (c.c_uint16 * (len(TEXT) + 1))()
        length = self._read(
            "text_result",
            lambda: self._message(_WM_GETTEXT, len(buffer), c.addressof(buffer)),
        )
        data = bytes(buffer)[: len(TEXT) * 2]
        self._observed("text_buffer", data)
        self._observed("text_terminator", buffer[-1])
        if (
            self._reject("text.result_length", length != len(TEXT))
            or self._reject("text.terminator", buffer[-1] != 0)
            or self._reject("text.bytes", data != TEXT.encode("utf-16-le"))
        ):
            raise ValueError("Frozen EDIT text bytes changed")
        if self._reject(
            "text.line_count",
            self._read("line_count", lambda: self._message(_EM_GETLINECOUNT)) != LINES,
        ) or self._reject(
            "text.margins",
            self._read("margins", lambda: self._message(_EM_GETMARGINS)) != 0,
        ):
            raise ValueError("Native logical line count/margins changed")
        # Packed EM_GETSEL is unambiguous because this text is shorter than 64K.
        if self._reject(
            "text.selection",
            self._read("selection", lambda: self._message(_EM_GETSEL)) != 0,
        ):
            raise ValueError("Unexpected EDIT selection or insertion position")
        indexes = [self._line_index(line) for line in range(LINES)]
        return {
            "sha256_utf16le": hashlib.sha256(data).hexdigest(),
            "characters": length,
            "lines": LINES,
            "line_indexes": indexes,
            "margins": 0,
            "selection": [0, 0],
        }

    def _font(self) -> dict[str, Any]:
        attestation = json.loads(self._font_attestation_json)
        self._observed("font_attestation", attestation)
        token = self._read("font_token", lambda: self._message(_WM_GETFONT))
        if self._reject("font.opaque_token", token != attestation["opaque_hfont"]):
            raise ValueError("Owner-attested opaque WM_GETFONT token changed")
        return {
            "source": "owner-attested-immutable-fixture",
            "attestation": attestation,
            "wm_getfont_opaque": token,
        }

    def _retained(self) -> int:
        self._read("process_guard", self._process_guard)
        self._read("association", lambda: self._association(self.panel))
        if (
            self._reject(
                "retained.panel_runtime_id",
                self._read("panel_runtime_id", lambda: self.runtime_id(self.panel))
                != self.panel_id,
            )
            or self._reject(
                "retained.root_runtime_id",
                self._read("root_runtime_id", lambda: self.runtime_id(self.root))
                != self.root_id,
            )
            or self._reject(
                "retained.panel_structure",
                self._read("panel_structure", lambda: self.structure(self.panel))
                != self.panel_baseline,
            )
            or self._reject(
                "retained.root_structure",
                self._read("root_structure", lambda: self.structure(self.root))
                != self.root_baseline,
            )
            or self._reject(
                "retained.compare_elements",
                not self._read(
                    "compare_elements", lambda: self._same(self.panel, self.by_handle)
                ),
            )
        ):
            raise ValueError("Retained UIA identity/layout changed")
        if self._reject(
            "root.foreground",
            self._read("foreground", lambda: self.raw(self.user.GetForegroundWindow))
            != self.target.hwnd,
        ) or self._reject(
            "root.visibility",
            not self._read("visible", lambda: self._visible(self.target.hwnd)),
        ):
            raise ValueError("Root foreground/visibility changed")
        if self._reject(
            "root.dpi",
            self._read(
                "root_dpi",
                lambda: self.raw(self.user.GetDpiForWindow, self.target.hwnd),
            )
            != DPI,
        ):
            raise ValueError("Root DPI changed")
        pid = c.c_uint32()
        self._attempt("child_pid")
        self._attempt("child_thread")
        thread = self.raw(self.user.GetWindowThreadProcessId, self.child, c.byref(pid))
        self.boolean(thread)
        self._observed("child_pid", pid.value)
        self._observed("child_thread", thread)
        if (
            self._reject("child.pid", pid.value != self.target.pid)
            or self._reject(
                "child.parent",
                self._read("parent", lambda: self.raw(self.user.GetParent, self.child))
                != self.target.hwnd,
            )
            or self._reject("child.uia_hwnd", self.panel_baseline["hwnd"] != self.child)
            or self._reject("child.uia_pid", self.panel_baseline["pid"] != pid.value)
        ):
            raise ValueError("Child HWND/UIA/process association changed")
        gui = GuiThreadInfo()
        gui.size = c.sizeof(gui)
        self._attempt("gui")
        self.boolean(self.raw(self.user.GetGUIThreadInfo, thread, c.byref(gui)))
        self._observed(
            "gui",
            {
                name: getattr(gui, name)
                for name in (
                    "active",
                    "focus",
                    "caret",
                    "capture",
                    "menu_owner",
                    "move_size",
                    "flags",
                )
            }
            | {"caret_rect": tuple(gui.caret_rect.values())},
        )
        if (
            self._reject("gui.active", gui.active != self.target.hwnd)
            or self._reject("gui.focus", gui.focus != self.child)
            or self._reject("gui.caret", gui.caret != self.child)
            or self._reject("gui.capture", gui.capture)
            or self._reject("gui.menu_owner", gui.menu_owner)
            or self._reject("gui.move_size", gui.move_size)
            or self._reject("gui.flags", gui.flags)
        ):
            raise ValueError("Focused EDIT/hidden-caret/input guard changed")
        return pid.value

    def _layout(self, pid: int) -> dict[str, object]:
        name = c.create_unicode_buffer(64)
        self._attempt("class_name")
        self.boolean(self.raw(self.user.GetClassNameW, self.child, name, len(name)))
        self._observed("class_name", name.value)
        style, ex_style = (
            self._read("style", lambda: self._window_long(-16)),
            self._read("ex_style", lambda: self._window_long(-20)),
        )
        dpi = self._read(
            "child_dpi", lambda: self.raw(self.user.GetDpiForWindow, self.child)
        )
        if (
            self._reject("layout.class_name", name.value != "Edit")
            or self._reject("layout.style", style != STYLE)
            or self._reject("layout.ex_style", ex_style != 0)
            or self._reject("layout.dpi", dpi != DPI)
        ):
            raise ValueError("Vertical-only standard EDIT class/style/DPI changed")
        local, window = Rect(), Rect()
        self._attempt("client_local")
        self.boolean(self.raw(self.user.GetClientRect, self.child, c.byref(local)))
        self._observed("client_local", tuple(local.values()))
        self._attempt("window")
        self.boolean(self.raw(self.user.GetWindowRect, self.child, c.byref(window)))
        self._observed("window", tuple(window.values()))
        first, last = Point(local.left, local.top), Point(local.right, local.bottom)
        self._attempt("client_screen")
        for point in (first, last):
            self.boolean(self.raw(self.user.ClientToScreen, self.child, c.byref(point)))
        client = (first.x, first.y, last.x, last.y)
        self._observed("client_screen", client)
        root = cast("Rectangle", tuple(self._read("root_client", self._client_rect)))
        window_rect = cast("Rectangle", tuple(window.values()))
        for rect in (client, root, window_rect):
            rectangle(rect)
        if (
            self._reject("layout.client_local", tuple(local.values()) != CLIENT)
            or self._reject("layout.client_screen", client != CLIENT_SCREEN)
            or self._reject("layout.root_client", root != ROOT_CLIENT)
            or self._reject("layout.window_in_root", not contains(root, window_rect))
            or self._reject(
                "layout.client_in_window", not contains(window_rect, client)
            )
            or self._reject("layout.window_origin", window_rect[:2] != client[:2])
            or self._reject("layout.window_bottom", window_rect[3] != client[3])
        ):
            raise ValueError("Frozen client/root mapping changed")
        self._read("occlusion", lambda: self._occlusion(list(root)))
        hits: list[dict[str, object]] = []
        self._observed("hit_tests", hits)
        for x, y in (
            (client[0], client[1]),
            (client[2] - 1, client[1]),
            (client[0], client[3] - 1),
            (client[2] - 1, client[3] - 1),
            self.target.point,
        ):
            hit: dict[str, object] = {"point": (x, y), "status": "attempted-no-output"}
            hits.append(hit)
            hwnd = self.raw(self.user.WindowFromPoint, Point(x, y))
            hit.update(status="observed", hwnd=hwnd)
            if self._reject("layout.hit_test_hwnd", hwnd != self.child):
                raise ValueError("Client point ownership changed")
        return {
            "hwnd": self.child,
            "root": self.target.hwnd,
            "pid": pid,
            "creation": self.target.process_created_100ns,
            "uia_hwnd": self.panel_baseline["hwnd"],
            "uia_pid": self.panel_baseline["pid"],
            "same_element": True,
            "parent": self.target.hwnd,
            "class_name": name.value,
            "style": style,
            "ex_style": ex_style,
            "dpi": dpi,
            "client_local": list(CLIENT),
            "client_screen": list(client),
            "window": list(window_rect),
            "root_client": list(root),
        }

    def _scroll(
        self, native: dict[str, Any]
    ) -> tuple[dict[str, object], list[float | int]]:
        bar = ScrollBarInfo()
        bar.size = c.sizeof(bar)
        self._attempt("vertical_bar")
        self.boolean(self.raw(self.user.GetScrollBarInfo, self.child, -5, c.byref(bar)))
        self._observed(
            "vertical_bar",
            {
                "rect": tuple(bar.rect.values()),
                "state": list(bar.state),
                "thumb": [bar.line, bar.top, bar.bottom],
            },
        )
        vertical = tuple(bar.rect.values())
        rectangle(vertical)
        window = tuple(native["window"])
        if (
            self._reject("vertical_bar.contained", not contains(window, vertical))
            or self._reject("vertical_bar.left", vertical[0] != CLIENT_SCREEN[2])
            or self._reject("vertical_bar.top", vertical[1] != CLIENT_SCREEN[1])
            or self._reject("vertical_bar.right_bottom", vertical[2:] != window[2:])
            or self._reject("vertical_bar.visible", bar.state[0] & (0x8000 | 0x10000))
        ):
            raise ValueError("Native vertical rail geometry/visibility changed")
        native["vertical_bar"] = list(vertical)
        info = ScrollInfo()
        info.size, info.mask = c.sizeof(info), 0x17
        self._attempt("scroll_info")
        self.boolean(self.raw(self.user.GetScrollInfo, self.child, 1, c.byref(info)))
        self._observed(
            "scroll_info",
            {
                "minimum": info.minimum,
                "maximum": info.maximum,
                "page": info.page,
                "position": info.position,
                "track": info.track,
            },
        )
        if (
            self._reject(
                "scroll_info.range_page",
                (info.minimum, info.maximum, info.page) != (0, 37, 25),
            )
            or self._reject(
                "scroll_info.position_bound", not 0 <= info.position <= BOTTOM
            )
            or self._reject("scroll_info.track_bound", not 0 <= info.track <= BOTTOM)
        ):
            raise ValueError("Frozen SIF_ALL vertical range/page disagrees")
        self._observed("pattern_presence", self.pattern is not None)
        if self._reject("pattern.required", self.pattern is None):
            raise ValueError("Retained ScrollPattern is required")
        retained_pattern = cast("Any", self.pattern)
        pattern = [
            self._read(
                f"pattern.{_PATTERN_NAMES[slot - 5]}",
                lambda slot=slot: cast(
                    "float", retained_pattern.read(slot, c.c_double).value
                ),
            )
            for slot in range(5, 9)
        ] + [
            self._read(
                f"pattern.{_PATTERN_NAMES[slot - 5]}",
                lambda slot=slot: cast(
                    "int", retained_pattern.read(slot, c.c_int32).value
                ),
            )
            for slot in (9, 10)
        ]
        if (
            any(
                self._reject(
                    f"pattern.finite.{_PATTERN_NAMES[index]}", not math.isfinite(value)
                )
                for index, value in enumerate(pattern[:4])
            )
            or self._reject(
                "pattern.horizontal_percent",
                float.hex(cast("Any", pattern[0])) != "0x0.0p+0",
            )
            or self._reject("pattern.horizontal_view", pattern[2] != FULL_PERCENT)
            or self._reject("pattern.vertical_view", not 0 < pattern[3] < FULL_PERCENT)
            or self._reject("pattern.scrollable_axes", pattern[4:] != [0, 1])
        ):
            raise ValueError("Vertical-only ScrollPattern capabilities changed")
        return {
            "minimum": info.minimum,
            "maximum": info.maximum,
            "page": info.page,
            "position": info.position,
            "track": info.track,
            "bar_state": list(bar.state),
            "thumb": [bar.line, bar.top, bar.bottom],
        }, pattern

    def _characters(self, first: int, advance: int) -> dict[str, object]:
        points: dict[str, Any] = {}
        self._observed(
            "characters",
            {
                "first_visible_line": first,
                "pitch": PITCH,
                "character_advance": advance,
                "points": points,
            },
        )
        for line in dict.fromkeys((first, 13, 14)):
            index = self._line_index(line)
            start, end = self._point(index), self._point(index + LINE_LENGTH - 1)
            points[str(line)] = {
                "index": index,
                "first": list(start),
                "last": list(end),
                "right": end[0] + advance,
            }
            if (
                self._reject(
                    "characters.origin_pitch", start != (0, (line - first) * PITCH)
                )
                or self._reject(
                    "characters.last_character",
                    end != ((LINE_LENGTH - 1) * advance, start[1]),
                )
                or self._reject("characters.right_edge", end[0] + advance > CLIENT[2])
            ):
                raise ValueError(
                    "Native character pitch/origin/horizontal fit disagrees"
                )
        return {
            "first_visible_line": first,
            "line13_y": points["13"]["first"][1],
            "line14_y": points["14"]["first"][1],
            "pitch": PITCH,
            "character_advance": advance,
            "points": points,
        }

    @override
    def snapshot(self) -> dict[str, Any]:
        """Allow measured vertical state, freeze all identity/layout/input guards."""
        self._begin_diagnostic()
        self._diagnostic_points = []
        try:
            native = self._layout(self._retained())
            font, text = self._font(), self._text()
            scroll, pattern = self._scroll(native)
            tick = LastInput(c.sizeof(LastInput), 0)
            self._attempt("input_tick")
            self.boolean(self.raw(self.user.GetLastInputInfo, c.byref(tick)))
            self._observed("input_tick", tick.tick)
            monitors = self._read("monitors", self._monitors)
            if self._reject(
                "monitors.full_root_visible",
                not any(
                    m[1] <= ROOT_CLIENT[0] < ROOT_CLIENT[2] <= m[3]
                    and m[2] <= ROOT_CLIENT[1] < ROOT_CLIENT[3] <= m[4]
                    for m in monitors
                ),
            ):
                raise ValueError("Full root client not wholly on one monitor")  # noqa: TRY301 - latch failed display guard
            fixed = {
                "native": native,
                "font": font,
                "text": text,
                "input_tick": tick.tick,
                "monitors": monitors,
                "pattern_fixed": [pattern[0], *pattern[2:]],
            }
            self._observed("fixed", fixed)
            if hasattr(self, "_fixed_baseline") and self._reject(
                "epoch.fixed_baseline", fixed != self._fixed_baseline
            ):
                raise ValueError(  # noqa: TRY301 - latch failed epoch guard
                    "Frozen EDIT identity/layout/input/display epoch changed"
                )
            first = self._read(
                "first_visible_line", lambda: self._message(_EM_GETFIRSTVISIBLELINE)
            )
            if self._reject("first_visible_line.bound", not 0 <= first <= BOTTOM):
                raise ValueError("First visible line outside fixed vertical range")  # noqa: TRY301 - latch invalid native state
            characters = self._characters(
                first, font["attestation"]["owner_measured_metrics"]["average_width"]
            )
            self.budget.check()
        except Exception as error:
            self._freeze_diagnostic(error)
            self._latch(error)
            raise
        # The full read completed under healthy guards. A state mismatch alone
        # is deterministic, so the orchestrator may use its reserved restore.
        if self._reject(
            "state.position_agrees", first != scroll["position"]
        ) or self._reject(
            "state.percentage_agrees",
            not math.isclose(
                pattern[1], 100 * first / BOTTOM, rel_tol=1e-12, abs_tol=1e-9
            ),
        ):
            error = NativeStateMismatchError(
                "Native line/position/percentage disagreement"
            )
            self._freeze_diagnostic(error)
            raise error
        result = {
            **fixed,
            "scroll": scroll,
            "scroll_pattern": pattern,
            "text_layout": characters,
        }
        try:
            encoded = json.dumps(result, allow_nan=False, separators=(",", ":"))
            if self._reject(
                "snapshot.byte_bound", len(encoded.encode("utf-8")) > MAX_SNAPSHOT_BYTES
            ):
                raise ValueError("Bounded native snapshot exceeds 16KiB")  # noqa: TRY301 - serialization failures cannot admit later writes
            if not hasattr(self, "_fixed_baseline"):
                self._fixed_baseline = copy.deepcopy(fixed)
            detached = json.loads(encoded)
            self.budget.check()
        except Exception as error:
            self._freeze_diagnostic(error)
            self._latch(error)
            raise
        self._last_native_snapshot = result
        return detached

    def seek(
        self,
        percent: float,
        *,
        restoring: bool = False,
        admit_write: Callable[[], None],
    ) -> None:
        """Perform one explicit frozen percentage on the retained owned element."""
        if (
            type(percent) not in (int, float)
            or percent not in PERCENTAGES
            or type(restoring) is not bool
            or not callable(admit_write)
        ):
            raise ValueError("Invalid frozen seek request/admission callback")
        self._last_seek_request = {
            "vertical": percent,
            "horizontal": -1.0,
            "restoring": restoring,
        }
        self._observed("seek", self._last_seek_request)
        if self.unsafe_write_reason is not None:
            raise RuntimeError(f"Further writes prohibited: {self.unsafe_write_reason}")
        try:
            try:
                self.snapshot()
            except NativeStateMismatchError:
                # A restoration may correct a fully read native state mismatch,
                # but never bypass a failed identity/input/display/native guard.
                if not restoring:
                    raise
            self.edit_emit(
                {
                    "event": "seek_start",
                    "vertical": percent,
                    "horizontal": -1.0,
                    "restoring": restoring,
                }
            )
            self.budget.check()
            if self.pattern is None:
                raise ValueError("Missing retained ScrollPattern")  # noqa: TRY301 - latch failed prewrite guard
            self.unsafe_write_reason = "ScrollPattern write outcome pending"
            self._write_admission = admit_write
            status = self.pattern.call(
                4, (c.c_double, c.c_double), -1.0, float(percent)
            )
            self.hresult(status)
            if self._write_admission is not None:
                raise RuntimeError("Native write admission was not consumed")  # noqa: TRY301 - fixed COM dispatch must invoke admission
            self.budget.check()
            self.edit_emit(
                {
                    "event": "seek_end",
                    "vertical": percent,
                    "horizontal": -1.0,
                    "restoring": restoring,
                    "status": status,
                }
            )
            self.budget.check()
        except Exception as error:
            self._freeze_diagnostic(error)
            self._latch(error)
            raise
        finally:
            self._write_admission = None
        self.unsafe_write_reason = None

    @override
    def _release_now(self, owner: Any) -> None:
        primary = sys.exception()
        try:
            owner.close()
        except BaseException as error:
            self._unsafe(error)
            if primary is None:
                raise
            primary.add_note(f"Observation cleanup failed: {type(error).__name__}")

    @override
    def close(self, primary: BaseException | None = None) -> None:
        """Attempt every resource even on unsafe writes, retaining first failure."""
        if primary is not None:
            self._freeze_diagnostic(primary)
        self._check_thread()
        if self._closed:
            return
        self._closed = True
        failure = primary
        for owner in reversed(self._owners):
            try:
                owner.close()
            except BaseException as error:  # noqa: BLE001 - attempt every remaining resource owner
                self._unsafe(error)
                if failure is None:
                    failure = error
                else:
                    failure.add_note(
                        f"Observation cleanup failed: {type(error).__name__}"
                    )
        self._owners.clear()
        if failure is not None and primary is None:
            raise failure
