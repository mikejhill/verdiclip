"""Full-width stable-prefix proofs, including independently measured frame edges."""

import pytest
from PySide6.QtGui import QColor, QImage, QPainter
from tests.capture.test_scrolling import document

from verdiclip.capture.scrolling import VerticalStitch
from verdiclip.exceptions import CaptureError


def patterned() -> QImage:
    """Use unique rows with the uniform outer margins of the native examples."""
    source = document(1200)
    painter = QPainter(source)
    painter.fillRect(0, 0, 3, 1200, QColor("white"))
    painter.fillRect(77, 0, 3, 1200, QColor("white"))
    painter.end()
    return source


def frame(source: QImage, offset: int, *, outline: bool = False) -> QImage:
    """Model an authored fixed header and native three-row bottom outline."""
    image = source.copy(0, offset, source.width(), 568)
    painter = QPainter(image)
    painter.fillRect(0, 0, image.width(), 64, QColor("lightgray"))
    if outline:
        painter.fillRect(0, 64, 1, 504, QColor("black"))
        painter.fillRect(image.width() - 1, 64, 1, 504, QColor("black"))
        painter.fillRect(0, 567, image.width(), 1, QColor("black"))
        for y, width in [(565, 2), (566, 3)]:
            painter.fillRect(0, y, width, 1, QColor("black"))
            painter.fillRect(image.width() - width, y, width, 1, QColor("black"))
    painter.end()
    return image


@pytest.mark.parametrize("outline", [False, True])
def test_full_sequence_and_short_terminal_step(*, outline: bool) -> None:
    """Retain all authored visible rows exactly once through the bottom."""
    source = patterned()
    stitch = VerticalStitch(frame(source, 0, outline=outline))
    for old, new in [(0, 284), (284, 568), (568, 632)]:
        stitch.append(frame(source, new, outline=outline), new - old)
    expected = source.copy()
    painter = QPainter(expected)
    painter.drawImage(0, 0, frame(source, 0, outline=outline).copy(0, 0, 80, 64))
    if outline:
        painter.fillRect(0, 64, 1, 1136, QColor("black"))
        painter.fillRect(79, 64, 1, 1136, QColor("black"))
        painter.drawImage(0, 1197, frame(source, 632, outline=True).copy(0, 565, 80, 3))
    painter.end()
    assert stitch.finish() == expected


@pytest.mark.parametrize(
    "change", ["header", "grow", "shrink", "interior", "corner", "bottom", "sidebar"]
)
def test_later_change_cannot_relax_accepted_proof(change: str) -> None:
    """Changes cannot widen the initial exemptions or select a partial match."""
    source = patterned()
    stitch = VerticalStitch(frame(source, 0, outline=True))
    stitch.append(frame(source, 284, outline=True), 284)
    image = frame(source, 568, outline=True)
    painter = QPainter(image)
    if change == "header":
        painter.fillRect(10, 10, 20, 10, QColor("red"))
    elif change == "grow":
        painter.fillRect(0, 64, 80, 10, QColor("lightgray"))
    elif change == "shrink":
        painter.drawImage(0, 54, source.copy(0, 622, 80, 10))
    elif change == "interior":
        painter.fillRect(20, 130, 1, 1, QColor("red"))
    elif change == "corner":
        painter.fillRect(1, 566, 1, 1, QColor("red"))
    elif change == "bottom":
        painter.fillRect(30, 567, 1, 1, QColor("red"))
    else:
        painter.fillRect(0, 64, 12, 500, QColor("red"))
    painter.end()
    with pytest.raises(CaptureError):
        stitch.append(image, 284)
    assert stitch.finish().height() == 852


def test_failed_verify_does_not_commit_prefix() -> None:
    """State is committed only after late native guards pass."""
    source = patterned()
    stitch = VerticalStitch(frame(source, 0))

    def fail(_advance: int) -> None:
        raise CaptureError("late guard")

    with pytest.raises(CaptureError, match="late guard"):
        stitch.append(frame(source, 284), 284, verify=fail)
    assert stitch._fixed_prefix is None
    assert stitch.finish().height() == 568


