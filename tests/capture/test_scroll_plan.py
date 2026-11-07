"""Pure scrolling lifecycle invariants; no native-provider qualification implied."""

from dataclasses import FrozenInstanceError, replace
from typing import cast

import pytest

from verdiclip.capture import scroll_plan
from verdiclip.capture.scroll_plan import (
    PhysicalRect,
    QualifiedPanel,
    ScrollGuard,
    ScrollOutcome,
    ScrollPlan,
    ScrollRequest,
    SettledObservation,
)
from verdiclip.capture.scroll_stitch import ScrollFrame

_VIEWPORT = PhysicalRect(-200, 80, 2, 96)
_RANGE = 120.0


def pixels(start: int, height: int = 96) -> ScrollFrame:
    """Encode physical source rows without periodic or blank seams."""
    return ScrollFrame(
        2,
        height,
        b"".join(row.to_bytes(3, "big") * 2 for row in range(start, start + height)),
    )


def observation(percent: float, *, row: int | None = None) -> SettledObservation:
    """Provide an explicitly settled aggregate from a synthetic continuous panel."""
    return SettledObservation(
        ScrollGuard(
            target=(100, 200, 300),
            foreground=(400, 500),
            viewport=_VIEWPORT,
            display_layout=(-1000, 0, 2000, 1000),
            input_epoch=0xFFFFFFFE,
            vertical_percent=percent,
            horizontal_percent=17.5,
            view_percent=96 * 100 / 216,
            visible_unoccluded=True,
        ),
        pixels(round(percent * _RANGE / 100) if row is None else row),
        sample_count=3,
        quiet_seconds=0.2,
    )


def start() -> tuple[ScrollPlan, ScrollRequest]:
    """Start a qualified fixture at a nonzero saved position."""
    plan = ScrollPlan(
        QualifiedPanel(
            _VIEWPORT, _RANGE, metric_basis_height=96, horizontal_scrollable=True
        )
    )
    request = plan.start(observation(40), 10)
    assert isinstance(request, ScrollRequest)
    return plan, request


def forward() -> tuple[ScrollPlan, ScrollRequest]:
    """Acknowledge top and return the first bounded forward request."""
    plan, request = start()
    request = plan.accept(request.request_id, observation(0), 11)
    assert isinstance(request, ScrollRequest)
    assert request.vertical_percent == 40
    return plan, request


def restore_failure(
    plan: ScrollPlan, request: ScrollRequest | ScrollOutcome
) -> ScrollOutcome:
    """A known safe failure restores but never exposes an image."""
    assert isinstance(request, ScrollRequest)
    assert request.vertical_percent == 40
    result = plan.accept(request.request_id, observation(40), 12)
    assert isinstance(result, ScrollOutcome)
    assert result.failure is not None
    assert result.restored
    assert not result.position_uncertain
    assert result.image is None
    return result


def test_complete_top_to_bottom_replay_restore() -> None:
    """Verify bounded steps, repeated bottom, replay and restoration before output."""
    plan, action = start()
    commands: list[float] = []
    ids: list[int] = []
    previous_percent = 40.0
    while isinstance(action, ScrollRequest):
        commands.append(action.vertical_percent)
        ids.append(action.request_id)
        assert action.horizontal_percent == 17.5
        assert action.expires_at == 100
        assert action.precondition.vertical_percent == previous_percent
        assert action.precondition.input_epoch == 0xFFFFFFFE
        previous_percent = action.vertical_percent
        action = plan.accept(
            action.request_id, observation(action.vertical_percent), 11
        )
    assert commands == [0, 40, 80, 100, 100, 0, 40]
    assert ids == list(range(1, 8))
    assert action.failure is None
    assert action.restored
    assert not action.position_uncertain
    assert action.image is not None
    assert action.image.rgb == pixels(0, 216).rgb
    assert action.image.height == 216
    assert action.image.frame_count == 4
    assert plan.cancel() is action
    assert plan.accept(1, observation(0), 1_000) is action
    for value, field in [
        (action, "restored"),
        (action.image, "height"),
        (_VIEWPORT, "x"),
    ]:
        with pytest.raises(FrozenInstanceError):
            setattr(value, field, 1)


