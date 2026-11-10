"""Independent link observations retain conflicts and never retry uncertain writes."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest
from PySide6.QtGui import QImage
from scripts import scrolling_link_probe as probe

from verdiclip.capture.scrolling import ScrollPosition
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Rect


def test_pair_comes_from_final_retained_native_states() -> None:
    """Earlier attractive pairs cannot replace the final anchor/trial."""
    states = [ScrollPosition(-1, p, 25) for p in (0, 20, 40, 20, 21)]
    assert probe.failed_pair(states, 600) == (20, 21)


@pytest.mark.parametrize(
    "states",
    [
        [],
        [ScrollPosition(-1, 0, 25)],
        [ScrollPosition(-1, 20, 25), ScrollPosition(-1, 10, 25)],
        [ScrollPosition(-1, 20, 25), ScrollPosition(-1, 21, 26)],
        [ScrollPosition(-1, 20, 25), ScrollPosition(-1, 20.01, 25)],
    ],
)
def test_pair_refuses_unproven_extent_or_nonintegral_motion(
    states: list[ScrollPosition],
) -> None:
    """Missing and inconsistent retained samples never authorize probe movement."""
    with pytest.raises(CaptureError):
        probe.failed_pair(states, 600)


def _record(rect: Rect) -> dict[str, object]:
    return {"eligible": True, "bounds": asdict(rect)}


def test_all_eligible_links_including_stationary_are_evaluated() -> None:
    """Two favorable moving links cannot hide a stationary third control."""
    records = [
        (_record(Rect(x, 300, 50, 20)), _record(Rect(x, 280, 50, 20)))
        for x in (10, 100)
    ]
    assert probe.motions(records)["motion_witness_observed"] is True
    records.append((_record(Rect(200, 20, 50, 20)), _record(Rect(200, 20, 50, 20))))
    result = probe.motions(records)
    assert result["motion_witness_observed"] is False
    assert result["stationary_count"] == 1
    assert result["conflicting_motion"] is True


def test_overlap_does_not_count_as_distinct_disjoint_witness() -> None:
    """Two overlapping controls cannot supply the nonoverlapping pair."""
    records = [
        (_record(Rect(x, 300, 50, 20)), _record(Rect(x, 280, 50, 20))) for x in (10, 20)
    ]
    assert probe.motions(records)["motion_witness_observed"] is False


def _target() -> Mock:
    target = Mock(area=Rect(0, 0, 600, 600))
    target.position.return_value = ScrollPosition(-1, 50, 25)
    target._automation.same_element.side_effect = lambda a, b: a is b
    return target


def test_unresolved_write_is_never_repeated(monkeypatch: pytest.MonkeyPatch) -> None:
    """A read-only refresh may occur, but no speculative setter retry follows."""
    monkeypatch.setattr(probe.time, "sleep", Mock())
    target = _target()
    with ExitStack() as owners:
        observer = probe._Observer(
            cast("probe.UiaScrollTarget", target), float("inf"), owners
        )
        with pytest.raises(CaptureError, match="unresolved"):
            observer.move(60)
        assert observer.uncertain is True
    target.move.assert_called_once_with(60)
    target.refresh_position.assert_called_once()


def test_incomplete_traversal_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    """The node budget cannot silently certify a partial set of links."""
    monkeypatch.setattr(probe, "_NODES", 1)
    target = _target()
    target._element.control_type.return_value = 50030
    target._walker.relative.return_value = Mock()
    with ExitStack() as owners:
        observer = probe._Observer(
            cast("probe.UiaScrollTarget", target), float("inf"), owners
        )
        with pytest.raises(CaptureError, match="incomplete"):
            observer.links()
    target.move.assert_not_called()


def test_changed_ancestry_rejected_without_runtime_ids() -> None:
    """Reparenting under the same Document still changes retained ancestry."""
    target = _target()
    link, first, other = Mock(), Mock(), Mock()
    with ExitStack() as owners:
        observer = probe._Observer(
            cast("probe.UiaScrollTarget", target), float("inf"), owners
        )
        assert observer._stable_chain(link, [first, target._element])
        assert not observer._stable_chain(link, [other, target._element])


def test_guard_failure_stops_native_observation() -> None:
    """Changed user ownership cannot be bypassed by a diagnostic."""
    target = _target()
    target.check.side_effect = CaptureError("input changed")
    operation = Mock()
    with ExitStack() as owners:
        observer = probe._Observer(
            cast("probe.UiaScrollTarget", target), float("inf"), owners
        )
        with pytest.raises(CaptureError, match="input changed"):
            observer.call(operation)
    operation.assert_not_called()


def test_uncertain_probe_does_not_restore_or_change_product(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The receipt keeps failure and no further setter after uncertain outcome."""
    target = _target()

    def fail(observer: probe._Observer, *_args: object) -> None:
        observer.uncertain = True
        raise CaptureError("unresolved")

    monkeypatch.setattr(probe, "_run_pair", fail)
    receipt = probe.collect_links(
        cast("probe.UiaScrollTarget", target), tmp_path, [], QImage()
    )
    assert receipt["restoration"] == "unproven"
    assert receipt["unresolved_write"] is True
    assert receipt["product_qualification"] is False
    target.move.assert_not_called()
    target.refresh_position.assert_not_called()