def test_repeated_entire_body_is_ambiguous_despite_provider() -> None:
    """Provider proximity cannot choose among repeated entire overlaps."""
    source = patterned()
    painter = QPainter(source)
    tile = source.copy(0, 64, 80, 60)
    for y in range(64, 1200, 60):
        painter.drawImage(0, y, tile)
    painter.end()
    stitch = VerticalStitch(frame(source, 0))
    with pytest.raises(CaptureError, match="ambiguous"):
        stitch.append(frame(source, 284), 284)


def test_provider_still_checks_unique_pixel_proof() -> None:
    """Pixel uniqueness never overrides a conflicting provider advance."""
    source = patterned()
    stitch = VerticalStitch(frame(source, 0))
    with pytest.raises(CaptureError, match="disagree"):
        stitch.append(frame(source, 284), 200)


def test_repeated_controls_need_unique_complete_overlap() -> None:
    """Local repeated controls cannot beat the one complete full-width movement."""
    source = patterned()
    painter = QPainter(source)
    tile = source.copy(0, 64, 80, 60)
    for index, y in enumerate(range(64, 1200, 60)):
        painter.drawImage(0, y, tile)
        if index % 4 == 0:
            painter.fillRect(20, y, 20, 10, QColor(index, 200, 30))
    painter.end()
    first, second = frame(source, 0, outline=True), frame(source, 284, outline=True)
    stitch = VerticalStitch(first)
    with pytest.raises(CaptureError, match="ambiguous"):
        stitch._alignment(stitch._rows(first), stitch._rows(second))
    stitch.append(second, 284)
    result = stitch.finish()
    assert result.copy(1, 64, 78, 785) == source.copy(1, 64, 78, 785)
    assert result.copy(0, 0, 80, 64) == first.copy(0, 0, 80, 64)
    assert result.copy(0, 849, 80, 3) == second.copy(0, 565, 80, 3)


@pytest.mark.parametrize(
    "mutation", ["broad", "textured", "disconnected", "wide_corner"]
)
def test_bottom_exemption_requires_independent_bounded_outline(mutation: str) -> None:
    """A stable footer or wide corner is not an acceptable native perimeter."""
    source = patterned()
    images = [frame(source, offset, outline=True) for offset in (0, 284)]
    for image in images:
        painter = QPainter(image)
        if mutation == "broad":
            painter.fillRect(0, 564, 80, 4, QColor("black"))
        elif mutation == "textured":
            painter.fillRect(30, 567, 1, 1, QColor("red"))
        elif mutation == "disconnected":
            painter.fillRect(0, 200, 1, 1, QColor("red"))
        else:
            painter.fillRect(0, 566, 4, 1, QColor("black"))
        painter.end()
    with pytest.raises(CaptureError):
        VerticalStitch(images[0]).append(images[1], 284)


def test_stationary_textured_sidebar_is_not_cropped() -> None:
    """The moving proof keeps sidebar columns, including distinctive texture."""
    source = patterned()
    first, second = frame(source, 0), frame(source, 284)
    painter = QPainter(second)
    painter.drawImage(0, 64, first.copy(0, 64, 12, 504))
    painter.end()
    with pytest.raises(CaptureError):
        VerticalStitch(first).append(second, 284)


def test_incidental_later_blank_prefix_keeps_generic_registration() -> None:
    """Unselected header support cannot reject an otherwise exact ordinary join."""
    source = patterned()
    painter = QPainter(source)
    for y in (120, 240):
        painter.fillRect(0, y, 80, 8, QColor("white"))
    painter.end()
    stitch = VerticalStitch(source.copy(0, 0, 80, 568))
    stitch.append(source.copy(0, 120, 80, 568), 120)
    assert stitch._fixed_prefix is None
    stitch.append(source.copy(0, 240, 80, 568), 120)
    assert stitch._fixed_prefix is None
    assert stitch.finish() == source.copy(0, 0, 80, 808)