@pytest.mark.parametrize("saved_percent", [0, 100])
def test_initial_endpoint_still_runs_complete_protocol(saved_percent: float) -> None:
    """Starting at either endpoint never bypasses the verification sequence."""
    plan = ScrollPlan(
        QualifiedPanel(
            _VIEWPORT, _RANGE, metric_basis_height=96, horizontal_scrollable=True
        )
    )
    result = plan.start(observation(saved_percent), 0)
    commands: list[float] = []
    while isinstance(result, ScrollRequest):
        commands.append(result.vertical_percent)
        result = plan.accept(result.request_id, observation(result.vertical_percent), 1)
    assert commands == [0, 40, 80, 100, 100, 0, saved_percent]
    assert result.image is not None


@pytest.mark.parametrize(
    ("viewport", "distance", "message"),
    [
        ((0, 0, 0, 96), 120, "nonempty"),
        ((0, 0, 2, 0), 120, "nonempty"),
        ((-200, 80, 2, 96), 0, "finite and positive"),
        ((-200, 80, 2, 96), -1, "finite and positive"),
        ((-200, 80, 2, 96), float("nan"), "finite and positive"),
        ((-200, 80, 2, 96), float("inf"), "finite and positive"),
        ((-200, 80, 2, 96), 32_673, "limits"),
        ((0, 0, 1_000_000, 96), 120, "limits"),
    ],
)
def test_qualified_bounds(
    viewport: tuple[int, int, int, int], distance: float, message: str
) -> None:
    """Reject impossible qualified metadata without requesting any write."""
    with pytest.raises(ValueError, match=message):
        QualifiedPanel(
            PhysicalRect(*viewport),
            distance,
            metric_basis_height=96,
            horizontal_scrollable=True,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("target", (9,)),
        ("foreground", (4, 6)),
        ("viewport", PhysicalRect(0, 0, 2, 96)),
        ("display_layout", (0,)),
        ("input_epoch", 0),
        ("input_epoch", -1),
        ("input_epoch", 0x100000000),
        ("horizontal_percent", 0.0),
        ("view_percent", 50.0),
        ("visible_unoccluded", False),
        ("vertical_percent", float("nan")),
        ("vertical_percent", -1.0),
        ("vertical_percent", 101.0),
        ("view_percent", float("nan")),
        ("view_percent", 0.0),
        ("view_percent", 100.0),
    ],
)
def test_guard_failure_latches_no_more_writes(field: str, value: object) -> None:
    """Epoch wrap, focus/geometry changes and corrupt state forbid restoration."""
    plan, request = start()
    sample = observation(0)
    sample = replace(sample, guard=replace(sample.guard, **{field: value}))
    result = plan.accept(request.request_id, sample, 11)
    assert isinstance(result, ScrollOutcome)
    assert result.image is None
    assert not result.restored
    assert result.failure is not None
    assert plan.accept(request.request_id, observation(0), 12) is result
    assert plan.cancel() is result


@pytest.mark.parametrize("percent", [float("nan"), -1, 101])
def test_initial_invalid_horizontal(percent: float) -> None:
    """Horizontal-scrollable targets need an explicit valid preserved percent."""
    plan = ScrollPlan(
        QualifiedPanel(
            _VIEWPORT, _RANGE, metric_basis_height=96, horizontal_scrollable=True
        )
    )
    sample = observation(40)
    sample = replace(sample, guard=replace(sample.guard, horizontal_percent=percent))
    result = plan.start(sample, 10)
    assert isinstance(result, ScrollOutcome)
    assert result.failure == "Invalid horizontal position"


def test_non_scrollable_horizontal_uses_provider_sentinel() -> None:
    """Only genuinely non-scrollable horizontal axes use the -1 sentinel."""
    plan = ScrollPlan(
        QualifiedPanel(
            _VIEWPORT, _RANGE, metric_basis_height=96, horizontal_scrollable=False
        )
    )
    sample = observation(40)
    sample = replace(sample, guard=replace(sample.guard, horizontal_percent=-1))
    request = plan.start(sample, 10)
    assert isinstance(request, ScrollRequest)
    assert request.horizontal_percent == -1
    other = ScrollPlan(
        QualifiedPanel(
            _VIEWPORT, _RANGE, metric_basis_height=96, horizontal_scrollable=False
        )
    )
    assert isinstance(other.start(observation(40), 10), ScrollOutcome)


