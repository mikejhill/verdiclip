"""Validate bounded UIA observations before they can propose or move a target."""

from __future__ import annotations

import math
from dataclasses import dataclass

from verdiclip.capture.scroll.targets import NodeKind, SemanticNode, SemanticTargets
from verdiclip.geometry import Rect

PERCENT_MAX = 100


class Fields:
    """Treat helper/provider data as untrusted values, including JSON booleans."""

    MAX_ID_LENGTH = 1024
    RECT_FIELDS = 4

    @staticmethod
    def get(value: object, name: str) -> object:
        """Read a named field only from a JSON object."""
        if not isinstance(value, dict) or name not in value:
            raise ValueError(f"UI Automation response is missing {name}")
        return value[name]

    @staticmethod
    def text(value: object) -> str:
        """Bound opaque identity strings; never read document text."""
        if not isinstance(value, str) or not value or len(value) > Fields.MAX_ID_LENGTH:
            raise ValueError("UI Automation returned an invalid identity")
        return value

    @staticmethod
    def number(value: object) -> float:
        """Reject booleans, nonnumbers and nonfinite provider metrics."""
        if isinstance(value, bool) or not isinstance(value, (float, int)):
            raise TypeError("UI Automation returned a nonnumeric metric")
        try:
            number = float(value)
        except OverflowError as error:
            raise ValueError("UI Automation returned an oversized metric") from error
        if not math.isfinite(number):
            raise ValueError("UI Automation returned a nonfinite metric")
        return number

    @staticmethod
    def integer(value: object) -> int:
        """Preserve integer process timestamps without a float precision roundtrip."""
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("UI Automation returned a noninteger identity")
        return value

    @staticmethod
    def boolean(value: object) -> bool:
        """Only actual booleans can authorize semantic state."""
        if not isinstance(value, bool):
            raise TypeError("UI Automation returned an invalid state flag")
        return value

    @staticmethod
    def bounds(value: object) -> Rect:
        """Accept only finite integral physical rectangles with positive area."""
        if not isinstance(value, list) or len(value) != Fields.RECT_FIELDS:
            raise ValueError("UI Automation returned invalid geometry")
        numbers = tuple(Fields.number(item) for item in value)
        if any(int(number) != number for number in numbers):
            raise ValueError("UI Automation geometry must use physical pixels")
        area = Rect(*numbers)
        if area.is_empty:
            raise ValueError("UI Automation returned an empty target")
        return area


@dataclass(frozen=True, slots=True)
class UiaReference:
    """Process-generation and runtime identity; a handle alone is insufficient."""

    window_handle: int
    process_id: int
    started_ticks: int
    runtime_id: str
    bounds: Rect
    vertical_percent: float
    view_size: float

    @property
    def window_identity(self) -> str:
        """Scope every runtime identity to the original native process generation."""
        return f"{self.window_handle}:{self.process_id}:{self.started_ticks}"

    @property
    def identity(self) -> str:
        """Match the semantic node identity produced by the helper."""
        return f"{self.window_identity}/{self.runtime_id}"

    @classmethod
    def decode(cls, value: object) -> UiaReference:
        """Validate provider data before storing it or requesting movement."""
        handle, process, started = (
            Fields.integer(Fields.get(value, key))
            for key in ("window_handle", "process_id", "started_ticks")
        )
        percent = Fields.number(Fields.get(value, "vertical_percent"))
        view = Fields.number(Fields.get(value, "view_size"))
        if (
            min(handle, process, started) < 1
            or not 0 <= percent <= PERCENT_MAX
            or not 0 < view < PERCENT_MAX
        ):
            raise ValueError("UI Automation returned unsupported scroll metrics")
        return cls(
            handle,
            process,
            started,
            Fields.text(Fields.get(value, "runtime_id")),
            Fields.bounds(Fields.get(value, "bounds")),
            percent,
            view,
        )

    def encode(self) -> dict[str, object]:
        """Send data only; nothing here becomes a shell expression."""
        return {
            "window_handle": self.window_handle,
            "process_id": self.process_id,
            "started_ticks": self.started_ticks,
            "runtime_id": self.runtime_id,
            "bounds": [
                self.bounds.x,
                self.bounds.y,
                self.bounds.width,
                self.bounds.height,
            ],
            "vertical_percent": self.vertical_percent,
            "view_size": self.view_size,
        }


