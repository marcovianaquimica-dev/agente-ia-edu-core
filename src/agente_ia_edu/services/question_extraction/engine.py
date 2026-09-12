"""PHASE 27/28 - Question Extraction Engine: orchestrator.

Ties structure -> boundary/classification -> LOCAL reconstruction (PHASE
28) -> asset association -> validation into ONE deterministic call. Each
stage lives in its own module and is independently unit-testable; this
function only sequences them - it contains no extraction logic of its own.

PHASE 28 acts strictly BETWEEN classification and validation (spec s16):
boundary detection (the source of PHASE 27's 83/83) is completely
unchanged. For each detected question, a LOCAL, verified column
reconstruction is attempted (``reconstruction.reconstruct_question``) and
adopted ONLY when it produces a measurably better structured result -
never blindly, never to inflate an approval count (spec s2).

Determinism (spec s16/s20): pure functions, no randomness, no network, no
LLM. The same file processed twice produces byte-identical results
(asserted by test).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .assets import AssetAssociation, associate_assets, unassociated_images
from .boundary import ExtractedQuestionDraft, QuestionBoundary, classify_and_extract, cut_at_answer_key, detect_boundaries
from .reconstruction import reconstruct_question, review_reasons_for
from .structure import DocumentStructure, PageImage, extract_structure
from .validation import ValidationReport, review_status_for, validate

ENGINE_VERSION = "phase28-question-reconstruction-1.0.0"


@dataclass
class ExtractedQuestionResult:
    draft: ExtractedQuestionDraft
    source_page_start: int
    source_page_end: int
    cross_page: bool
    assets: list[AssetAssociation] = field(default_factory=list)
    review_status: str = "DISCOVERED"
    reconstructed_text: str = ""
    reconstruction_applied: bool = False
    review_reasons: list[str] = field(default_factory=list)


@dataclass
class ExtractionResult:
    document_hash: str
    page_count: int
    engine_version: str
    questions: list[ExtractedQuestionResult]
    validation: ValidationReport
    answer_key_cut_offset: int
    unassociated_images: list[PageImage] = field(default_factory=list)


def extract_questions(
    pdf_path: Path,
    *,
    expected_question_count: int | None = None,
    use_column_detection: bool = False,
) -> ExtractionResult:
    structure = extract_structure(pdf_path, use_column_detection=use_column_detection)
    full_text = structure.text()
    main_text, cut_offset = cut_at_answer_key(full_text)

    boundaries = detect_boundaries(main_text)
    drafts = [classify_and_extract(b, main_text) for b in boundaries]

    question_pages: dict[int, tuple[int, int]] = {}
    reconstructions: dict[int, object] = {}  # number -> ReconstructionResult
    results: list[ExtractedQuestionResult] = []
    for boundary, draft in zip(boundaries, drafts):
        page_start = structure.offset_to_page(boundary.start)
        page_end = structure.offset_to_page(max(boundary.start, boundary.end - 1))
        cross_page = page_end > page_start
        if cross_page:
            draft.flags.add("cross_page")
        question_pages[draft.number] = (page_start, page_end)

        # PHASE 28: attempt a LOCAL, verified column reconstruction. raw_text
        # (PHASE 27's original) is NEVER overwritten (spec s10) - only
        # normalized_text/options/confidence adopt the reconstruction, and
        # only when it is a measurable structural improvement.
        recon = reconstruct_question(structure, boundary, draft.raw_text)
        if recon.reconstruction_applied:
            # the ORIGINAL boundary's paragraph-break signal is about the
            # PRE-reconstruction line order and no longer applies once the
            # text itself has been rebuilt and independently verified as a
            # structural improvement - re-derive confidence from the
            # reconstructed content on its own merits, not a stale flag.
            fake_boundary = QuestionBoundary(
                number=boundary.number, marker_style=boundary.marker_style,
                start=0, end=len(recon.reconstructed_text),
                preceded_by_paragraph_break=True,
            )
            improved = classify_and_extract(fake_boundary, recon.reconstructed_text)
            draft.normalized_text = improved.normalized_text
            draft.options = improved.options
            draft.question_type = improved.question_type
            draft.confidence = max(draft.confidence, improved.confidence)
            draft.flags.discard("possible_missing_content")
            draft.flags.discard("options_complete_false")
            draft.flags |= improved.flags
            draft.flags.add("column_reconstructed")

        reconstructions[draft.number] = recon
        results.append(ExtractedQuestionResult(
            draft=draft, source_page_start=page_start, source_page_end=page_end,
            cross_page=cross_page, reconstructed_text=recon.reconstructed_text,
            reconstruction_applied=recon.reconstruction_applied,
        ))

    asset_map = associate_assets(structure, question_pages)
    orphan_images = unassociated_images(structure, asset_map)
    for result in results:
        assoc = asset_map.get(result.draft.number, [])
        if assoc:
            result.assets = assoc
            result.draft.flags.add("image_present")
    orphan_assets = len(orphan_images)

    for result in results:
        result.review_status = review_status_for(result.draft)
        if result.review_status == "REVIEW_REQUIRED":
            result.review_reasons = review_reasons_for(
                result.draft, reconstruction=reconstructions[result.draft.number])

    report = validate(drafts, expected_question_count=expected_question_count,
                      orphan_asset_count=orphan_assets)

    return ExtractionResult(
        document_hash=structure.document_hash, page_count=structure.page_count,
        engine_version=ENGINE_VERSION, questions=results, validation=report,
        answer_key_cut_offset=cut_offset, unassociated_images=orphan_images,
    )