@pytest.mark.parametrize(
    "sample",
    [
        replace(observation(0), frame=pixels(0, 95)),
        replace(observation(0), sample_count=2),
        replace(observation(0), quiet_seconds=0.199),
        replace(observation(0), quiet_seconds=float("nan")),
    ],
)
def test_unsettled_or_wrong_frame_is_terminal(sample: SettledObservation) -> None:
    """Untrusted frame aggregates cannot be admitted or trigger restoration."""
    plan, request = start()
    result = plan.accept(request.request_id, sample, 11)
    assert isinstance(result, ScrollOutcome)
    assert result.failure is not None
    assert result.image is None


def test_initial_metrics_must_agree_with_qualified_range() -> None:
    """View size can reject a qualification but never choose image dimensions."""
    plan = ScrollPlan(
        QualifiedPanel(
            _VIEWPORT, 125, metric_basis_height=96, horizontal_scrollable=True
        )
    )
    result = plan.start(observation(40), 10)
    assert isinstance(result, ScrollOutcome)
    assert result.failure == "View size disagrees with qualified scroll range"


@pytest.mark.parametrize("now", [100, 101, 9, float("nan"), float("inf")])
def test_deadline_and_invalid_clocks_stop_without_restore(now: float) -> None:
    """The exact 90-second deadline and clock violations latch a terminal result."""
    plan, request = start()
    result = plan.accept(request.request_id, observation(0), now)
    assert isinstance(result, ScrollOutcome)
    assert result.position_uncertain
    assert not result.restored
    assert result.image is None


def test_invalid_initial_clock_and_duplicate_start() -> None:
    """Never begin with an invalid clock or issue a second initial command."""
    plan = ScrollPlan(
        QualifiedPanel(
            _VIEWPORT, _RANGE, metric_basis_height=96, horizontal_scrollable=True
        )
    )
    result = plan.start(observation(40), float("nan"))
    assert isinstance(result, ScrollOutcome)
    assert result.failure == "Invalid clock"
    active, _ = start()
    result = active.start(observation(40), 11)
    assert isinstance(result, ScrollOutcome)
    assert result.position_uncertain


@pytest.mark.parametrize("request_id", [0, 2, 999])
def test_wrong_request_id_is_terminal(request_id: int) -> None:
    """Stale, future and unrelated replies cannot admit images or advance state."""
    plan, _ = start()
    result = plan.accept(request_id, observation(0), 11)
    assert isinstance(result, ScrollOutcome)
    assert result.position_uncertain
    assert result.image is None


def test_cancel_before_and_during_operation() -> None:
    """Cancellation never issues a compensating restoration write."""
    plan = ScrollPlan(
        QualifiedPanel(
            _VIEWPORT, _RANGE, metric_basis_height=96, horizontal_scrollable=True
        )
    )
    result = plan.cancel()
    assert not result.position_uncertain
    assert plan.start(observation(40), 10) is result
    active, request = start()
    result = active.cancel()
    assert result.position_uncertain
    assert active.accept(request.request_id, observation(0), 11) is result


@pytest.mark.parametrize(
    ("percent", "row", "reason"),
    [
        (0, 0, "monotonically"),
        (40, 120, "overlap"),
        (40, 46, "displacement"),
        (50, 60, "bounded step"),
    ],
)
def test_bad_forward_step_restores_without_output(
    percent: float, row: int, reason: str
) -> None:
    """Ordinary observed step failures can restore but never deliver the stitch."""
    plan, request = forward()
    action = plan.accept(request.request_id, observation(percent, row=row), 11)
    result = restore_failure(plan, action)
    assert result.failure is not None
    assert reason in result.failure


def test_missing_top_is_rejected_and_restored() -> None:
    """An acknowledged operation that did not reach top is not a first frame."""
    plan, request = start()
    result = restore_failure(plan, plan.accept(request.request_id, observation(20), 11))
    assert result.failure == "Top was not reached"


@pytest.mark.parametrize("offset", [-1, 1])
def test_one_pixel_displacement_allowance(offset: int) -> None:
    """Exactly one physical pixel of rounding disagreement remains admissible."""
    plan, request = forward()
    action = plan.accept(request.request_id, observation(40, row=48 + offset), 11)
    assert isinstance(action, ScrollRequest)
    assert action.vertical_percent == 80