def test_incidental_initial_blank_prefix_does_not_select_header() -> None:
    """An ordinary first join must not freeze equal blank content as a header."""
    source = patterned()
    painter = QPainter(source)
    for y in (0, 120):
        painter.fillRect(0, y, 80, 8, QColor("white"))
    painter.end()
    stitch = VerticalStitch(source.copy(0, 0, 80, 568))
    stitch.append(source.copy(0, 120, 80, 568), 120)
    assert stitch._fixed_prefix is None
    stitch.append(source.copy(0, 240, 80, 568), 120)
    assert stitch._fixed_prefix is None
    assert stitch.finish() == source.copy(0, 0, 80, 808)


def test_incidental_body_rows_do_not_expand_real_fixed_header() -> None:
    """Coincident body rows remain uncertain until a later pair resolves them."""
    source = patterned()
    painter = QPainter(source)
    for y in (64, 348):
        painter.fillRect(0, y, 80, 8, QColor("white"))
    painter.end()
    stitch = VerticalStitch(frame(source, 0))
    stitch.append(frame(source, 284), 284)
    assert stitch._fixed_prefix is not None
    assert stitch._fixed_prefix == (64, 72)
    stitch.append(frame(source, 568), 284)
    assert stitch._fixed_prefix == (64, 64)
    expected = source.copy(0, 0, 80, 1136)
    painter = QPainter(expected)
    painter.drawImage(0, 0, frame(source, 0).copy(0, 0, 80, 64))
    painter.end()
    assert stitch.finish() == expected


def boundary_coincidence_source() -> QImage:
    """Make moving pixels coincide with four actual stationary header rows."""
    source = patterned()
    painter = QPainter(source)
    painter.fillRect(0, 344, 80, 4, QColor("lightgray"))
    painter.end()
    return source


def test_coincident_header_edge_retains_possible_boundary_interval() -> None:
    """Neither the maximal nor minimal first-pair boundary is frozen as truth."""
    source = boundary_coincidence_source()
    stitch = VerticalStitch(frame(source, 0))
    stitch.append(frame(source, 284), 284)
    assert stitch._fixed_prefix == (60, 64)
    stitch.append(frame(source, 568), 284)
    assert stitch._fixed_prefix == (64, 64)
    expected = source.copy(0, 0, 80, 1136)
    painter = QPainter(expected)
    painter.drawImage(0, 0, frame(source, 0).copy(0, 0, 80, 64))
    painter.end()
    assert stitch.finish() == expected


@pytest.mark.parametrize("row", [20, 61, 64, 130])
def test_uncertain_boundary_does_not_accept_mutations(row: int) -> None:
    """Interior header, uncertain boundary and moving body mutations still fail."""
    source = boundary_coincidence_source()
    stitch = VerticalStitch(frame(source, 0))
    stitch.append(frame(source, 284), 284)
    image = frame(source, 568)
    image.setPixelColor(20, row, QColor("red"))
    with pytest.raises(CaptureError):
        stitch.append(image, 284)
    assert stitch._fixed_prefix == (60, 64)
    assert stitch.finish().height() == 852


def test_interval_narrowing_is_not_committed_before_verify() -> None:
    """A failed late guard cannot consume alternative valid header boundaries."""
    source = boundary_coincidence_source()
    stitch = VerticalStitch(frame(source, 0))
    stitch.append(frame(source, 284), 284)

    def fail(_advance: int) -> None:
        raise CaptureError("late guard")

    with pytest.raises(CaptureError, match="late guard"):
        stitch.append(frame(source, 568), 284, verify=fail)
    assert stitch._fixed_prefix == (60, 64)
    stitch.append(frame(source, 568), 284)
    assert stitch._fixed_prefix == (64, 64)


def test_later_equal_body_differing_from_original_caps_header() -> None:
    """Original equality caps an incidental later run instead of rejecting it."""
    source = patterned()
    painter = QPainter(source)
    painter.fillRect(0, 284, 80, 64, QColor("lightgray"))
    for y in (348, 632):
        painter.fillRect(0, y, 80, 8, QColor("white"))
    painter.end()
    stitch = VerticalStitch(frame(source, 0))
    stitch.append(frame(source, 284), 284)
    assert stitch._fixed_prefix is None
    stitch.append(frame(source, 568), 284)
    assert stitch._fixed_prefix == (64, 64)
    expected = source.copy(0, 0, 80, 1136)
    painter = QPainter(expected)
    painter.drawImage(0, 0, frame(source, 0).copy(0, 0, 80, 64))
    painter.end()
    assert stitch.finish() == expected


