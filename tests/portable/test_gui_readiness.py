"""Pure production orchestration/font-resource ports; no Qt imports or widgets."""

from __future__ import annotations

import pytest

from verdiclip.shell.gui_readiness import (
    FontPreparation,
    GuiReadinessError,
    StartupReadiness,
    prepare_and_admit,
    prepare_gui_fonts,
)


class Fonts:
    """Read-only fake resource provider with observable call order."""

    def __init__(
        self, calls: list[str], count: int = 12, advance: float = 63.5
    ) -> None:
        """Retain only primitive metadata, never font names or GUI objects."""
        self.calls = calls
        self.count = count
        self.advance = advance

    def family_count(self) -> int:
        """Model the required catalog operation."""
        self.calls.append("families")
        return self.count

    def digit_advance(self) -> float:
        """Model the required default-font numeric metrics operation."""
        self.calls.append("metrics")
        return self.advance


def test_font_preparation_reports_actual_scoped_cost_and_resources() -> None:
    """Both real operation ports complete before their receipt is returned."""
    calls: list[str] = []
    ticks = iter((1_000_000, 21_000_000))
    receipt = prepare_gui_fonts(Fonts(calls), lambda: next(ticks))
    assert calls == ["families", "metrics"]
    assert receipt == FontPreparation(12, 63.5, 20.0)


def test_orchestration_orders_theme_resources_then_actual_admission() -> None:
    """Readiness is a production sequence, not a test-only warmup."""
    calls: list[str] = []
    ticks = iter((0, 50_000_000))

    def prepare() -> FontPreparation:
        calls.append("prepare")
        return FontPreparation(12, 63.5, 20.0)

    receipt = prepare_and_admit(
        lambda: calls.append("theme"),
        prepare,
        lambda: calls.append("admit"),
        lambda: next(ticks),
    )
    assert calls == ["theme", "prepare", "admit"]
    assert receipt.fonts.elapsed_ms == 20.0
    assert receipt.controller_start_ms == 50.0
    assert receipt.combined_ms(80.0) == 130.0  # Preparation is nested, not added twice.


def test_resource_failure_does_not_admit_tray_or_hotkeys() -> None:
    """The original failure escapes before any startup admission callback."""
    calls: list[str] = []
    original = GuiReadinessError("font backend unavailable")

    def fail() -> FontPreparation:
        raise original

    with pytest.raises(GuiReadinessError, match="backend unavailable") as caught:
        prepare_and_admit(
            lambda: calls.append("theme"), fail, lambda: calls.append("admit")
        )
    assert caught.value is original
    assert calls == ["theme"]


def test_theme_failure_does_not_prepare_or_admit() -> None:
    """The effective theme must be established before preparing its UI font."""
    calls: list[str] = []

    def theme() -> None:
        raise ValueError("theme")

    def prepare() -> FontPreparation:
        calls.append("prepare")
        return FontPreparation(1, 12.0, 0.0)

    with pytest.raises(ValueError, match="theme"):
        prepare_and_admit(theme, prepare, lambda: calls.append("admit"))
    assert not calls


def test_control_exception_is_not_swallowed_or_replaced() -> None:
    """Cancellation during resource preparation cannot publish false readiness."""
    original = KeyboardInterrupt("startup")
    admitted: list[bool] = []

    def prepare() -> FontPreparation:
        raise original

    with pytest.raises(KeyboardInterrupt) as caught:
        prepare_and_admit(lambda: None, prepare, lambda: admitted.append(True))
    assert caught.value is original
    assert not admitted


def test_admission_failure_cannot_return_a_successful_startup_receipt() -> None:
    """Later shell startup failures retain their original behavior/object."""
    original = RuntimeError("admission")

    def admit() -> None:
        raise original

    with pytest.raises(RuntimeError, match="admission") as caught:
        prepare_and_admit(lambda: None, lambda: FontPreparation(1, 12.0, 0.0), admit)
    assert caught.value is original


@pytest.mark.parametrize("count", [0, -1])
def test_empty_catalog_is_not_prepared_readiness(count: int) -> None:
    """No available font catalog cannot be silently declared prepared."""
    with pytest.raises(GuiReadinessError, match="font families"):
        prepare_gui_fonts(Fonts([], count=count))


@pytest.mark.parametrize("advance", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_default_metrics_are_not_prepared_readiness(advance: float) -> None:
    """Unavailable/nonfinite metrics cannot bypass startup admission."""
    with pytest.raises(GuiReadinessError, match="numeric font metrics"):
        prepare_gui_fonts(Fonts([], advance=advance))


def test_preparation_clock_cannot_create_negative_duration() -> None:
    """A broken clock is explicit instead of publishing an invented duration."""
    ticks = iter((20, 10))
    with pytest.raises(GuiReadinessError, match="not monotonic"):
        prepare_gui_fonts(Fonts([]), lambda: next(ticks))


@pytest.mark.parametrize("capture_ms", [-1.0, float("nan"), float("inf")])
def test_combined_accounting_rejects_invalid_external_capture_duration(
    capture_ms: float,
) -> None:
    """A hosted measurement must provide a finite separate capture interval."""
    receipt = StartupReadiness(FontPreparation(1, 12.0, 10.0), 20.0)
    with pytest.raises(ValueError, match="First-capture"):
        receipt.combined_ms(capture_ms)