def test_accumulated_extent_error_rejected() -> None:
    """Locally allowed rounding errors cannot accumulate into a false extent."""
    plan, request = forward()
    for percent, row in [(40, 49), (80, 98), (100, 123)]:
        action = plan.accept(request.request_id, observation(percent, row=row), 11)
        assert isinstance(action, ScrollRequest)
        request = action
    result = restore_failure(plan, request)
    assert result.failure == "Output extent disagrees with qualified range"


def to_endpoint() -> tuple[ScrollPlan, ScrollRequest]:
    """Reach bottom and return its mandatory repeated request."""
    plan, request = forward()
    for percent in [40, 80, 100]:
        action = plan.accept(request.request_id, observation(percent), 11)
        assert isinstance(action, ScrollRequest)
        request = action
    assert request.vertical_percent == 100
    return plan, request


@pytest.mark.parametrize("sample", [observation(99), observation(100, row=121)])
def test_bottom_requires_repeated_identical_endpoint(
    sample: SettledObservation,
) -> None:
    """Position or pixel changes after the final 100 request invalidate capture."""
    plan, request = to_endpoint()
    result = restore_failure(plan, plan.accept(request.request_id, sample, 11))
    assert result.failure == "Endpoint changed after repeated bottom request"


@pytest.mark.parametrize("sample", [observation(1), observation(0, row=1)])
def test_top_replay_must_match(sample: SettledObservation) -> None:
    """Previously offscreen changes visible on replay reject the entire result."""
    plan, request = to_endpoint()
    action = plan.accept(request.request_id, observation(100), 11)
    assert isinstance(action, ScrollRequest)
    result = restore_failure(plan, plan.accept(action.request_id, sample, 11))
    assert result.failure == "Top replay did not match"


@pytest.mark.parametrize("sample", [observation(41), observation(40, row=49)])
def test_restoration_failure_exposes_no_complete_image(
    sample: SettledObservation,
) -> None:
    """Even a verified capture is withheld when original position/pixels differ."""
    plan, request = to_endpoint()
    for percent in [100, 0]:
        action = plan.accept(request.request_id, observation(percent), 11)
        assert isinstance(action, ScrollRequest)
        request = action
    result = plan.accept(request.request_id, sample, 11)
    assert isinstance(result, ScrollOutcome)
    assert result.image is None
    assert not result.restored
    assert result.failure == "Original position or pixels were not restored"


@pytest.mark.parametrize(
    ("name", "limit", "reason"),
    [
        ("MAX_FRAMES", 1, "frame limit"),
        ("MAX_OUTPUT_ROWS", 96, "size limit"),
        ("MAX_OUTPUT_PIXELS", 192, "size limit"),
    ],
)
def test_no_forward_write_after_limits(
    monkeypatch: pytest.MonkeyPatch, name: str, limit: int, reason: str
) -> None:
    """Check ceilings before issuing a forward command, not after another frame."""
    plan, request = start()
    monkeypatch.setattr(scroll_plan, name, limit)
    result = restore_failure(plan, plan.accept(request.request_id, observation(0), 11))
    assert result.failure is not None
    assert reason in result.failure


@pytest.mark.parametrize("uncertain", [True, False])
def test_native_failure_without_known_readback_forbids_restore(
    *, uncertain: bool
) -> None:
    """Missing readback is unsafe even when a caller labels the call known."""
    plan, request = start()
    result = plan.fail(request.request_id, "Provider failure", 11, uncertain=uncertain)
    assert isinstance(result, ScrollOutcome)
    assert result.image is None
    assert result.position_uncertain
    assert not result.restored


def test_known_native_failure_requires_unchanged_fresh_state() -> None:
    """A known failed operation can restore only from the last acknowledged state."""
    plan, request = start()
    action = plan.fail(
        request.request_id,
        "Provider failure",
        11,
        uncertain=False,
        current=observation(40),
    )
    result = restore_failure(plan, action)
    assert result.failure == "Provider failure"
    for current in [observation(41), replace(observation(40), sample_count=1)]:
        plan, request = start()
        result = plan.fail(
            request.request_id, "Provider failure", 11, uncertain=False, current=current
        )
        assert isinstance(result, ScrollOutcome)
        assert not result.restored
        assert result.image is None


