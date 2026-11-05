"""Pure document-job ownership, state identity and stale-result acceptance."""

from __future__ import annotations

from dataclasses import replace

import pytest

from verdiclip.document.annotations import RectangleShape
from verdiclip.document.commands import AddAnnotations, ReplaceAnnotations
from verdiclip.document.document import Document
from verdiclip.document.history import History
from verdiclip.editor.jobs import DocumentJobs, JobKind, JobOutcome
from verdiclip.geometry import Point, Rect


def test_current_output_delivers_exactly_once(document: Document) -> None:
    """A completed current-state output marks delivery; duplicate results do not."""
    history = History(document, delivered=False)
    jobs = DocumentJobs("A", history)
    ticket = jobs.begin(JobKind.OUTPUT)
    assert jobs.blocks_close
    assert ticket.document_id == "A"
    assert ticket.state_id == 0
    assert jobs.pending == (ticket,)
    assert jobs.finish(ticket) is JobOutcome.CURRENT
    assert history.is_delivered
    assert not jobs.blocks_close
    assert jobs.finish(ticket) is JobOutcome.UNKNOWN


def test_stale_output_and_analysis_never_clear_new_changes(document: Document) -> None:
    """Old output and OCR-like analysis cannot declare newer content delivered."""
    history = History(document, delivered=False)
    jobs = DocumentJobs("A", history)
    output = jobs.begin(JobKind.OUTPUT)
    analysis = jobs.begin(JobKind.ANALYSIS)
    history.execute(AddAnnotations([RectangleShape(rect=Rect(5, 5, 10, 10))], "Draw"))
    assert jobs.finish(output) is JobOutcome.STALE
    assert jobs.finish(analysis) is JobOutcome.STALE
    assert not history.is_delivered
    assert not history.mark_delivered(expected_state_id=0)
    current_analysis = jobs.begin(JobKind.ANALYSIS)
    assert jobs.finish(current_analysis) is JobOutcome.CURRENT
    assert not history.is_delivered


def test_state_identity_survives_undo_redo_but_not_merge_or_branch(
    document: Document,
) -> None:
    """Equal undo depths are insufficient to accept a background result."""
    history = History(document, delivered=False)
    shape = RectangleShape(rect=Rect(0, 0, 10, 10))
    history.execute(AddAnnotations([shape], "Draw"))
    first = history.state_id
    history.undo()
    assert history.state_id == 0
    history.redo()
    assert history.state_id == first
    moved = shape.translated(Point(1, 0))
    history.execute(ReplaceAnnotations([shape], [moved], "Move", merge_key="nudge"))
    jobs = DocumentJobs("A", history)
    ticket = jobs.begin(JobKind.OUTPUT)
    again = moved.translated(Point(1, 0))
    history.execute(ReplaceAnnotations([moved], [again], "Move", merge_key="nudge"))
    assert isinstance(history.state_id, int)
    assert history.state_id != ticket.state_id
    assert jobs.finish(ticket) is JobOutcome.STALE
    history.undo()
    assert document.annotations == (shape,)
    assert history.state_id == first
    history.execute(AddAnnotations([shape.with_new_id()], "Branch"))
    assert not history.can_redo
    assert history.state_id not in {first, ticket.state_id}


def test_cancel_request_is_not_worker_acknowledgement(document: Document) -> None:
    """A requested output cancellation keeps the close lease until acknowledged."""
    history = History(document, delivered=False)
    jobs = DocumentJobs("A", history)
    ticket = jobs.begin(JobKind.OUTPUT)
    assert not jobs.acknowledge_cancel(ticket)
    assert jobs.request_cancel(ticket)
    assert jobs.blocks_close
    with pytest.raises(RuntimeError, match="still running"):
        jobs.close()
    assert jobs.acknowledge_cancel(ticket)
    assert not jobs.blocks_close
    assert not jobs.acknowledge_cancel(ticket)
    assert not jobs.request_cancel(ticket)
    jobs.close()
    jobs.close()
    assert jobs.finish(ticket) is JobOutcome.UNKNOWN
    with pytest.raises(RuntimeError, match="disposed"):
        jobs.begin(JobKind.ANALYSIS)


@pytest.mark.parametrize("edit_after_start", [False, True])
def test_committed_output_after_cancel_is_reported_honestly(
    document: Document, *, edit_after_start: bool
) -> None:
    """Cancellation cannot undo a completed file or clean a different state."""
    history = History(document, delivered=False)
    jobs = DocumentJobs("A", history)
    ticket = jobs.begin(JobKind.OUTPUT)
    assert jobs.request_cancel(ticket)
    if edit_after_start:
        history.execute(
            AddAnnotations([RectangleShape(rect=Rect(5, 5, 10, 10))], "Draw")
        )
    assert jobs.finish(ticket) is JobOutcome.COMMITTED_AFTER_CANCEL
    assert history.is_delivered is not edit_after_start
    assert not jobs.blocks_close


