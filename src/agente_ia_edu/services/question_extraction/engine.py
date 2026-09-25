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
from .boundary import (
    ExtractedQuestionDraft,
    QuestionBoundary,
    classify_and_extract,
    cut_at_answer_key,
    detect_boundaries,
    extract_resolutions_by_question,
)
from .reconstruction import reconstruct_question, review_reasons_for
from .structure import DocumentStructure, PageImage, TextLine, extract_structure
from .validation import CONFIDENCE_REVIEW_THRESHOLD, ValidationReport, review_status_for, validate

ENGINE_VERSION = "phase28-question-reconstruction-1.16.0"


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
    # PHASE 31 (additive) - the source PDF's own "Resolução" section text
    # for this question number, ONLY when extract_resolutions_by_question()
    # could segment it unambiguously. None/"NONE" for practically every
    # question in the current real corpus (spec: no ENEM pilot PDF has a
    # resolution section at all) - a forward-compatible capture path, never
    # invented.
    resolution_raw_text: str | None = None
    resolution_status: str = "NONE"


@dataclass
class ExtractionResult:
    document_hash: str
    page_count: int
    engine_version: str
    questions: list[ExtractedQuestionResult]
    validation: ValidationReport
    answer_key_cut_offset: int
    unassociated_images: list[PageImage] = field(default_factory=list)


def merge_extraction_results(
    baseline: ExtractionResult, enhanced: ExtractionResult,
) -> ExtractionResult:
    """Merge two full passes over the SAME document - ``baseline`` (no
    column reordering) and ``enhanced`` (with it) - by question number,
    keeping for each number whichever pass found MORE recognized options.
    This is the global-reading-order analogue of PHASE 28's own rule for
    its LOCAL per-question reconstruction (spec s2: 'adopted ONLY when it
    produces a measurably better structured result - never blindly').
    Column-major reordering can misread a table/formula as a second
    column (spec s4's own documented risk) and silently break a question
    that was already correctly read without it - found on a real exam
    during PHASE 27/28 hardening. Never lets that happen: the reordered
    pass only ever WINS, it never SILENTLY LOSES information the baseline
    pass already had.

    A second, distinct risk (found on a real PUC-Rio exam, which numbers
    its discursive section 1, 2, 3... independently of its multiple-
    choice section): reordering a page can shift which of two DUPLICATE-
    numbered questions elsewhere in the document boundary detection binds
    a number to, so ``b`` and ``e`` are sometimes not two readings of the
    SAME question at all - comparing their option counts is then
    meaningless, and a wrongly-bound duplicate can even score MORE raw
    options than the correctly-bound one. Confidence already measures
    "does this look like a well-formed, correctly-bounded question," so a
    pass is only trusted to win (by option count OR on a tie) over a
    pass that clears ``CONFIDENCE_REVIEW_THRESHOLD`` on its own if it also
    clears that same bar - never let a REVIEW_REQUIRED-grade result
    silently replace one that would have validated by itself."""
    baseline_by_num = {r.draft.number: r for r in baseline.questions}
    enhanced_by_num = {r.draft.number: r for r in enhanced.questions}
    merged: list[ExtractedQuestionResult] = []
    for number in sorted(set(baseline_by_num) | set(enhanced_by_num)):
        b = baseline_by_num.get(number)
        e = enhanced_by_num.get(number)
        if b is None:
            merged.append(e)
        elif e is None:
            merged.append(b)
        elif (
            b.draft.confidence >= CONFIDENCE_REVIEW_THRESHOLD
            and e.draft.confidence < CONFIDENCE_REVIEW_THRESHOLD
        ):
            merged.append(b)
        elif len(e.draft.options) < len(b.draft.options):
            merged.append(b)
        elif len(e.draft.options) == len(b.draft.options) and b.draft.confidence > e.draft.confidence:
            merged.append(b)
        else:
            merged.append(e)

    report = validate(
        [r.draft for r in merged],
        expected_question_count=enhanced.validation.expected_question_count,
        orphan_asset_count=enhanced.validation.orphan_asset_count,
    )
    return ExtractionResult(
        document_hash=enhanced.document_hash, page_count=enhanced.page_count,
        engine_version=enhanced.engine_version, questions=merged, validation=report,
        answer_key_cut_offset=enhanced.answer_key_cut_offset,
        unassociated_images=enhanced.unassociated_images,
    )


def extract_questions(
    pdf_path: Path,
    *,
    expected_question_count: int | None = None,
    use_column_detection: bool = True,
) -> ExtractionResult:
    """Defaults to True (verified safe across a 10-exam, 7-institution real
    corpus plus both golden pilot PDFs - zero cases of the merged result
    having fewer options than the baseline for any question, see commit
    history for measurements). When True, runs BOTH a baseline pass (no
    reordering) and a column-aware pass, then merges via
    ``merge_extraction_results`` - never lets the reordered pass silently
    downgrade a question the baseline already extracted correctly. Pass
    False explicitly to force the single, unreordered pass."""
    if use_column_detection:
        baseline = _extract_questions_single_pass(
            pdf_path, expected_question_count=expected_question_count, use_column_detection=False)
        enhanced = _extract_questions_single_pass(
            pdf_path, expected_question_count=expected_question_count, use_column_detection=True)
        return merge_extraction_results(baseline, enhanced)
    return _extract_questions_single_pass(
        pdf_path, expected_question_count=expected_question_count, use_column_detection=False)


def _extract_questions_single_pass(
    pdf_path: Path,
    *,
    expected_question_count: int | None = None,
    use_column_detection: bool = False,
) -> ExtractionResult:
    structure = extract_structure(pdf_path, use_column_detection=use_column_detection)
    full_text = structure.text()
    main_text, cut_offset = cut_at_answer_key(full_text)
    # PHASE 31 (additive) - never affects boundary detection/classification
    # above (unchanged, still runs over main_text alone); this only ever
    # ADDS resolution_raw_text/resolution_status onto a result below, and
    # only for a number that also has a real detected question.
    resolutions_by_number = extract_resolutions_by_question(full_text, cut_offset)

    boundaries = detect_boundaries(main_text)
    drafts = [classify_and_extract(b, main_text) for b in boundaries]

    question_lines: dict[int, list[TextLine]] = {}
    reconstructions: dict[int, object] = {}  # number -> ReconstructionResult
    results: list[ExtractedQuestionResult] = []
    for boundary, draft in zip(boundaries, drafts):
        page_start = structure.offset_to_page(boundary.start)
        page_end = structure.offset_to_page(max(boundary.start, boundary.end - 1))
        cross_page = page_end > page_start
        if cross_page:
            draft.flags.add("cross_page")
        question_lines[draft.number] = structure.lines_in_range(boundary.start, boundary.end)

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
        resolution_text = resolutions_by_number.get(draft.number)
        results.append(ExtractedQuestionResult(
            draft=draft, source_page_start=page_start, source_page_end=page_end,
            cross_page=cross_page, reconstructed_text=recon.reconstructed_text,
            reconstruction_applied=recon.reconstruction_applied,
            resolution_raw_text=resolution_text,
            resolution_status="PENDING_REVIEW" if resolution_text else "NONE",
        ))

    asset_map = associate_assets(structure, question_lines)
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
