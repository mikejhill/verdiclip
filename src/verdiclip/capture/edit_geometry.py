"""Pure admission for the owned standard EDIT research experiment, not product code."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Never

BASE = "4c73283c05347a16e20181c0af74535cad40ee19"
WORKLOAD_SECONDS = 44
TOTAL_SECONDS = 49
PHASE_SECONDS = {"prepare": 10, "entry": 2, "probe": 30, "exit": 2}
RECT_FIELDS = 4
MAX_COORDINATE = 1_000_000
DPI = 96
REQUIRED_STYLE = 0x503008C4
FORBIDDEN_ALIGNMENT = 3
FORBIDDEN_EX_STYLE = 0x00400000 | 0x00002000 | 0x00001000
Rect = tuple[int, int, int, int]


def rectangle(value: Rect) -> Rect:
    """Accept physical integral rectangles without rounding or coercion."""
    if (
        type(value) is not tuple
        or len(value) != RECT_FIELDS
        or any(type(x) is not int or abs(x) > MAX_COORDINATE for x in value)
        or value[0] >= value[2]
        or value[1] >= value[3]
    ):
        raise ValueError("Invalid physical rectangle")
    return value


def contains(outer: Rect, inner: Rect) -> bool:
    """Check complete containment, not an intersection crop."""
    return (
        outer[0] <= inner[0] < inner[2] <= outer[2]
        and outer[1] <= inner[1] < inner[3] <= outer[3]
    )


@dataclass(frozen=True)
class ClientEvidence:
    """Native evidence only; expected authored content geometry is absent."""

    hwnd: int
    root: int
    pid: int
    creation: int
    uia_hwnd: int
    uia_pid: int
    same_element: bool
    parent: int
    class_name: str
    style: int
    ex_style: int
    dpi: int
    client_local: Rect
    client_screen: Rect
    window: Rect
    root_client: Rect
    horizontal_bar: Rect
    vertical_bar: Rect

    def reject(self, message: str, reason: str) -> Never:
        """Carry the immutable observed descriptor through constructor cleanup."""
        raise ClientAdmissionError(message, reason, self)

    def admit(self) -> Rect:  # noqa: C901, PLR0912 - preserve each original admission predicate with bounded reason codes
        """Admit only exact own-HWND geometry; fixture provenance is separate."""
        for name in (
            "hwnd",
            "root",
            "pid",
            "creation",
            "uia_hwnd",
            "uia_pid",
            "parent",
        ):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                self.reject("Invalid identity", f"identity.{name}")
        for rejected, reason in (
            (self.hwnd == self.root, "association.root_substitution"),
            (self.uia_hwnd != self.hwnd, "association.uia_hwnd"),
            (self.uia_pid != self.pid, "association.uia_pid"),
            (self.same_element is not True, "association.compare_elements"),
            (self.parent != self.root, "association.parent"),
            (self.class_name != "Edit", "class_name"),
            (self.dpi != DPI or type(self.dpi) is not int, "dpi"),
        ):
            if rejected:
                self.reject("Not the selected owned standard EDIT HWND", reason)
        if type(self.style) is not int:
            self.reject("Unsupported EDIT styles", "style.type")
        if self.style & REQUIRED_STYLE != REQUIRED_STYLE:
            self.reject("Unsupported EDIT styles", "style.required_bits")
        if self.style & FORBIDDEN_ALIGNMENT:
            self.reject("Unsupported EDIT styles", "style.alignment")
        if self.style != REQUIRED_STYLE:
            self.reject("Unsupported EDIT styles", "style.unexpected_bits")
        if type(self.ex_style) is not int:
            self.reject("Unsupported EDIT styles", "ex_style.type")
        if self.ex_style & FORBIDDEN_EX_STYLE:
            self.reject("Unsupported EDIT styles", "ex_style.forbidden_bits")
        if self.ex_style != 0:
            self.reject("Unsupported EDIT styles", "ex_style.unexpected_bits")
        rectangles = []
        for name in (
            "client_local",
            "client_screen",
            "window",
            "root_client",
            "horizontal_bar",
            "vertical_bar",
        ):
            try:
                rectangles.append(rectangle(getattr(self, name)))
            except ValueError:
                self.reject("Invalid physical rectangle", f"rectangle.{name}")
        local, client, window, root, horizontal, vertical = rectangles
        for rejected, reason in (
            (local[:2] != (0, 0), "mapping.local_origin"),
            (
                local[2:] != (client[2] - client[0], client[3] - client[1]),
                "mapping.size",
            ),
            (not contains(window, client), "mapping.client_outside_window"),
            (not contains(root, window), "mapping.window_outside_root"),
            (not contains(window, horizontal), "bar.horizontal_outside_window"),
            (not contains(window, vertical), "bar.vertical_outside_window"),
            (horizontal[1] < client[3], "bar.horizontal_overlaps_client"),
            (vertical[0] < client[2], "bar.vertical_overlaps_client"),
        ):
            if rejected:
                self.reject(
                    "Client mapping/nonclient scrollbar evidence disagrees", reason
                )
        return client


class ClientAdmissionError(ValueError):
    """Original rejection plus immutable already-read native evidence, no queries."""

    def __init__(self, message: str, reason: str, evidence: ClientEvidence) -> None:
        super().__init__(message)
        self.reason, self.evidence = reason, evidence

    def diagnostic(self) -> dict[str, object]:
        """Bounded native-width style values; do not normalize into acceptance."""
        observed = asdict(self.evidence)
        styles = {}
        for name in ("style", "ex_style"):
            value = getattr(self.evidence, name)
            bounded = type(value) is int and -(1 << 63) <= value < (1 << 64)
            styles[name] = {
                "value": value if bounded else None,
                "hex": hex(value) if bounded else None,
                "valid_native_integer": bounded,
            }
        return {
            "reason": self.reason,
            "observed": observed,
            "styles": styles,
            "required_style": REQUIRED_STYLE,
            "forbidden_alignment": FORBIDDEN_ALIGNMENT,
            "forbidden_ex_style": FORBIDDEN_EX_STYLE,
        }


class Budget:
    """One monotonic experiment clock with failure latching and no phase reset."""

    def __init__(self, clock: Callable[[], float]) -> None:
        self.clock = clock
        self.failure: str | None = None
        self.last = self.now()
        self.started = self.last
        self.deadline = self.started + WORKLOAD_SECONDS
        self.phases: list[str] = []
        self.cleaning = False

    def now(self) -> float:
        """Callback errors and regressions permanently invalidate admission."""
        if self.failure is not None:
            raise RuntimeError(self.failure)
        try:
            value = self.clock()
        except Exception as error:
            self.failure = str(error) or type(error).__name__
            raise RuntimeError(self.failure) from error
        if (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or value < getattr(self, "last", value)
        ):
            self.failure = "Invalid/regressed clock"
            raise RuntimeError(self.failure)
        self.last = value
        return value

    def check(self) -> float:
        """Admit strictly before the current absolute deadline."""
        value = self.now()
        if value >= self.deadline:
            self.failure = "Experiment phase deadline expired"
            raise RuntimeError(self.failure)
        return value

    def enter(self, phase: str) -> None:
        """Only the declared phase order; consumed time never extends outer cap."""
        now = self.check()
        order = tuple(PHASE_SECONDS)
        if len(self.phases) >= len(order) or phase != order[len(self.phases)]:
            self.failure = "Invalid/repeated phase"
            raise RuntimeError(self.failure)
        self.phases.append(phase)
        self.deadline = min(self.started + WORKLOAD_SECONDS, now + PHASE_SECONDS[phase])
