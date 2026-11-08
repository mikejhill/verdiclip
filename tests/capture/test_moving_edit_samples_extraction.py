"""Source-qualified sampling and retained ownership, without native execution."""

from unittest.mock import Mock

import pytest

from verdiclip.capture.edit_native import ProbeBudget
from verdiclip.capture.edit_samples import crop, stable_samples


def test_client_crop_keeps_all_edges_and_margins() -> None:
    """Each output pixel is exact source provenance, no semantic trimming."""
    rgb = bytes(range(90))
    expected = rgb[18:27] + rgb[36:45] + rgb[54:63]
    assert crop(rgb, [10, 20, 16, 25], [10, 21, 13, 24]) == expected


@pytest.mark.parametrize(
    "inner", [[9, 20, 13, 23], [10, 19, 13, 23], [10, 20, 17, 23], [10, 20, 13, 26]]
)
def test_crop_rejects_partial_visibility(inner: list[int]) -> None:
    """No intersection crop is permitted."""
    with pytest.raises(ValueError, match="outside"):
        crop(bytes(90), [10, 20, 16, 25], inner)


def test_fixed_three_samples() -> None:
    """Three identical observations span200ms without a restart."""
    now = [0.0]
    read = Mock(return_value=b"whole client")
    assert (
        stable_samples(
            read,
            lambda: None,
            2,
            lambda: now[0],
            lambda d: now.__setitem__(0, now[0] + d),
        )
        == b"whole client"
    )
    assert read.call_count == 3
    assert now[0] == 0.2


def test_changed_sample_does_not_settle_again() -> None:
    """A mismatch is terminal even if a third matching sample is available."""
    now = [0.0]
    read = Mock(side_effect=[b"a", b"b", b"b"])
    with pytest.raises(ValueError, match="no reset"):
        stable_samples(
            read,
            lambda: None,
            2,
            lambda: now[0],
            lambda d: now.__setitem__(0, now[0] + d),
        )
    assert read.call_count == 2


@pytest.mark.parametrize("finish", [2.0, 2.001])
def test_slow_sample_fails_at_readiness_boundary(finish: float) -> None:
    """A returned image does not erase elapsed deadline."""
    now = [0.0]

    def read() -> bytes:
        now[0] = finish
        return b"a"

    with pytest.raises(RuntimeError, match="deadline"):
        stable_samples(read, lambda: None, 2, lambda: now[0], lambda _: None)


def test_probe_clock_latches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Expired probe cannot resume if test clock subsequently rewinds."""
    now = [0.0]
    monkeypatch.setattr("verdiclip.capture.edit_native.time.monotonic", lambda: now[0])
    budget = ProbeBudget(30, lambda _: None)
    now[0] = 30
    with pytest.raises(RuntimeError, match="deadline"):
        budget.check()
    now[0] = 1
    with pytest.raises(RuntimeError, match="deadline"):
        budget.check()


def test_native_return_retained_before_late_log_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Acquisition output survives post-call logging failure until ownership."""
    import threading  # noqa: PLC0415 - keep optional/native-adjacent imports out of entry initialization

    from verdiclip.capture.edit_native import (  # noqa: PLC0415 - defer optional capture imports outside supervisor startup
        EditObservation,
    )

    ticks = iter([0.0] * 20)
    monkeypatch.setattr(
        "verdiclip.capture.edit_native.time.monotonic", lambda: next(ticks)
    )
    budget = ProbeBudget(30, lambda _: None)
    native = EditObservation.__new__(EditObservation)
    native.budget = budget
    native._thread = threading.get_ident()
    native._closed = False
    native._owners = []

    def emit(event: dict[str, object]) -> None:
        if event["event"] == "call_end":
            raise OSError("log flush failed")

    native.edit_emit = emit
    assert native.raw(lambda: 123) == 123
    closed = []
    native.own(lambda: closed.append(123))
    with pytest.raises(OSError, match="log flush"):
        budget.check()
    native.close()
    assert closed == [123]


@pytest.mark.parametrize("slow_part", ["first-read", "last-guard"])
def test_guards_cannot_supply_sample_span(slow_part: str) -> None:
    """Only time between first completed and last begun acquisition counts."""
    now = [0.0]
    reads = [0]
    guards = [0]

    def read() -> bytes:
        reads[0] += 1
        if slow_part == "first-read" and reads[0] == 1:
            now[0] += 0.5
        return b"same"

    def check() -> None:
        guards[0] += 1
        if slow_part == "last-guard" and guards[0] == 6:
            now[0] += 0.5

    with pytest.raises(ValueError, match="Insufficient"):
        stable_samples(read, check, 2, lambda: now[0], lambda _: None)


@pytest.mark.parametrize("edge", ["top", "bottom", "left", "right"])
def test_whole_client_crop_keeps_every_boundary_pixel(edge: str) -> None:
    """Edge paint, margins and partial glyph pixels cannot be silently inset."""
    root = bytes(range(180))  # 10x6 RGB, unique bytes expose all off-by-one changes.
    actual = crop(root, [100, 200, 110, 206], [102, 201, 108, 205])
    expected = b"".join(
        root[(row * 10 + 2) * 3 : (row * 10 + 8) * 3] for row in range(1, 5)
    )
    assert actual == expected
    indices = {
        "top": range(18),
        "bottom": range(54, 72),
        "left": [row * 18 + channel for row in range(4) for channel in range(3)],
        "right": [row * 18 + 15 + channel for row in range(4) for channel in range(3)],
    }[edge]
    for index in indices:
        changed = bytearray(actual)
        changed[index] ^= 1
        assert bytes(changed) != expected