def test_failed_restoration_is_not_retried() -> None:
    """A restoration failure is terminal even with known unchanged readback."""
    plan, request = start()
    action = plan.accept(request.request_id, observation(20), 11)
    assert isinstance(action, ScrollRequest)
    result = plan.fail(
        action.request_id,
        "Restore failed",
        12,
        uncertain=False,
        current=observation(20),
    )
    assert isinstance(result, ScrollOutcome)
    assert result.failure == "Restore failed"
    assert result.image is None
    assert (
        plan.fail(
            action.request_id, "Again", 13, uncertain=False, current=observation(20)
        )
        is result
    )


@pytest.mark.parametrize("phase_index", range(7))
def test_cancellation_at_every_write_phase(phase_index: int) -> None:
    """No phase, including replay or restoration, can override cancellation."""
    plan, request = start()
    for _ in range(phase_index):
        action = plan.accept(
            request.request_id, observation(request.vertical_percent), 11
        )
        assert isinstance(action, ScrollRequest)
        request = action
    result = plan.cancel()
    assert result.image is None
    assert result.position_uncertain
    assert not result.restored
    assert (
        plan.accept(request.request_id, observation(request.vertical_percent), 12)
        is result
    )


@pytest.mark.parametrize("phase_index", range(7))
def test_input_epoch_change_at_every_write_phase(phase_index: int) -> None:
    """An observed new input epoch is never adopted for another operation."""
    plan, request = start()
    for _ in range(phase_index):
        action = plan.accept(
            request.request_id, observation(request.vertical_percent), 11
        )
        assert isinstance(action, ScrollRequest)
        request = action
    sample = observation(request.vertical_percent)
    sample = replace(sample, guard=replace(sample.guard, input_epoch=0))
    result = plan.accept(request.request_id, sample, 12)
    assert isinstance(result, ScrollOutcome)
    assert result.image is None
    assert not result.restored
    assert (
        plan.accept(request.request_id, observation(request.vertical_percent), 13)
        is result
    )


def test_ambiguous_pixels_are_never_disambiguated_using_percent() -> None:
    """Plausible provider displacement cannot rescue a repetitive pixel seam."""
    plan, request = start()
    top = replace(observation(0), frame=ScrollFrame(2, 96, b"abcdef" * 96))
    action = plan.accept(request.request_id, top, 11)
    assert isinstance(action, ScrollRequest)
    moved = replace(
        observation(40), frame=ScrollFrame(2, 96, b"abcdef" * 95 + b"ghijkl")
    )
    result = restore_failure(plan, plan.accept(action.request_id, moved, 11))
    assert result.failure is not None
    assert "Unchanged interior" in result.failure


@pytest.mark.parametrize(
    ("name", "limit"),
    [("MAX_FRAMES", 4), ("MAX_OUTPUT_ROWS", 216), ("MAX_OUTPUT_PIXELS", 432)],
)
def test_exact_completion_ceiling_is_allowed(
    monkeypatch: pytest.MonkeyPatch, name: str, limit: int
) -> None:
    """Reaching the bound on the final accepted frame still permits verification."""
    plan, action = start()
    monkeypatch.setattr(scroll_plan, name, limit)
    while isinstance(action, ScrollRequest):
        action = plan.accept(
            action.request_id, observation(action.vertical_percent), 11
        )
    assert action.image is not None
    assert action.image.height == 216


def test_one_pixel_final_extent_allowance() -> None:
    """Final extent may differ by one row, without cropping or filling to fit."""
    plan, request = forward()
    for percent, row in [(40, 49), (80, 97), (100, 121), (100, 121), (0, 0)]:
        action = plan.accept(request.request_id, observation(percent, row=row), 11)
        assert isinstance(action, ScrollRequest)
        request = action
    result = plan.accept(request.request_id, observation(40), 11)
    assert isinstance(result, ScrollOutcome)
    assert result.image is not None
    assert result.image.height == 217
    assert result.image.rgb == pixels(0, 217).rgb


def test_small_range_clips_request_to_bottom() -> None:
    """A range shorter than half a viewport requests 100, never an overshoot."""
    plan = ScrollPlan(
        QualifiedPanel(_VIEWPORT, 2, metric_basis_height=96, horizontal_scrollable=True)
    )

    def small_sample(percent: float) -> SettledObservation:
        sample = observation(percent, row=round(percent * 2 / 100))
        return replace(sample, guard=replace(sample.guard, view_percent=96 * 100 / 98))

    action = plan.start(small_sample(50), 0)
    commands: list[float] = []
    while isinstance(action, ScrollRequest):
        commands.append(action.vertical_percent)
        action = plan.accept(
            action.request_id, small_sample(action.vertical_percent), 1
        )
    assert commands == [0, 100, 100, 0, 50]
    assert action.image is not None
    assert action.image.rgb == pixels(0, 98).rgb


