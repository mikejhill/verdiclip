"""Deterministic owned browser pages; never inject into another application."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

SCENARIOS = frozenset({"conversation", "table"})
THEMES = frozenset({"light", "dark"})
VARIANTS = frozenset({"static", "sticky", "streaming"})
MAX_REFERENCE_OFFSET = 8_000
MIN_WIDTH = 240
MAX_WIDTH = 900


@dataclass(frozen=True)
class MatrixCase:
    """Authored page configuration, separate from matcher/capture inputs."""

    scenario: str
    theme: str = "light"
    variant: str = "static"
    width: int = 320
    reference_offset: float | None = None
    reference_mode: str = "scroll"
    reference_endpoint: bool = False

    def validate(self) -> None:
        """Bound all authored controls before interpolating the local document."""
        if self.scenario not in SCENARIOS or self.theme not in THEMES:
            raise ValueError("Unknown owned matrix scenario or theme")
        if self.variant not in VARIANTS or not MIN_WIDTH <= self.width <= MAX_WIDTH:
            raise ValueError("Invalid owned matrix variant or width")
        if self.reference_mode not in {"scroll", "translate"}:
            raise ValueError("Invalid reference mode")
        if self.reference_endpoint and (
            self.reference_offset is not None
            or self.reference_mode != "scroll"
            or self.variant != "static"
        ):
            raise ValueError(
                "Endpoint reference requires static self-scroll without offset"
            )
        if self.reference_offset is not None and (
            not math.isfinite(self.reference_offset)
            or not 0 <= self.reference_offset <= MAX_REFERENCE_OFFSET
            or self.variant != "static"
        ):
            raise ValueError("Reference mode requires a bounded static offset")


def render(case: MatrixCase) -> str:
    """Render a complete local fixture with inert JSON and fixed authored code."""
    case.validate()
    source = Path(__file__).with_name("panel_fixture") / "matrix.html"
    configuration = json.dumps(asdict(case), allow_nan=False).replace("<", "\\u003c")
    return source.read_text(encoding="utf-8").replace(
        "MATRIX_CONFIGURATION", configuration
    )


def initial_cases() -> tuple[MatrixCase, ...]:
    """First representative pages; native scale is a separate runner concern."""
    return tuple(
        MatrixCase(scenario, theme, width=320 if scenario == "conversation" else 520)
        for scenario in sorted(SCENARIOS)
        for theme in sorted(THEMES)
    )
