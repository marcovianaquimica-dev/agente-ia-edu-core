"""PHASE 27 - Question Extraction Engine: asset detection + association.

STEP 9 of the pipeline (spec s3/s9): associate an image/graphic/table to
the question whose TERRITORY on the page contains it. NEVER reconstructs a
graphic into text and NEVER substitutes an image with invented content
(spec s9) - an asset that cannot be confidently attributed to one question
is still recorded (page/bbox/digest) and flagged, never dropped.

Association is POSITION-based (bbox y-coordinate vs. each question's own
first line on that page), not page-range-based: real exams routinely place
several questions on one shared page (found on real ENEM/vestibular
exams), and page-range-only attribution put every image on that page onto
EVERY question covering it - both over-associating images to unrelated
neighbours and (via the confidence penalty that produced) pushing
perfectly fine questions into REVIEW_REQUIRED. A question's TERRITORY on a
page runs from its own first line's y-coordinate there up to the next
question's first line on that same page (or the end of the page/document
if it is the last one there); an image with no bbox (rare, when
``page.get_image_bbox`` fails) falls back to whole-page attribution at a
lower confidence, since its exact position within a shared page is
unknown - it is never dropped either way.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass

from .structure import DocumentStructure, PageImage, TextLine


@dataclass(frozen=True)
class AssetAssociation:
    question_number: int
    asset_type: str  # IMAGE
    source_page: int
    bbox: tuple[float, float, float, float] | None
    digest: str
    extraction_confidence: float


def _is_degenerate(image: PageImage) -> bool:
    # a 1x1 spacer/decoration pixel is never real question content.
    return bool(image.width and image.height and image.width * image.height < 100)


def _page_starts(question_lines: dict[int, list[TextLine]]) -> list[tuple[int, float, int]]:
    """One (page, y0, question_number) entry per page a question touches,
    y0 being that question's OWN first line there - the reading-order
    boundary where its territory on that page begins."""
    starts: list[tuple[int, float, int]] = []
    for qnum, lines in question_lines.items():
        first_y0_on_page: dict[int, float] = {}
        for ln in lines:
            if ln.page not in first_y0_on_page or ln.y0 < first_y0_on_page[ln.page]:
                first_y0_on_page[ln.page] = ln.y0
        for page, y0 in first_y0_on_page.items():
            starts.append((page, y0, qnum))
    starts.sort(key=lambda t: (t[0], t[1]))
    return starts


def associate_assets(
    structure: DocumentStructure,
    question_lines: dict[int, list[TextLine]],
) -> dict[int, list[AssetAssociation]]:
    """``question_lines``: {question_number: [TextLine, ...]} - the actual
    lines that question's own text spans (e.g. from
    ``DocumentStructure.lines_in_range``). Returns {question_number:
    [AssetAssociation, ...]} - an image is attributed to whichever question's
    TERRITORY (its own first line on that page, up to the next question's
    first line on that same page) contains it; never to every question
    sharing the page (spec s9: no invented/guessed attribution, but also no
    over-attribution that manufactures false uncertainty for neighbours).
    An image before any question's first line anywhere in the document is
    left unassociated, never guessed."""
    starts = _page_starts(question_lines)
    start_keys = [(page, y0) for page, y0, _ in starts]
    pages_touched = {qnum: {ln.page for ln in lines} for qnum, lines in question_lines.items()}

    out: dict[int, list[AssetAssociation]] = {}
    for image in structure.images:
        if _is_degenerate(image):
            continue
        if image.bbox is not None:
            idx = bisect.bisect_right(start_keys, (image.page, image.bbox[1]))
            if idx == 0:
                continue  # before any known question - never guessed
            owner = starts[idx - 1][2]
            # 0.9 when the image sits on a page the owning question has its
            # own lines on; 0.7 when it was carried forward from an earlier
            # page (e.g. a full-page graphic between two questions' text).
            confidence = 0.9 if image.page in pages_touched.get(owner, set()) else 0.7
            out.setdefault(owner, []).append(AssetAssociation(
                question_number=owner, asset_type="IMAGE", source_page=image.page,
                bbox=image.bbox, digest=image.digest, extraction_confidence=confidence,
            ))
        else:
            # no usable bbox (page.get_image_bbox failed) - exact position
            # within a shared page is unknown, so fall back to whole-page
            # attribution at a visibly lower confidence rather than
            # silently dropping the image.
            for qnum, pages in pages_touched.items():
                if image.page in pages:
                    out.setdefault(qnum, []).append(AssetAssociation(
                        question_number=qnum, asset_type="IMAGE", source_page=image.page,
                        bbox=None, digest=image.digest, extraction_confidence=0.5,
                    ))
    return out


def unassociated_images(
    structure: DocumentStructure, associated: dict[int, list[AssetAssociation]],
) -> list[PageImage]:
    """PHASE 29 (spec s8) - every non-degenerate image NOT matched to any
    question by ``associate_assets`` above. Never dropped: the review flow
    persists these (question_id=NULL, status=UNASSOCIATED) so a human can
    explicitly [Associar] or [Ignorar] them - the engine itself never
    decides on their behalf."""
    matched = {(a.source_page, a.digest) for assocs in associated.values() for a in assocs}
    return [
        image for image in structure.images
        if not _is_degenerate(image) and (image.page, image.digest) not in matched
    ]