@pytest.mark.parametrize("field", ["x", "y", "width", "height"])
@pytest.mark.parametrize("value", [True, 1.5, float("nan"), float("inf"), "10", None])
def test_physical_rectangle_requires_integer_coordinates(
    field: str, value: object
) -> None:
    """Geometry never coerces booleans, fractional numbers or malformed values."""
    with pytest.raises(ValueError, match="must be integers"):
        replace(_VIEWPORT, **{field: value})


def test_native_rectangle_conversion_preserves_negative_origin() -> None:
    """Native right/bottom edges become sizes exactly once and remain immutable."""
    rect = PhysicalRect.from_ltrb(-500, -300, -185, -55)
    assert rect == PhysicalRect(x=-500, y=-300, width=315, height=245)
    with pytest.raises(ValueError, match="nonempty"):
        PhysicalRect.from_ltrb(-500, -300, -500, -55)


@pytest.mark.parametrize("field", ["left", "top", "right", "bottom"])
@pytest.mark.parametrize("value", [True, 1.5, float("nan"), "10", None])
def test_native_rectangle_conversion_rejects_nonintegers(
    field: str, value: object
) -> None:
    """Edge subtraction cannot hide malformed native coordinate types."""
    edges = {"left": -500, "top": -300, "right": -185, "bottom": -55}
    edges[field] = cast("int", value)
    with pytest.raises(ValueError, match="edges must be integers"):
        PhysicalRect.from_ltrb(**edges)


def test_qualified_panel_rejects_ambiguous_geometry_tuple() -> None:
    """A native LTRB tuple cannot silently enter the XYWH content contract."""
    with pytest.raises(ValueError, match="explicit PhysicalRect"):
        QualifiedPanel(
            cast("PhysicalRect", (-500, -300, -185, -55)),
            1212,
            metric_basis_height=264,
            horizontal_scrollable=True,
        )


@pytest.mark.parametrize(
    "basis", [True, 0, -1, 264.0, 264.5, float("nan"), None, 32_769, 10**400]
)
def test_metric_basis_must_be_explicit_positive_integer(basis: object) -> None:
    """Raw UIA view metrics need a separately validated physical basis."""
    with pytest.raises(ValueError, match="basis height"):
        QualifiedPanel(
            _VIEWPORT,
            _RANGE,
            metric_basis_height=cast("int", basis),
            horizontal_scrollable=True,
        )


@pytest.mark.parametrize(
    "count", [True, 3.0, 3.5, float("nan"), float("inf"), "3", None, -1]
)
def test_invalid_sample_count_never_issues_initial_request(count: object) -> None:
    """A noninteger sample count cannot claim the three-sample stability gate."""
    plan = ScrollPlan(
        QualifiedPanel(
            _VIEWPORT, _RANGE, metric_basis_height=96, horizontal_scrollable=True
        )
    )
    sample = replace(observation(40), sample_count=cast("int", count))
    result = plan.start(sample, 10)
    assert isinstance(result, ScrollOutcome)
    assert result.failure == "Frame did not settle"
    assert result.image is None


@pytest.mark.parametrize(
    "epoch", [True, 1.0, 0.5, float("nan"), float("inf"), "7", None, -1, 0x100000000]
)
def test_invalid_initial_epoch_never_issues_request(epoch: object) -> None:
    """Initial ownership uses a strict uint32 tick, never fractional or NaN data."""
    plan = ScrollPlan(
        QualifiedPanel(
            _VIEWPORT, _RANGE, metric_basis_height=96, horizontal_scrollable=True
        )
    )
    sample = observation(40)
    sample = replace(
        sample, guard=replace(sample.guard, input_epoch=cast("int", epoch))
    )
    result = plan.start(sample, 10)
    assert isinstance(result, ScrollOutcome)
    assert result.image is None
    assert not result.restored