@dataclass(frozen=True, slots=True)
class UiaDiscovery:
    """Bounded nearest-first ancestry and references, without live COM objects."""

    window_identity: str
    hit_identity: str
    nodes: tuple[SemanticNode, ...]
    references: tuple[UiaReference, ...]

    @classmethod
    def decode(cls, reply: object) -> UiaDiscovery:
        """Reject missing, oversized, cross-window and inconsistent target metadata."""
        result = Fields.get(reply, "result")
        window = Fields.text(Fields.get(result, "window_identity"))
        hit = Fields.text(Fields.get(result, "hit_identity"))
        raw = Fields.get(result, "nodes")
        if (
            not isinstance(raw, list)
            or not 1 <= len(raw) <= SemanticTargets.MAX_ANCESTORS
        ):
            raise ValueError("UI Automation ancestry exceeds its limit")
        nodes: list[SemanticNode] = []
        references: list[UiaReference] = []
        for item in raw:
            node, reference = cls._node(item, window)
            nodes.append(node)
            if reference is not None:
                references.append(reference)
        identities = {node.identity for node in nodes}
        if len(identities) != len(nodes) or hit != nodes[0].identity:
            raise ValueError("UI Automation returned duplicate or unrelated hit nodes")
        for index, node in enumerate(nodes):
            expected = nodes[index + 1].identity if index + 1 < len(nodes) else None
            if node.parent_identity != expected:
                raise ValueError(
                    "UI Automation ancestry is not a complete ordered chain"
                )
        if nodes[-1].kind is not NodeKind.WINDOW:
            raise ValueError("UI Automation ancestry has no verified window root")
        return cls(window, hit, tuple(nodes), tuple(references))

    @staticmethod
    def _node(item: object, window: str) -> tuple[SemanticNode, UiaReference | None]:
        """Verify one node and its optional scroll capability in the same scope."""
        identity = Fields.text(Fields.get(item, "identity"))
        if Fields.text(Fields.get(item, "window_identity")) != window:
            raise ValueError("UI Automation ancestry changed windows")
        parent = Fields.get(item, "parent_identity")
        parent = None if parent is None else Fields.text(parent)
        scrollable = Fields.boolean(Fields.get(item, "vertically_scrollable"))
        bounds = Fields.bounds(Fields.get(item, "bounds"))
        node = SemanticNode(
            identity,
            window,
            NodeKind(Fields.text(Fields.get(item, "kind"))),
            bounds,
            parent,
            scrollable,
        )
        reference = Fields.get(item, "reference")
        if not scrollable:
            if reference is not None:
                raise ValueError(
                    "UI Automation supplied an unverified scroll reference"
                )
            return node, None
        decoded = UiaReference.decode(reference)
        if (
            decoded.identity != identity
            or decoded.window_identity != window
            or decoded.bounds != bounds
        ):
            raise ValueError("UI Automation reference does not match its node")
        return node, decoded


@dataclass(frozen=True, slots=True)
class UiaObservation:
    """Fresh movement metrics; document end still needs independent pixel evidence."""

    reference: UiaReference
    at_end: bool

    @classmethod
    def decode(cls, reply: object, expected: UiaReference) -> UiaObservation:
        """Native handle reuse, provider replacement and resize stop acquisition."""
        result = Fields.get(reply, "result")
        reference = UiaReference.decode(Fields.get(result, "reference"))
        end = Fields.boolean(Fields.get(result, "at_end"))
        if (
            reference.identity != expected.identity
            or reference.bounds != expected.bounds
        ):
            raise ValueError("UI Automation target identity or geometry changed")
        if end != (reference.vertical_percent >= PERCENT_MAX):
            raise ValueError("UI Automation end evidence is inconsistent")
        return cls(reference, end)


@dataclass(frozen=True, slots=True)
class UiaSample:
    """A validated file receipt; callers open only their own expected path."""

    observation: UiaObservation
    viewport: Rect
    image_path: str

    @classmethod
    def decode(
        cls, reply: object, expected: UiaReference, *, viewport: Rect, image_path: str
    ) -> UiaSample:
        """Match actual target, physical ROI and exact private-file destination."""
        observation = UiaObservation.decode(reply, expected)
        result = Fields.get(reply, "result")
        actual_area = Fields.bounds(Fields.get(result, "viewport"))
        actual_path = Fields.text(Fields.get(result, "image_path"))
        if actual_area != viewport or actual_path != image_path:
            raise ValueError("Capture helper returned an unrelated viewport or file")
        return cls(observation, actual_area, actual_path)
