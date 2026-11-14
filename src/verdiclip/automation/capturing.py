"""What ``ctl capture`` asks for, checked before the app touches the screen."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from verdiclip.automation.protocol import (
    AutomationError,
    Deferred,
    ErrorCode,
    JsonObject,
    JsonValue,
)
from verdiclip.editor.page import DocumentRecord
from verdiclip.geometry import Point, Rect


class CaptureKind(StrEnum):
    """The capture types ``ctl capture`` offers."""

    REGION = "region"
    WINDOW = "window"
    FULLSCREEN = "fullscreen"
    SCROLLING = "scrolling"


@dataclass(frozen=True, slots=True)
class CaptureSpec:
    """One capture request.

    Coordinates are physical desktop pixels, as ``ctl targets`` reports them.
    """

    kind: CaptureKind
    region: Rect | None = None
    title: str | None = None
    handle: int | None = None
    at: Point | None = None
    monitor: int | None = None
    all_screens: bool = False
    from_top: bool = True
    output: Path | None = None
    copy: bool = False

    @property
    def interactive(self) -> bool:
        """Return whether the user chooses what to capture on screen."""
        if self.kind is CaptureKind.REGION:
            return self.region is None
        if self.kind is CaptureKind.SCROLLING:
            return self.at is None and not self.names_window
        return False

    @property
    def names_window(self) -> bool:
        """Return whether a window is picked by title or handle."""
        return self.title is not None or self.handle is not None

    @classmethod
    def from_params(cls, params: JsonObject) -> CaptureSpec:
        """Read and check the parameters of a ``capture`` request.

        Raises:
            AutomationError: If a parameter is missing, malformed or doesn't fit
                the capture type.
        """
        try:
            kind = CaptureKind(str(params.get("mode")))
        except ValueError:
            kinds = ", ".join(k.value for k in CaptureKind)
            msg = f"mode must be one of {kinds}, got {params.get('mode')!r}"
            raise AutomationError(ErrorCode.BAD_REQUEST, msg) from None
        spec = cls(
            kind=kind,
            region=_rect(params.get("region")),
            title=_text(params.get("title"), "title"),
            handle=_whole(params.get("handle"), "handle", minimum=1),
            at=_point(params.get("at")),
            monitor=_whole(params.get("monitor"), "monitor", minimum=1),
            all_screens=_flag(params.get("all"), "all"),
            from_top=_from_top(params.get("from")),
            output=_output(params.get("output")),
            copy=_flag(params.get("copy"), "copy"),
        )
        spec.check()
        return spec

    def check(self) -> None:
        """Refuse options that don't belong to this capture type.

        Raises:
            AutomationError: If an option doesn't apply.
        """
        allowed = {
            CaptureKind.REGION: {"region"},
            CaptureKind.WINDOW: {"title", "handle", "at"},
            CaptureKind.FULLSCREEN: {"monitor", "all"},
            CaptureKind.SCROLLING: {"title", "handle", "at", "from"},
        }[self.kind]
        given = {
            "region": self.region is not None,
            "title": self.title is not None,
            "handle": self.handle is not None,
            "at": self.at is not None,
            "monitor": self.monitor is not None,
            "all": self.all_screens,
            "from": not self.from_top,
        }
        extra = [name for name, used in given.items() if used and name not in allowed]
        if extra:
            msg = f"{self.kind.value} capture doesn't take {extra[0]}"
            raise AutomationError(ErrorCode.BAD_REQUEST, msg)
        targets = [n for n in ("title", "handle", "at") if given[n]]
        if len(targets) > 1:
            msg = f"Give one of title, handle or at, not {' and '.join(targets)}"
            raise AutomationError(ErrorCode.BAD_REQUEST, msg)
        if not self.from_top and not targets:
            msg = (
                "from here needs at, title or handle; in the on-screen chooser, "
                "Ctrl+click starts from the current position"
            )
            raise AutomationError(ErrorCode.BAD_REQUEST, msg)
        if self.monitor is not None and self.all_screens:
            msg = "Give monitor or all, not both"
            raise AutomationError(ErrorCode.BAD_REQUEST, msg)


@dataclass(frozen=True, slots=True)
class CaptureOutcome:
    """A capture opened in an editor tab, and what else was done with it."""

    record: DocumentRecord
    saved: Path | None = None
    copied: bool = False
    warning: str = ""


class AppActions(Protocol):
    """What the running app does for ``ctl`` beyond reading documents."""

    def capture(self, spec: CaptureSpec) -> Deferred[CaptureOutcome]:
        """Capture as ``spec`` says and open the result in an editor tab."""

    def capture_targets(self) -> JsonObject:
        """Describe the monitors and windows that can be captured."""

    def close_document(self, record: DocumentRecord, *, discard: bool) -> None:
        """Close ``record``'s tab without asking the user."""


def _rect(value: JsonValue) -> Rect | None:
    numbers = _numbers(value, 4, "region", "[x, y, width, height]")
    if numbers is None:
        return None
    x, y, w, h = numbers
    if w <= 0 or h <= 0:
        msg = f"region width and height must be positive, got {value!r}"
        raise AutomationError(ErrorCode.BAD_REQUEST, msg)
    return Rect(x, y, w, h)


def _point(value: JsonValue) -> Point | None:
    numbers = _numbers(value, 2, "at", "[x, y]")
    return None if numbers is None else Point(numbers[0], numbers[1])


def _numbers(value: JsonValue, count: int, name: str, shape: str) -> list[float] | None:
    if value is None:
        return None
    items = value if isinstance(value, list) else []
    if len(items) != count or not all(
        isinstance(n, (int, float)) and not isinstance(n, bool) for n in items
    ):
        msg = f"{name} must be {shape}, got {value!r}"
        raise AutomationError(ErrorCode.BAD_REQUEST, msg)
    return [float(n) for n in items]  # ty: ignore[invalid-argument-type]


def _text(value: JsonValue, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        msg = f"{name} must be non-empty text, got {value!r}"
        raise AutomationError(ErrorCode.BAD_REQUEST, msg)
    return value


def _whole(value: JsonValue, name: str, *, minimum: int) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        msg = f"{name} must be an integer of at least {minimum}, got {value!r}"
        raise AutomationError(ErrorCode.BAD_REQUEST, msg)
    return value


def _flag(value: JsonValue, name: str) -> bool:
    if value is None:
        return False
    if not isinstance(value, bool):
        msg = f"{name} must be true or false, got {value!r}"
        raise AutomationError(ErrorCode.BAD_REQUEST, msg)
    return value


def _from_top(value: JsonValue) -> bool:
    if value is None or value == "top":
        return True
    if value == "here":
        return False
    msg = f"from must be 'top' or 'here', got {value!r}"
    raise AutomationError(ErrorCode.BAD_REQUEST, msg)


def _output(value: JsonValue) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        msg = f"output must be an absolute path, got {value!r}"
        raise AutomationError(ErrorCode.BAD_REQUEST, msg)
    return Path(value)