@pytest.mark.parametrize("change", ["lower_above_upper", "upper_below_lower"])
def test_inconsistent_interval_never_commits_pixels_or_bounds(change: str) -> None:
    """Contradictory stationary and moving evidence leaves the accepted state intact."""
    source = patterned()
    stitch = VerticalStitch(frame(source, 0))
    stitch.append(frame(source, 284), 284)
    before = stitch.finish()
    assert stitch._fixed_prefix == (64, 64)
    image = frame(source, 568)
    painter = QPainter(image)
    if change == "lower_above_upper":
        painter.fillRect(0, 64, 80, 4, QColor("lightgray"))
    else:
        painter.fillRect(0, 60, 80, 4, QColor("red"))
    painter.end()
    with pytest.raises(CaptureError):
        stitch.append(image, 284)
    assert stitch._fixed_prefix == (64, 64)
    assert stitch.finish() == before


def test_outline_short_terminal_after_multiple_interval_refinements() -> None:
    """Boundary ambiguity narrows across outlined frames before the short last step."""
    source = patterned()
    painter = QPainter(source)
    # On the first move, the last four header rows also look like moving rows.
    painter.fillRect(0, 344, 80, 4, QColor("lightgray"))
    # The first body rows also happen to stay the same until the second move.
    for y in (64, 348):
        painter.fillRect(0, y, 80, 8, QColor("white"))
    painter.end()

    # A rounded outline's body-side black columns distinguish header rows,
    # so use an unchanged full-width header with those same outer columns.
    def outlined(offset: int) -> QImage:
        image = frame(source, offset, outline=True)
        painter = QPainter(image)
        painter.fillRect(0, 0, 1, 64, QColor("black"))
        painter.fillRect(79, 0, 1, 64, QColor("black"))
        painter.end()
        return image

    stitch = VerticalStitch(outlined(0))
    stitch.append(outlined(284), 284)
    assert stitch._fixed_prefix == (60, 72)
    stitch.append(outlined(568), 284)
    assert stitch._fixed_prefix == (64, 64)
    stitch.append(outlined(632), 64)
    assert stitch._fixed_prefix == (64, 64)
    expected = source.copy()
    painter = QPainter(expected)
    painter.drawImage(0, 0, outlined(0).copy(0, 0, 80, 64))
    painter.fillRect(0, 64, 1, 1136, QColor("black"))
    painter.fillRect(79, 64, 1, 1136, QColor("black"))
    painter.drawImage(0, 1197, outlined(632).copy(0, 565, 80, 3))
    painter.end()
    assert stitch.finish() == expected


def test_outline_rejects_width_without_noncorner_interior() -> None:
    """A real narrow image cannot independently identify a bottom interior."""
    image = document(120, width=5)
    stitch = VerticalStitch(image)
    rows = stitch._rows(image)
    assert stitch._bottom_outline(rows, rows, 2) == (0, 0, 0)


def test_outline_rejects_corners_crossing_prefix() -> None:
    """A short body cannot donate header pixels as native-corner evidence."""
    image = QImage(80, 5, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    painter.fillRect(0, 0, 1, 5, QColor("black"))
    painter.fillRect(79, 0, 1, 5, QColor("black"))
    painter.fillRect(0, 4, 80, 1, QColor("black"))
    painter.end()
    stitch = VerticalStitch(image)
    rows = stitch._rows(image)
    assert stitch._bottom_outline(rows, rows, 3) == (0, 0, 0)


def test_complete_flat_overlap_is_not_textured_evidence() -> None:
    """An entire exact uniform body still cannot establish a displacement."""
    image = QImage(80, 120, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    painter.fillRect(0, 0, 80, 64, QColor("gray"))
    painter.end()
    stitch = VerticalStitch(image)
    rows = stitch._rows(image)
    assert stitch._fixed_candidates(rows, rows, 64) == []