@pytest.mark.parametrize("occluded", [False, True])
def test_center_hit_requires_exact_link_or_descendant(*, occluded: bool) -> None:
    """A coincident rectangle does not prove the link owns its painted center."""
    target = _target()
    link, child = Mock(), Mock()
    target._automation.pointer_result.return_value = child
    target._walker.parent.side_effect = [target._element if occluded else link]
    with ExitStack() as owners:
        observer = probe._Observer(
            cast("probe.UiaScrollTarget", target), float("inf"), owners
        )
        assert observer.hit(link, Rect(10, 10, 50, 20)) is not occluded
    target.move.assert_not_called()


def test_nested_scroll_ancestry_is_not_borrowed() -> None:
    """A nearest nested provider excludes its links even inside the outer panel."""
    target = _target()
    link, parent = Mock(), Mock()
    target._walker.parent.return_value = parent
    parent.scroll_pattern.return_value = Mock()
    with ExitStack() as owners:
        observer = probe._Observer(
            cast("probe.UiaScrollTarget", target), float("inf"), owners
        )
        assert observer.ancestry(link) is False


def test_complete_second_enumeration_records_new_and_lost_links() -> None:
    """Unmatched identities remain explicit rather than selecting favorable pairs."""
    a, lost, new = Mock(), Mock(), Mock()
    observer = Mock()
    observer.links.return_value = [a, new]
    observer.same.side_effect = lambda x, y: x is y
    observer.observe.return_value = {"eligible": False, "reason": "occluded"}
    receipt: dict[str, object] = {}
    probe._paired_observations(observer, [a, lost], [{}, {}], receipt)
    records = cast("list[dict[str, object]]", receipt["links"])
    assert records[0]["status"] == "retained_same_identity"
    assert records[1]["status"] == "lost_or_duplicate_identity"
    assert len(cast("list[object]", receipt["new_links"])) == 1
    assert receipt["enumeration_b_complete"] is True


def test_tiny_unacknowledged_move_never_settles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unchanged provider within the old tolerance cannot acknowledge a write."""
    monkeypatch.setattr(probe.time, "sleep", Mock())
    target = _target()
    with ExitStack() as owners:
        observer = probe._Observer(
            cast("probe.UiaScrollTarget", target), float("inf"), owners
        )
        with pytest.raises(CaptureError, match="unresolved"):
            observer.move(50.01)
        assert observer.uncertain
    target.move.assert_called_once_with(50.01)


def test_nested_document_without_scroll_pattern_is_excluded() -> None:
    """A Document boundary remains distinct even without its own scroll pattern."""
    target = _target()
    parent = Mock()
    parent.control_type.return_value = 50030
    parent.scroll_pattern.return_value = None
    target._walker.parent.return_value = parent
    with ExitStack() as owners:
        observer = probe._Observer(
            cast("probe.UiaScrollTarget", target), float("inf"), owners
        )
        assert observer.ancestry(Mock()) is False


def test_lost_link_blocks_complete_witness_despite_good_pair() -> None:
    """A favorable pair's raw statistics cannot hide incomplete retained identity."""
    a, b, lost = Mock(), Mock(), Mock()
    observer = Mock()
    observer.links.return_value = [a, b]
    observer.same.side_effect = lambda x, y: x is y
    observer.observe.side_effect = [
        _record(Rect(10, 280, 50, 20)),
        _record(Rect(100, 280, 50, 20)),
    ]
    first = [
        _record(Rect(10, 300, 50, 20)),
        _record(Rect(100, 300, 50, 20)),
        _record(Rect(200, 300, 50, 20)),
    ]
    receipt: dict[str, object] = {}
    probe._paired_observations(observer, [a, b, lost], first, receipt)
    assert (
        cast("dict[str, object]", receipt["raw_paired_motion"])[
            "motion_witness_observed"
        ]
        is True
    )
    assert receipt["complete_motion_witness_observed"] is False


def test_new_eligible_link_prevents_complete_witness() -> None:
    """Unknown newly eligible motion cannot be hidden by two retained movers."""
    a, b, new = Mock(), Mock(), Mock()
    observer = Mock()
    observer.links.return_value = [a, b, new]
    observer.same.side_effect = lambda x, y: x is y
    observer.observe.side_effect = [
        _record(Rect(10, 280, 50, 20)),
        _record(Rect(100, 280, 50, 20)),
        _record(Rect(200, 280, 50, 20)),
    ]
    receipt: dict[str, object] = {}
    probe._paired_observations(
        observer,
        [a, b],
        [
            _record(Rect(10, 300, 50, 20)),
            _record(Rect(100, 300, 50, 20)),
        ],
        receipt,
    )
    assert (
        cast("dict[str, object]", receipt["raw_paired_motion"])[
            "motion_witness_observed"
        ]
        is True
    )
    assert receipt["complete_motion_witness_observed"] is False