@pytest.mark.parametrize("epoch", [0, 0xFFFFFFFF])
def test_uint32_epoch_endpoints_are_valid(epoch: int) -> None:
    """Both uint32 endpoints are valid immutable epochs, without ordering tests."""
    plan = ScrollPlan(
        QualifiedPanel(
            _VIEWPORT, _RANGE, metric_basis_height=96, horizontal_scrollable=True
        )
    )
    sample = observation(40)
    sample = replace(sample, guard=replace(sample.guard, input_epoch=epoch))
    result = plan.start(sample, 10)
    assert isinstance(result, ScrollRequest)
    assert result.precondition.input_epoch == epoch


def test_raw_view_metric_basis_is_separate_from_edge_content_height() -> None:
    """Reproduce Edge's 245-row content, 264-row metric basis and 1212-row range."""
    rect = PhysicalRect.from_ltrb(-500, -300, -185, -55)
    raw_view_percent = 17.88617886178862
    plan = ScrollPlan(
        QualifiedPanel(rect, 1212, metric_basis_height=264, horizontal_scrollable=True)
    )

    def edge_sample(percent: float) -> SettledObservation:
        original = observation(percent)
        row = round(percent * 1212 / 100)
        frame = ScrollFrame(
            rect.width,
            rect.height,
            b"".join(
                i.to_bytes(3, "big") * rect.width for i in range(row, row + rect.height)
            ),
        )
        return replace(
            original,
            guard=replace(original.guard, viewport=rect, view_percent=raw_view_percent),
            frame=frame,
        )

    action = plan.start(edge_sample(40), 0)
    while isinstance(action, ScrollRequest):
        assert action.precondition.view_percent == raw_view_percent
        action = plan.accept(action.request_id, edge_sample(action.vertical_percent), 1)
    assert action.failure is None
    assert action.image is not None
    assert (action.image.width, action.image.height) == (315, 1457)
    assert action.image.rgb == b"".join(i.to_bytes(3, "big") * 315 for i in range(1457))
    incorrect = ScrollPlan(
        QualifiedPanel(rect, 1212, metric_basis_height=245, horizontal_scrollable=True)
    )
    assert isinstance(incorrect.start(edge_sample(40), 0), ScrollOutcome)


def test_odd_height_uses_integral_half_viewport_guide() -> None:
    """A 413-row viewport requests 206 physical rows, never 206.5."""
    rect = PhysicalRect(-100, -50, 2, 413)
    plan = ScrollPlan(
        QualifiedPanel(rect, 800, metric_basis_height=413, horizontal_scrollable=True)
    )

    def tall_sample(percent: float) -> SettledObservation:
        sample = observation(percent)
        return replace(
            sample,
            guard=replace(sample.guard, viewport=rect, view_percent=413 * 100 / 1213),
            frame=pixels(round(percent * 800 / 100), height=413),
        )

    top_request = plan.start(tall_sample(40), 0)
    assert isinstance(top_request, ScrollRequest)
    forward_request = plan.accept(top_request.request_id, tall_sample(0), 1)
    assert isinstance(forward_request, ScrollRequest)
    assert forward_request.vertical_percent == 25.75
    assert forward_request.vertical_percent * 800 / 100 == 206
    action: ScrollRequest | ScrollOutcome = forward_request
    while isinstance(action, ScrollRequest):
        action = plan.accept(action.request_id, tall_sample(action.vertical_percent), 1)
    assert action.image is not None
    assert action.image.rgb == pixels(0, 1213).rgb


def test_one_row_viewport_never_requests_zero_step() -> None:
    """An integral half-step of zero is unsupported rather than a progress loop."""
    rect = PhysicalRect(0, 0, 2, 1)
    plan = ScrollPlan(
        QualifiedPanel(rect, 2, metric_basis_height=1, horizontal_scrollable=True)
    )
    sample = observation(0)
    sample = replace(
        sample,
        guard=replace(sample.guard, viewport=rect, view_percent=100 / 3),
        frame=pixels(0, 1),
    )
    initial = plan.start(sample, 0)
    assert isinstance(initial, ScrollRequest)
    restoration = plan.accept(initial.request_id, sample, 1)
    assert isinstance(restoration, ScrollRequest)
    outcome = plan.accept(restoration.request_id, sample, 1)
    assert isinstance(outcome, ScrollOutcome)
    assert outcome.failure == "Viewport is too short for a forward step"
    assert outcome.image is None
