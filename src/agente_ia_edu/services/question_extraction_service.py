"""PHASE 27/28/29 - Question Extraction Engine: persistence + review workflow.

Turns one ``ExtractionResult`` (pure, in-memory - see
``question_extraction.engine``) into staged, queryable rows, and drives the
review status machine (PHASE 29 spec s3):

    EXTRACTED -> VALIDATED -> APPROVED
    EXTRACTED -> REVIEW_REQUIRED -> IN_REVIEW -> APPROVED
    REVIEW_REQUIRED -> REJECTED

Then, via ``question_publication_service.QuestionPublicationService``
(separate module - a different concern from review):

    APPROVED -> PUBLISHED (writes NEW rows into the OFFICIAL Question Bank,
                            tagged origin_type='AUTHORIAL' - never modifies
                            an existing official row)
    APPROVED -> DUPLICATE_REVIEW (a content-hash collision was found at
                                   publish time - never silently discarded)

REJECTED can never reach PUBLISHED (publish only ever starts from APPROVED -
spec s3's "não permitir REJECTED -> PUBLISHED sem nova aprovação" holds
trivially: there is no path from REJECTED to APPROVED in this service).

Batched throughout (spec s25/s26): one INSERT set per collection (runs,
questions, options, assets), never one query per question.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..db.models import (
    ExtractedQuestion,
    ExtractedQuestionAsset,
    ExtractedQuestionOption,
    IngestionDocument,
    QuestionExtractionRun,
)
from .question_extraction import ExtractionResult, extract_questions
from .question_extraction.engine import ENGINE_VERSION

TERMINAL_STATUSES = ("PUBLISHED", "REJECTED")
REVIEWABLE_STATUSES = ("REVIEW_REQUIRED", "IN_REVIEW", "VALIDATED", "DUPLICATE_REVIEW")
REJECTION_REASONS = (
    "DUPLICATE", "CORRUPTED_SOURCE", "INCOMPLETE_SOURCE", "NOT_A_QUESTION",
    "UNUSABLE_CONTENT", "OTHER",
)
# spec s5 - deterministic, no AI. Complex structural ambiguities always sort
# last (P4) regardless of confidence; otherwise priority is purely a
# function of confidence, so a professor works through cheap fixes first.
_COMPLEX_REASONS = frozenset({
    "COLUMN_AMBIGUITY", "BROKEN_READING_ORDER", "CROSS_PAGE_AMBIGUITY",
    "SEQUENCE_ANOMALY", "FORMULA_AMBIGUITY",
})


class QuestionExtractionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class QuestionExtractionNotFound(LookupError):
    pass


def _fingerprint(question_number: int, normalized_text: str) -> str:
    return hashlib.sha256(f"{question_number}:{normalized_text}".encode("utf-8")).hexdigest()


def compute_priority(confidence: float, review_reasons: list[str] | None) -> str:
    """PHASE 29 spec s5 - P1 (fastest fixes) .. P4 (hardest). Pure function
    of already-computed, deterministic signals (confidence + structured
    review reasons) - never a model, never a guess."""
    reasons = set(review_reasons or [])
    if reasons & _COMPLEX_REASONS or len(reasons) >= 3:
        return "P4"
    if confidence >= 0.6:
        return "P1"
    if confidence >= 0.4:
        return "P2"
    return "P3"


def record_review_event(
    question: ExtractedQuestion, *, event: str, actor: str,
    to_status: str | None = None, detail: dict | None = None,
) -> None:
    """Append one entry to the audit trail (spec s21: actor, timestamp,
    previous state, new state, and now also non-status edits - never
    overwritten, always appended)."""
    history = list(question.status_history or [])
    history.append({
        "event": event, "from_status": question.review_status, "to_status": to_status,
        "actor": actor, "at": datetime.now(timezone.utc).isoformat(), "detail": detail,
    })
    question.status_history = history


def _record_transition(question: ExtractedQuestion, *, to_status: str, actor: str) -> None:
    record_review_event(question, event="STATUS_CHANGE", actor=actor, to_status=to_status)


class QuestionExtractionService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # STEP 1-12 (engine) + persistence, idempotent per (document, engine version)
    # ------------------------------------------------------------------
    async def run_extraction(
        self,
        ingestion_document_id: UUID,
        filepath: Path,
        *,
        started_by: str,
        school_id: UUID | None,
        expected_question_count: int | None = None,
        use_column_detection: bool = True,
    ) -> tuple[QuestionExtractionRun, bool]:
        """Returns (run, created). created=False -> an identical-engine-
        version run for this document already exists; it is returned
        as-is, never duplicated (spec s17/s21)."""
        existing = (await self._session.execute(
            select(QuestionExtractionRun).where(
                QuestionExtractionRun.ingestion_document_id == ingestion_document_id,
                QuestionExtractionRun.engine_version == ENGINE_VERSION,
            )
        )).scalars().first()
        if existing is not None:
            return existing, False

        try:
            result: ExtractionResult = extract_questions(
                filepath, expected_question_count=expected_question_count,
                use_column_detection=use_column_detection,
            )
        except Exception as exc:  # noqa: BLE001
            run = QuestionExtractionRun(
                ingestion_document_id=ingestion_document_id, school_id=school_id,
                engine_version=ENGINE_VERSION, document_hash="",
                run_status="FAILED", error_message=str(exc),
                started_by_external_identity=started_by,
            )
            self._session.add(run)
            await self._session.commit()
            raise QuestionExtractionError("EXTRACTION_FAILED", str(exc)) from exc

        report = result.validation
        run = QuestionExtractionRun(
            ingestion_document_id=ingestion_document_id, school_id=school_id,
            engine_version=ENGINE_VERSION, document_hash=result.document_hash,
            run_status="COMPLETED",
            expected_question_count=report.expected_question_count,
            detected_question_count=report.detected_question_count,
            validated_question_count=sum(
                1 for q in result.questions if q.review_status == "VALIDATED"),
            review_required_count=sum(
                1 for q in result.questions if q.review_status == "REVIEW_REQUIRED"),
            missing_numbers=report.missing_numbers or None,
            duplicated_numbers=report.duplicated_numbers or None,
            sequence_gaps=report.sequence_gaps or None,
            validated=report.validated,
            started_by_external_identity=started_by,
        )
        self._session.add(run)
        await self._session.flush()  # need run.id for the batch below

        question_rows = []
        for q in result.questions:
            normalized = q.draft.normalized_text
            question_rows.append(ExtractedQuestion(
                run_id=run.id, question_number=q.draft.number,
                question_type=q.draft.question_type, raw_text=q.draft.raw_text,
                normalized_text=normalized,
                reconstructed_text=q.reconstructed_text or None,
                reconstruction_applied=q.reconstruction_applied,
                review_reasons=q.review_reasons or None,
                status_history=[{
                    "event": "EXTRACTED", "from_status": None, "to_status": q.review_status,
                    "actor": started_by, "at": datetime.now(timezone.utc).isoformat(), "detail": None,
                }],
                fingerprint=_fingerprint(q.draft.number, normalized),
                extraction_confidence=q.draft.confidence,
                flags=sorted(q.draft.flags) or None, review_status=q.review_status,
                source_page_start=q.source_page_start, source_page_end=q.source_page_end,
                cross_page=q.cross_page, school_id=school_id,
                # PHASE 31 (additive) - captured by
                # extract_resolutions_by_question() and wired onto the
                # engine result; "NONE"/None for practically every question
                # in the current real corpus (no ENEM pilot PDF has a
                # resolution section), explicit here rather than relying
                # only on the model's own default so the persisted row
                # always reflects THIS run's actual finding.
                resolution_raw_text=q.resolution_raw_text,
                resolution_status=q.resolution_status,
            ))
        self._session.add_all(question_rows)
        await self._session.flush()  # one flush for every question - not per-row

        option_rows = []
        asset_rows = []
        for db_question, extracted in zip(question_rows, result.questions):
            for i, opt in enumerate(extracted.draft.options, start=1):
                option_rows.append(ExtractedQuestionOption(
                    question_id=db_question.id, label=opt.label, text=opt.text, position=i,
                ))
            for asset in extracted.assets:
                asset_rows.append(ExtractedQuestionAsset(
                    question_id=db_question.id, run_id=run.id, asset_type=asset.asset_type,
                    source_page=asset.source_page,
                    bbox=list(asset.bbox) if asset.bbox else None,
                    digest=asset.digest, extraction_confidence=asset.extraction_confidence,
                    status="ASSOCIATED",
                ))
        # PHASE 29 (spec s8) - an image the engine could not confidently
        # attribute to any question is still persisted (question_id=NULL,
        # status=UNASSOCIATED), so a human can decide - never dropped.
        for image in result.unassociated_images:
            asset_rows.append(ExtractedQuestionAsset(
                question_id=None, run_id=run.id, asset_type="IMAGE",
                source_page=image.page, bbox=list(image.bbox) if image.bbox else None,
                digest=image.digest, extraction_confidence=0.0, status="UNASSOCIATED",
            ))
        if option_rows:
            self._session.add_all(option_rows)
        if asset_rows:
            self._session.add_all(asset_rows)
        await self._session.commit()
        # commit expires every attribute (incl. PK, on the real session -
        # expire_on_commit=True) and the route (run_extraction handler)
        # reads run.id right after this call returns with no intervening
        # await, then serialises run via _run_to_dict - both would raise
        # MissingGreenlet without this refresh.
        await self._session.refresh(run)
        return run, True

    # ------------------------------------------------------------------
    async def get_run(self, run_id: UUID) -> QuestionExtractionRun:
        run = await self._session.get(QuestionExtractionRun, run_id)
        if run is None:
            raise QuestionExtractionNotFound("extraction run not found")
        return run

    async def list_runs(self, *, school_id: UUID | None) -> list[QuestionExtractionRun]:
        q = select(QuestionExtractionRun)
        if school_id is not None:
            q = q.where(QuestionExtractionRun.school_id == school_id)
        return list((await self._session.execute(
            q.order_by(QuestionExtractionRun.created_at.desc())
        )).scalars().all())

    async def list_questions(
        self, run_id: UUID, *, review_status: str | None = None,
    ) -> list[ExtractedQuestion]:
        q = (
            select(ExtractedQuestion)
            .options(selectinload(ExtractedQuestion.options), selectinload(ExtractedQuestion.assets))
            .where(ExtractedQuestion.run_id == run_id)
        )
        if review_status:
            q = q.where(ExtractedQuestion.review_status == review_status)
        return list((await self._session.execute(
            q.order_by(ExtractedQuestion.question_number)
        )).scalars().all())

    async def get_question(self, question_id: UUID) -> ExtractedQuestion:
        """Eager-loads ``options``/``assets`` (spec s10/s8: both are read
        and edited together with the question in the review UI) so later
        attribute access never needs a lazy DB round-trip - an AsyncSession
        lazy-load outside an active await raises MissingGreenlet."""
        q = await self._session.scalar(
            select(ExtractedQuestion)
            .options(selectinload(ExtractedQuestion.options), selectinload(ExtractedQuestion.assets))
            .where(ExtractedQuestion.id == question_id)
        )
        if q is None:
            raise QuestionExtractionNotFound("extracted question not found")
        return q

    # ------------------------------------------------------------------
    # PHASE 29 - REVIEW QUEUE (spec s4/s5/s16/s17): every question a
    # professor still needs to look at, deterministically prioritised.
    # ------------------------------------------------------------------
    async def list_review_queue(
        self, *, school_id: UUID | None, run_id: UUID | None = None,
        review_status: str | list[str] | None = None, reason: str | None = None,
        min_confidence: float | None = None, max_confidence: float | None = None,
        page: int | None = None, limit: int = 50, offset: int = 0,
    ) -> list[dict]:
        """Returns dicts (question + computed priority + document context),
        priority-sorted (P1 first), then lowest confidence first. school_id
        scopes to the caller's tenant (spec s22) - never optional at the API
        layer, only optional here for a platform-admin/report-script caller
        that already resolved its own scope."""
        q = (
            select(ExtractedQuestion, QuestionExtractionRun, IngestionDocument)
            .join(QuestionExtractionRun, ExtractedQuestion.run_id == QuestionExtractionRun.id)
            .join(IngestionDocument, QuestionExtractionRun.ingestion_document_id == IngestionDocument.id)
        )
        if school_id is not None:
            q = q.where(ExtractedQuestion.school_id == school_id)
        if run_id is not None:
            q = q.where(ExtractedQuestion.run_id == run_id)
        if review_status is not None:
            statuses = [review_status] if isinstance(review_status, str) else list(review_status)
            q = q.where(ExtractedQuestion.review_status.in_(statuses))
        else:
            q = q.where(ExtractedQuestion.review_status.in_(REVIEWABLE_STATUSES))
        if page is not None:
            q = q.where(ExtractedQuestion.source_page_start == page)
        if min_confidence is not None:
            q = q.where(ExtractedQuestion.extraction_confidence >= min_confidence)
        if max_confidence is not None:
            q = q.where(ExtractedQuestion.extraction_confidence <= max_confidence)
        rows = (await self._session.execute(q)).all()

        out = []
        for question, run, doc in rows:
            reasons = question.review_reasons or []
            if reason and reason not in reasons:
                continue
            priority = compute_priority(float(question.extraction_confidence), reasons)
            out.append({
                "id": str(question.id), "run_id": str(question.run_id),
                "document": doc.filename, "question_number": question.question_number,
                "source_page_start": question.source_page_start,
                "source_page_end": question.source_page_end,
                "review_status": question.review_status,
                "extraction_confidence": float(question.extraction_confidence),
                "review_reasons": reasons, "priority": priority,
                "created_at": question.created_at.isoformat(),
                "updated_at": question.updated_at.isoformat(),
                "reviewed_by": question.reviewed_by_external_identity,
            })
        # spec s5 - deterministic sort: priority, then confidence ascending
        # (the professor sees the "quick wins" - already decent confidence,
        # simple reason - before the hardest/lowest-confidence cases within
        # the same tier), then question number for a stable tie-break.
        priority_rank = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}
        out.sort(key=lambda r: (priority_rank[r["priority"]], r["extraction_confidence"], r["question_number"]))
        return out[offset: offset + limit]

    async def queue_progress(self, *, school_id: UUID | None, run_id: UUID | None = None) -> dict:
        """spec s15/s17 - real counts, never estimated."""
        q = select(ExtractedQuestion)
        if school_id is not None:
            q = q.where(ExtractedQuestion.school_id == school_id)
        if run_id is not None:
            q = q.where(ExtractedQuestion.run_id == run_id)
        questions = list((await self._session.execute(q)).scalars().all())
        total = len(questions)
        approved = sum(1 for x in questions if x.review_status in ("APPROVED", "PUBLISHED"))
        rejected = sum(1 for x in questions if x.review_status == "REJECTED")
        pending = sum(1 for x in questions if x.review_status in REVIEWABLE_STATUSES)
        return {
            "total": total, "approved": approved, "rejected": rejected,
            "pending": pending, "published": sum(1 for x in questions if x.review_status == "PUBLISHED"),
            "percent_complete": round(100 * (approved + rejected) / total, 1) if total else 0.0,
        }

    # ------------------------------------------------------------------
    # STEP: REVIEW (manual correction - raw_text/normalized_text/
    # reconstructed_text are NEVER overwritten - spec s7/s24: a human edit
    # is a LAYER over extraction, stored separately as reviewed_text)
    # ------------------------------------------------------------------
    async def start_review(self, question_id: UUID, *, reviewer: str) -> ExtractedQuestion:
        """REVIEW_REQUIRED -> IN_REVIEW (spec s3). A no-op re-open of an
        already-open/clean question; refused from a terminal/duplicate
        status - those need their own explicit action first."""
        question = await self.get_question(question_id)
        if question.review_status in ("IN_REVIEW", "VALIDATED"):
            return question
        if question.review_status != "REVIEW_REQUIRED":
            raise QuestionExtractionError(
                "REVIEW_BLOCKED", f"cannot start review from status {question.review_status!r}")
        _record_transition(question, to_status="IN_REVIEW", actor=reviewer)
        question.review_status = "IN_REVIEW"
        question.reviewed_by_external_identity = reviewer
        await self._session.commit()
        # commit expires every attribute (incl. scalar columns and PK - the
        # real Postgres/production session has expire_on_commit=True) - the
        # caller (route) builds its response dict right after this returns,
        # with no further await in between, so a bare attribute access would
        # raise MissingGreenlet. refresh(attribute_names=[...]) reloads ONLY
        # the names given (NOT scalars too) - a bare refresh() call for
        # scalars, then a second call for the relationships the route's
        # _question_to_dict(with_options=True, with_assets=True) reads.
        await self._session.refresh(question)
        await self._session.refresh(question, attribute_names=["options", "assets"])
        return question

    async def update_question(
        self, question_id: UUID, *, reviewed_text: str | None = None,
        options: list[dict] | None = None, question_type: str | None = None,
        notes: str | None = None, reviewed_by: str,
    ) -> ExtractedQuestion:
        """``options`` (when given) fully replaces the option set - add/
        remove/reorder/edit are all expressed as one new ordered list (spec
        s10); a question is never assumed to need A-E (discursive questions
        keep an empty list)."""
        question = await self.get_question(question_id)
        if question.review_status in TERMINAL_STATUSES:
            raise QuestionExtractionError(
                "EDIT_BLOCKED",
                "cannot edit a question that is already PUBLISHED/REJECTED "
                "(spec s24: never alter retroactively what was finalised)")
        changed = False
        if reviewed_text is not None and reviewed_text != question.reviewed_text:
            question.reviewed_text = reviewed_text
            record_review_event(question, event="TEXT_EDIT", actor=reviewed_by,
                          detail={"length": len(reviewed_text)})
            changed = True
        if question_type is not None and question_type != question.question_type:
            question.question_type = question_type
            record_review_event(question, event="TYPE_EDIT", actor=reviewed_by, detail={"question_type": question_type})
            changed = True
        if options is not None:
            existing = list((await self._session.execute(
                select(ExtractedQuestionOption).where(ExtractedQuestionOption.question_id == question.id)
            )).scalars().all())
            for opt in existing:
                await self._session.delete(opt)
            await self._session.flush()
            for i, opt in enumerate(options, start=1):
                self._session.add(ExtractedQuestionOption(
                    question_id=question.id, label=str(opt["label"]).upper(),
                    text=str(opt["text"]), position=i,
                    is_correct=bool(opt.get("is_correct", False)),
                ))
            record_review_event(question, event="OPTIONS_EDIT", actor=reviewed_by,
                          detail={"option_count": len(options)})
            changed = True
        if notes is not None and notes != question.notes:
            question.notes = notes
            record_review_event(question, event="NOTES_EDIT", actor=reviewed_by)
            changed = True
        if changed and question.review_status in ("REVIEW_REQUIRED", "IN_REVIEW"):
            _record_transition(question, to_status="VALIDATED", actor=reviewed_by)
            question.review_status = "VALIDATED"
        question.reviewed_by_external_identity = reviewed_by
        await self._session.commit()
        # commit expires every attribute (scalars included - production runs
        # with expire_on_commit=True) and the route builds its response dict
        # right after this returns, with no further await in between - a
        # bare attribute access would raise MissingGreenlet. refresh(
        # attribute_names=[...]) reloads ONLY the names given (not scalars
        # too), so a bare refresh() first for scalars, then a second call
        # for the relationships the route's _question_to_dict(with_options=
        # True, with_assets=True) reads (options is reloaded unconditionally
        # too, since get_question's earlier selectinload is stale either way
        # once this method has run at all).
        await self._session.refresh(question)
        await self._session.refresh(question, attribute_names=["options", "assets"])
        return question

    def _approval_blockers(
        self, question: ExtractedQuestion, options: list[ExtractedQuestionOption],
    ) -> list[str]:
        """spec s11 - checked before approval; the caller shows these
        verbatim so the professor knows exactly what to fix. ``options`` is
        passed in explicitly (queried separately) rather than accessed via
        the ORM relationship - a bare lazy-load on an AsyncSession object
        outside an active await raises MissingGreenlet."""
        errors: list[str] = []
        statement = question.reviewed_text or question.reconstructed_text or question.normalized_text
        if not statement or not statement.strip():
            errors.append("O enunciado está vazio.")
        if not question.question_number or question.question_number <= 0:
            errors.append("Número da questão inválido.")
        if question.question_type == "multiple_choice":
            options = sorted(options, key=lambda o: o.position)
            labels = [o.label for o in options]
            if len(labels) < 2:
                errors.append("Questão de múltipla escolha precisa de ao menos 2 alternativas.")
            elif len(labels) != len(set(labels)):
                errors.append("Alternativas duplicadas.")
            if any(not (o.text or "").strip() for o in options):
                errors.append("Existe alternativa com texto vazio.")
            # migration 051 bugfix: without this, publish_run had nothing to
            # copy into the official QuestionOption.is_valid_option, and
            # that column's default (True) silently marked every option of
            # every published question "correct" instead of exactly one.
            correct_count = sum(1 for o in options if o.is_correct)
            if correct_count == 0:
                errors.append("Nenhuma alternativa marcada como correta.")
            elif correct_count > 1:
                errors.append("Mais de uma alternativa marcada como correta.")
        return errors

    async def approve_question(self, question_id: UUID, *, reviewed_by: str) -> ExtractedQuestion:
        question = await self.get_question(question_id)
        if question.review_status not in ("VALIDATED", "REVIEW_REQUIRED", "IN_REVIEW", "DUPLICATE_REVIEW"):
            raise QuestionExtractionError(
                "APPROVAL_BLOCKED", f"cannot approve from status {question.review_status!r}")
        if question.review_status == "REVIEW_REQUIRED":
            raise QuestionExtractionError(
                "REVIEW_REQUIRED",
                "this question still needs a human edit before approval "
                "(spec s18: a confidence/structure problem is never approved as-is)")
        options = list((await self._session.execute(
            select(ExtractedQuestionOption).where(ExtractedQuestionOption.question_id == question.id)
        )).scalars().all())
        errors = self._approval_blockers(question, options)
        if errors:
            raise QuestionExtractionError("APPROVAL_VALIDATION_FAILED", "; ".join(errors))
        _record_transition(question, to_status="APPROVED", actor=reviewed_by)
        question.review_status = "APPROVED"
        question.reviewed_by_external_identity = reviewed_by
        await self._session.commit()
        # see start_review's comment - commit expires scalars too (real
        # session has expire_on_commit=True) and the route reads them right
        # after this returns with no intervening await.
        await self._session.refresh(question)
        return question

    async def reject_question(
        self, question_id: UUID, *, reviewed_by: str, reason: str, notes: str | None = None,
    ) -> ExtractedQuestion:
        if reason not in REJECTION_REASONS:
            raise QuestionExtractionError(
                "INVALID_REJECTION_REASON", f"reason must be one of {REJECTION_REASONS}")
        question = await self.get_question(question_id)
        if question.review_status == "PUBLISHED":
            raise QuestionExtractionError("REJECTION_BLOCKED", "cannot reject an already-published question")
        _record_transition(question, to_status="REJECTED", actor=reviewed_by)
        question.review_status = "REJECTED"
        question.rejection_reason = reason
        question.reviewed_by_external_identity = reviewed_by
        if notes:
            question.notes = notes
        await self._session.commit()
        # see start_review's comment.
        await self._session.refresh(question)
        return question

    # ------------------------------------------------------------------
    # PHASE 31 - RESOLUTION REVIEW (spec: step-by-step resolution capture).
    # resolution_status is INDEPENDENT of review_status - a question can be
    # APPROVED with its resolution still PENDING_REVIEW or entirely absent
    # (NONE). This mirrors the statement's edit/approve/reject pattern
    # (update_question/approve_question/reject_question above) as its own,
    # parallel state machine: NONE|PENDING_REVIEW|REJECTED -> PENDING_REVIEW
    # (edit) -> APPROVED|REJECTED. Unlike the statement's TERMINAL_STATUSES
    # gate, there is no blanket "can't edit a REJECTED/APPROVED resolution"
    # rule: a reviewer fixing a mistake (including one caught AFTER
    # approval) is expected and always reopens review - see update_resolution.
    # ------------------------------------------------------------------
    async def update_resolution(
        self, question_id: UUID, *, resolution_reviewed_text: str, reviewed_by: str,
    ) -> ExtractedQuestion:
        """Edits the human-reviewed resolution text. Design decision: ANY
        edit (whatever the current resolution_status - NONE, PENDING_REVIEW,
        APPROVED, or REJECTED) moves resolution_status to PENDING_REVIEW.
        Unlike update_question (which only forwards REVIEW_REQUIRED/
        IN_REVIEW -> VALIDATED and leaves an already-APPROVED question
        untouched by a later edit), an APPROVED resolution is a certification
        that the *current* resolution_reviewed_text was checked - editing it
        invalidates that certification, so it must go back through review
        rather than silently keep an APPROVED label on unreviewed text.
        Never touches resolution_raw_text (spec: raw capture vs. human
        review are always kept separate, same discipline as raw_text/
        reviewed_text for the statement)."""
        question = await self.get_question(question_id)
        from_resolution_status = question.resolution_status
        question.resolution_reviewed_text = resolution_reviewed_text
        question.resolution_status = "PENDING_REVIEW"
        record_review_event(
            question, event="RESOLUTION_TEXT_EDIT", actor=reviewed_by,
            detail={"length": len(resolution_reviewed_text),
                    "from_resolution_status": from_resolution_status,
                    "to_resolution_status": "PENDING_REVIEW"},
        )
        await self._session.commit()
        # see start_review's comment - commit expires scalars too (real
        # session has expire_on_commit=True) and the route reads them right
        # after this returns with no intervening await.
        await self._session.refresh(question)
        return question

    async def approve_resolution(self, question_id: UUID, *, reviewed_by: str) -> ExtractedQuestion:
        """Validation: never approve an empty/absent resolution (mirrors
        _approval_blockers's empty-statement check). Falls back to
        resolution_raw_text when no human edit exists yet, same fallback
        order used to display the statement (reviewed_text or
        reconstructed_text or normalized_text)."""
        question = await self.get_question(question_id)
        text = question.resolution_reviewed_text or question.resolution_raw_text
        if not text or not text.strip():
            raise QuestionExtractionError(
                "RESOLUTION_APPROVAL_VALIDATION_FAILED", "A resolução está vazia.")
        # NOTE: record_review_event's own ``to_status`` param pairs with
        # ``from_status=question.review_status`` (the STATEMENT's status) -
        # passing it here would misleadingly log a review_status transition
        # that never happened. The resolution transition goes in ``detail``
        # instead.
        record_review_event(
            question, event="RESOLUTION_STATUS_CHANGE", actor=reviewed_by,
            detail={"from_resolution_status": question.resolution_status, "to_resolution_status": "APPROVED"},
        )
        question.resolution_status = "APPROVED"
        await self._session.commit()
        # see start_review's comment.
        await self._session.refresh(question)
        return question

    async def reject_resolution(
        self, question_id: UUID, *, reviewed_by: str, reason: str | None = None,
    ) -> ExtractedQuestion:
        """No fixed reason enum (unlike reject_question/REJECTION_REASONS) -
        resolution capture is a new, rarer path (spec: real ENEM source
        PDFs almost never carry a resolution section), so ``reason`` is a
        free-form note recorded on the audit trail rather than a structured
        column - there is no resolution_rejection_reason column in the
        schema (migration 047 only adds resolution_raw_text/
        resolution_reviewed_text/resolution_status)."""
        question = await self.get_question(question_id)
        # see approve_resolution's note on why ``to_status`` is not used here.
        record_review_event(
            question, event="RESOLUTION_STATUS_CHANGE", actor=reviewed_by,
            detail={"from_resolution_status": question.resolution_status,
                    "to_resolution_status": "REJECTED", "reason": reason},
        )
        question.resolution_status = "REJECTED"
        await self._session.commit()
        # see start_review's comment.
        await self._session.refresh(question)
        return question

    # ------------------------------------------------------------------
    # PHASE 29 - ASSETS (spec s8): an image the engine could not
    # confidently attribute to any question is never dropped - a human
    # explicitly associates or ignores it.
    # ------------------------------------------------------------------
    async def list_candidate_assets(self, question_id: UUID) -> list[ExtractedQuestionAsset]:
        """Unassociated assets in the SAME run whose page falls within this
        question's page range - the candidates a professor can attach."""
        question = await self.get_question(question_id)
        q = select(ExtractedQuestionAsset).where(
            ExtractedQuestionAsset.run_id == question.run_id,
            ExtractedQuestionAsset.status == "UNASSOCIATED",
            ExtractedQuestionAsset.source_page >= question.source_page_start,
            ExtractedQuestionAsset.source_page <= question.source_page_end,
        )
        return list((await self._session.execute(q)).scalars().all())

    async def associate_asset(self, question_id: UUID, asset_id: UUID, *, reviewed_by: str) -> ExtractedQuestionAsset:
        question = await self.get_question(question_id)
        asset = await self._session.get(ExtractedQuestionAsset, asset_id)
        if asset is None or asset.run_id != question.run_id:
            raise QuestionExtractionNotFound("asset not found in this run")
        # An asset already tied to a DIFFERENT question is not a free
        # candidate for this one - list_candidate_assets() never offers it,
        # so only a stale/forged asset_id reaches here. Without this check
        # the call would silently steal the asset (no error, and no audit
        # entry on the question that lost it - spec s21 requires every
        # change be traceable). Re-associating an asset THIS question
        # already owns stays a harmless idempotent no-op (double-click safe).
        if asset.question_id is not None and asset.question_id != question.id:
            raise QuestionExtractionError(
                "ASSET_NOT_AVAILABLE",
                f"asset {asset.id} is already associated with a different question")
        asset.question_id = question.id
        asset.status = "ASSOCIATED"
        record_review_event(question, event="ASSET_ASSOCIATED", actor=reviewed_by,
                      detail={"asset_id": str(asset.id), "source_page": asset.source_page})
        await self._session.commit()
        # see start_review's comment - the route's _asset_to_dict reads
        # scalar attributes right after this returns.
        await self._session.refresh(asset)
        return asset

    async def ignore_asset(self, question_id: UUID, asset_id: UUID, *, reviewed_by: str) -> ExtractedQuestionAsset:
        question = await self.get_question(question_id)
        asset = await self._session.get(ExtractedQuestionAsset, asset_id)
        if asset is None or asset.run_id != question.run_id:
            raise QuestionExtractionNotFound("asset not found in this run")
        # Mirrors associate_asset's guard: an asset owned by a DIFFERENT
        # question is not this question's to decide on. Without this check
        # a caller could ignore another question's asset while the audit
        # event was recorded on the WRONG question's status_history (spec
        # s21 - every entry must reflect what actually happened). Ignoring
        # a still-UNASSOCIATED candidate, or one THIS question already
        # owns (change of mind after associating it), remains allowed.
        if asset.question_id is not None and asset.question_id != question.id:
            raise QuestionExtractionError(
                "ASSET_NOT_AVAILABLE",
                f"asset {asset.id} is associated with a different question")
        asset.status = "IGNORED"
        record_review_event(question, event="ASSET_IGNORED", actor=reviewed_by,
                      detail={"asset_id": str(asset.id), "source_page": asset.source_page})
        await self._session.commit()
        # see start_review's comment.
        await self._session.refresh(asset)
        return asset