@pytest.mark.parametrize("becomes_eligible", [False, True])
def test_retained_eligibility_transition_blocks_complete_witness(
    *,
    becomes_eligible: bool,
) -> None:
    """A retained but only partly eligible link has unknown paired motion."""
    a, b, transition = Mock(), Mock(), Mock()
    observer = Mock()
    observer.links.return_value = [a, b, transition]
    observer.same.side_effect = lambda x, y: x is y
    before = _record(Rect(200, 300, 50, 20))
    after = _record(Rect(200, 280, 50, 20))
    unqualified = before if becomes_eligible else after
    unqualified.update(eligible=False, reason="center_hit_not_confirmed")
    observer.observe.side_effect = [
        _record(Rect(10, 280, 50, 20)),
        _record(Rect(100, 280, 50, 20)),
        after,
    ]
    receipt: dict[str, object] = {}
    probe._paired_observations(
        observer,
        [a, b, transition],
        [
            _record(Rect(10, 300, 50, 20)),
            _record(Rect(100, 300, 50, 20)),
            before,
        ],
        receipt,
    )
    assert (
        cast("dict[str, object]", receipt["raw_paired_motion"])[
            "motion_witness_observed"
        ]
        is True
    )
    assert receipt["eligibility_transition_count"] == 1
    assert receipt["complete_motion_witness_observed"] is False
    assert receipt["identity_set_complete_for_witness"] is False
    records = cast("list[dict[str, object]]", receipt["links"])
    assert records[-1]["before"] == before
    assert records[-1]["after"] == after


@pytest.mark.parametrize("failure", ["pixels", "state"])
def test_final_b_validation_invalidates_complete_witness(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: str,
) -> None:
    """Paired statistics survive, but failed final evidence cannot certify them."""
    target = _target()
    target._element.control_type.return_value = 50030
    image = QImage(600, 600, QImage.Format.Format_RGB32)
    image.fill(0)
    changed = image.copy()
    changed.setPixel(0, 0, 1)
    observer = Mock(
        target=target,
        original=ScrollPosition(-1, 50, 25),
        proven=ScrollPosition(-1, 21, 25),
        uncertain=False,
        native_calls=0,
    )
    observer.links.return_value = []
    observer.call.side_effect = lambda function, *args: function(*args)
    observer.frame.side_effect = [
        image,
        image,
        image,
        image,
        changed if failure == "pixels" else CaptureError("frame state changed"),
    ]
    monkeypatch.setattr(probe, "_Observer", Mock(return_value=observer))
    restore = Mock()
    monkeypatch.setattr(probe, "_restore", restore)

    def paired(
        _observer: object, _links: object, _first: object, receipt: dict[str, object]
    ) -> None:
        receipt.update(
            raw_paired_motion={"motion_witness_observed": True},
            complete_motion_witness_observed=True,
        )

    monkeypatch.setattr(probe, "_paired_observations", paired)
    states = [ScrollPosition(-1, p, 25) for p in (0, 20, 40, 20, 21)]
    receipt = probe.collect_links(
        cast("probe.UiaScrollTarget", target), tmp_path, states, image
    )
    assert receipt["success"] is False
    assert receipt["complete_motion_witness_observed"] is False
    assert receipt["raw_paired_motion"] == {"motion_witness_observed": True}
    restore.assert_called_once()


def test_restore_checkpoint_oserror_does_not_skip_safe_restore(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Receipt persistence is subordinate to one guarded resolved-state restore."""
    monkeypatch.setattr(probe.time, "sleep", Mock())
    target = _target()
    image = QImage(600, 600, QImage.Format.Format_RGB32)
    image.fill(0)
    target.frame.return_value = image

    def run(observer: probe._Observer, *_args: object) -> None:
        observer.proven = ScrollPosition(-1, 60, 25)
        target.position.return_value = observer.proven

    def move(percent: float) -> None:
        target.position.return_value = ScrollPosition(-1, percent, 25)

    target.move.side_effect = move
    monkeypatch.setattr(probe, "_run_pair", run)
    original_checkpoint = probe._checkpoint

    def checkpoint(output: Path, receipt: dict[str, object], phase: str) -> None:
        if phase == "restoration_pending":
            raise OSError("receipt disk failure")
        original_checkpoint(output, receipt, phase)

    monkeypatch.setattr(probe, "_checkpoint", checkpoint)
    receipt = probe.collect_links(
        cast("probe.UiaScrollTarget", target), tmp_path, [], image
    )
    target.move.assert_called_once_with(50)
    assert receipt["restoration"] == "restored"
    assert receipt["unresolved_write"] is False
    assert receipt["success"] is False
    assert "receipt disk failure" in str(receipt["persistence_errors"])