def test_failed_output_and_cancelled_analysis_are_not_delivery(
    document: Document,
) -> None:
    """No successful image output means the document stays undelivered."""
    history = History(document, delivered=False)
    jobs = DocumentJobs("A", history)
    failed = jobs.begin(JobKind.OUTPUT)
    assert jobs.finish(failed, success=False) is JobOutcome.FAILED
    analysis = jobs.begin(JobKind.ANALYSIS)
    assert jobs.request_cancel(analysis)
    assert jobs.finish(analysis) is JobOutcome.CANCELLED
    assert not history.is_delivered


def test_forged_foreign_and_post_close_tickets_cannot_mutate(
    document: Document,
) -> None:
    """Exact ticket/lifetime identity is required, including after document close."""
    history = History(document, delivered=False)
    jobs = DocumentJobs("A", history)
    other = DocumentJobs("B", history)
    ticket = jobs.begin(JobKind.ANALYSIS)
    assert jobs.finish(replace(ticket)) is JobOutcome.UNKNOWN
    assert other.finish(ticket) is JobOutcome.UNKNOWN
    assert jobs.pending == (ticket,)
    jobs.close()
    assert jobs.pending == ()
    assert jobs.finish(ticket) is JobOutcome.UNKNOWN
    assert document.annotations == ()


def test_snapshot_pixels_are_independent_of_document_wrapper(
    document: Document,
) -> None:
    """Worker-owned QImage mutation cannot change the live document's pixels."""
    jobs = DocumentJobs("A", History(document))
    ticket = jobs.begin(JobKind.ANALYSIS)
    original = document.image.pixelColor(0, 0)
    ticket.snapshot.image.fill(0xFFFF0000)
    assert document.image.pixelColor(0, 0) == original
    assert ticket.snapshot.image.pixelColor(0, 0) != original


def test_edit_result_defers_during_close_and_is_one_undo_step(
    document: Document,
) -> None:
    """A modal close decision cannot be raced by an asynchronous edit result."""
    history = History(document, delivered=False)
    jobs = DocumentJobs("A", history)
    ticket = jobs.begin(JobKind.EDIT)
    command = AddAnnotations([RectangleShape(rect=Rect(5, 5, 10, 10))], "Job edit")
    jobs.freeze()
    with pytest.raises(RuntimeError, match="closing"):
        jobs.begin(JobKind.ANALYSIS)
    assert jobs.apply(ticket, command) is JobOutcome.DEFERRED
    assert document.annotations == ()
    jobs.unfreeze()
    assert jobs.apply(ticket, command) is JobOutcome.CURRENT
    assert len(document.annotations) == 1
    assert jobs.apply(ticket, command) is JobOutcome.UNKNOWN
    history.undo()
    assert document.annotations == ()
    analysis = jobs.begin(JobKind.ANALYSIS)
    with pytest.raises(ValueError, match="edit job"):
        jobs.apply(analysis, command)


def test_unsubscribed_view_receives_no_later_notifications(document: Document) -> None:
    """Closing/rebinding a page releases every callback into its old host."""
    history = History(document)
    changes: list[bool] = []

    def listener() -> None:
        changes.append(True)

    history.subscribe(listener)
    assert history.mark_delivered(expected_state_id=history.state_id)
    assert changes == [True]
    history.unsubscribe(listener)
    history.unsubscribe(listener)
    history.execute(AddAnnotations([RectangleShape(rect=Rect(5, 5, 10, 10))], "Draw"))
    assert changes == [True]


def test_repeated_branches_after_discarded_delivery_remain_undoable(
    document: Document,
) -> None:
    """An unreachable delivery marker stays safe across another undo/branch."""
    history = History(document, delivered=False)
    shape = RectangleShape(rect=Rect(5, 5, 10, 10))
    history.execute(AddAnnotations([shape], "First"))
    history.mark_delivered()
    history.undo()
    replacement = shape.with_new_id()
    history.execute(AddAnnotations([replacement], "Replace delivered branch"))
    assert not history.is_delivered
    history.undo()
    third = shape.with_new_id()
    history.execute(AddAnnotations([third], "Branch again"))
    assert document.annotations == (third,)
    assert not history.can_redo
    assert not history.is_delivered
    history.undo()
    history.redo()
    assert document.annotations == (third,)


def test_closed_frozen_edit_and_forged_ticket_are_terminal(document: Document) -> None:
    """Only a live ticket may defer; closed results cannot remain queued."""
    history = History(document, delivered=False)
    jobs = DocumentJobs("A", history)
    ticket = jobs.begin(JobKind.EDIT)
    command = AddAnnotations([RectangleShape(rect=Rect(5, 5, 10, 10))], "Late edit")
    jobs.freeze()
    assert jobs.apply(replace(ticket), command) is JobOutcome.UNKNOWN
    assert jobs.apply(ticket, command) is JobOutcome.DEFERRED
    jobs.close()
    assert jobs.pending == ()
    assert jobs.apply(ticket, command) is JobOutcome.UNKNOWN
    assert document.annotations == ()
