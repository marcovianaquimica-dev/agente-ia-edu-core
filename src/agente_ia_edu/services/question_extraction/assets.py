"""PHASE 27 - Question Extraction Engine: asset detection + association.

STEP 9 of the pipeline (spec s3/s9): associate an image/graphic/table to
the question whose page range contains it. NEVER reconstructs a graphic
into text and NEVER substitutes an image with invented content (spec s9)
- an asset that cannot be confidently attributed to one question is still
recorded (page/bbox/digest) and flagged, never dropped.

Association is by page range only (a question's ``source_page_start``..
``source_page_end``) - deliberately simple and deterministic. A future
phase could use bbox proximity to the question's own text for tighter
attribution on a single crowded page; today's two golden PDFs place at
most one graphic-bearing question per page, so page-range attribution is
sufficient and does not need calibrating against something we cannot
verify without inventing test fixtures the real pilot does not exercise.
"""

from __future__ import annotations

from dataclasses import dataclass

from .structure import DocumentStructure, PageImage


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


def associate_assets(
    structure: DocumentStructure,
    question_pages: dict[int, tuple[int, int]],
) -> dict[int, list[AssetAssociation]]:
    """``question_pages``: {question_number: (page_start, page_end)}.
    Returns {question_number: [AssetAssociation, ...]} - only for questions
    whose page range actually contains an image. An image on a page no
    question's range covers is simply not associated (never invented)."""
    out: dict[int, list[AssetAssociation]] = {}
    for image in structure.images:
        if _is_degenerate(image):
            continue
        for qnum, (start, end) in question_pages.items():
            if start <= image.page <= end:
                confidence = 0.9 if start == end else 0.7  # cross-page: slightly less certain
                out.setdefault(qnum, []).append(AssetAssociation(
                    question_number=qnum, asset_type="IMAGE", source_page=image.page,
                    bbox=image.bbox, digest=image.digest, extraction_confidence=confidence,
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
