"""PHASE 26 - Authorial Material Ingestion Engine: orchestrator.

Turns:  ARQUIVO -> EXTRAÇÃO -> ESTRUTURA -> CURRICULUM-V2 -> EXERCÍCIOS ->
        REVISÃO -> APROVAÇÃO -> PUBLICAÇÃO
into one deterministic, auditable flow, REUSING every existing piece
end to end:

  * ``MaterialStorage``              - PHASE 26 (new, minimal local storage)
  * ``parse_authorial_document``     - PHASE 26 (new sibling parser)
  * ``IngestionService.ingest_document(parsed_override=...)`` - EXISTING,
    UNMODIFIED (PHASE 3) - persists IngestionDocument/Run/Section/Question/
    Asset exactly as it already does for the ENEM pipeline. Its own hash-based
    ``check_idempotency`` is the single source of truth for duplicate
    detection (spec s4/s21) - never re-implemented here.
  * ``AuthorialCurriculumMatcher``   - PHASE 26 (new, curriculum-v2 only,
    never creates a node - TAXONOMY_GAP when unmatched)
  * ``IngestionMaterialReview``      - PHASE 26 (new, 1:1 review/publish state)
  * ``TheoryMaterialService``        - EXISTING, UNMODIFIED (PHASE 23) - the
    ONLY path from "approved content" to a real TheoryMaterial/Version/
    Section/Block/Exercise. Publication never invents a second material model.

INGESTION != PUBLICATION (spec s6): ingest_file() only extracts, structures,
classifies and lands the result at PENDING_REVIEW/NEEDS_REVIEW. A human must
approve() before publish() can create anything a student can see.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    IngestionDocument,
    IngestionMaterialReview,
    IngestionQuestion,
    IngestionSection,
)
from .authorial_curriculum_matcher import AuthorialCurriculumMatcher
from .authorial_material_parser import parse_authorial_document
from .catalog import TheoryMaterialService
from .ingestion import IngestionService
from .material_storage import MaterialStorage, file_sha256

SUPPORTED_SUFFIXES = {".pdf", ".docx", ".txt", ".md"}

# spec s22 - explicit failure codes, never masked
EXTRACTION_FAILED = "EXTRACTION_FAILED"
STRUCTURE_PARSE_FAILED = "STRUCTURE_PARSE_FAILED"
CURRICULUM_MAPPING_FAILED = "CURRICULUM_MAPPING_FAILED"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
PUBLICATION_BLOCKED = "PUBLICATION_BLOCKED"
UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"

_SECTION_TYPE_MAP = {
    "EPISODE": "CHAPTER", "CHAPTER": "CHAPTER", "SEASON": "CHAPTER",
    "SUBSECTION": "SUBSECTION", "SECTION": "SECTION", "OTHER": "SECTION",
}


class AuthorialIngestionError(ValueError):
    def __init__(self, code: str, message: str, payload: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.payload = payload or {}


class AuthorialIngestionNotFound(LookupError):
    pass


class AuthorialMaterialIngestionService:
    def __init__(self, session: AsyncSession, storage: MaterialStorage | None = None) -> None:
        self._session = session
        self._storage = storage or MaterialStorage()
        self._catalog = TheoryMaterialService()
        self._matcher = AuthorialCurriculumMatcher(session)

    # ------------------------------------------------------------------
    # STEP 1-6: discovery is the caller's job (a folder listing / an
    # uploaded file); identification+extraction+structuring+curriculum
    # mapping+exercise detection all happen here, in one auditable call.
    # ------------------------------------------------------------------
    async def ingest_file(
        self,
        filepath: Path,
        *,
        uploaded_by: str,
        school_id: UUID | None,
        origin_type: str = "AUTHORIAL",
    ) -> tuple[IngestionMaterialReview, bool]:
        """Returns (review, created). created=False means this exact file
        (by hash) was already ingested - the EXISTING review is returned,
        never a duplicate (spec s4/s21)."""
        if filepath.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise AuthorialIngestionError(
                UNSUPPORTED_FORMAT, f"unsupported file format: {filepath.suffix!r}")
        if not filepath.is_file():
            raise AuthorialIngestionError(EXTRACTION_FAILED, "file does not exist")

        digest = file_sha256(filepath)
        existing_doc = (await self._session.execute(
            select(IngestionDocument).where(IngestionDocument.document_hash == digest)
        )).scalars().first()
        if existing_doc is not None:
            review = await self._review_for_document(existing_doc.id)
            if review is not None:
                return review, False
            # a document ingested by the OFFICIAL pipeline with the same
            # bytes (unlikely, but fail closed rather than adopting it
            # silently into the authorial workflow) - report it, never mask.
            raise AuthorialIngestionError(
                EXTRACTION_FAILED,
                "this exact file is already ingested outside the authorial "
                "review workflow (no IngestionMaterialReview for it)",
                {"ingestion_document_id": str(existing_doc.id)},
            )

        managed_path, digest = self._storage.store(filepath, document_hash=digest)

        try:
            parsed = parse_authorial_document(managed_path)
        except Exception as exc:  # noqa: BLE001
            raise AuthorialIngestionError(EXTRACTION_FAILED, str(exc)) from exc

        structure_issues: list[str] = []
        if not parsed.sections:
            structure_issues.append("no section/chapter structure was detected")
        if parsed.total_images:
            structure_issues.append(
                f"{parsed.total_images} visual element(s) detected but not reconstructed "
                "(no IMAGE block is created for them - human review required)")

        service = IngestionService()
        doc, _run = await service.ingest_document(
            self._session, managed_path, ingested_by=uploaded_by,
            source_metadata={
                "school_id": str(school_id) if school_id else None,
                "origin_type": origin_type,
                "original_filename": filepath.name,
                "phase": "PHASE_26_AUTHORIAL",
            },
            parsed_override=parsed,
            document_type_override=None if filepath.suffix.lower() in (".docx", ".pdf") else "OTHER",
        )

        # PHASE 26 (ESTENDER, additive): populate the new content_text column
        # with the full extracted body - ingest_document() itself is
        # UNCHANGED and only ever writes the 500-char content_preview.
        doc_sections = (await self._session.execute(
            select(IngestionSection).where(IngestionSection.document_id == doc.id)
        )).scalars().all()
        by_position = {s.position: s for s in doc_sections}
        for parsed_section in parsed.sections:
            row = by_position.get(parsed_section.position)
            if row is not None and parsed_section.content_lines:
                row.content_text = "\n".join(parsed_section.content_lines)
        await self._session.flush()

        try:
            # candidate texts for the curriculum matcher: the document title
            # plus every section's title AND description - DocxParser puts a
            # chapter's real subtitle in `description` (title is the generic
            # "Episódio N"), so both must be offered to the matcher.
            candidate_texts = [parsed.title]
            for sec in parsed.sections:
                if sec.title:
                    candidate_texts.append(sec.title)
                if sec.description:
                    candidate_texts.append(sec.description)
            match = await self._matcher.match(candidate_texts)
        except Exception as exc:  # noqa: BLE001
            raise AuthorialIngestionError(CURRICULUM_MAPPING_FAILED, str(exc)) from exc

        review_status = "NEEDS_REVIEW" if (match.classification_state == "TAXONOMY_GAP" or structure_issues) \
            else "PENDING_REVIEW"

        review = IngestionMaterialReview(
            ingestion_document_id=doc.id, school_id=school_id, origin_type=origin_type,
            review_status=review_status,
            discipline_code=match.discipline_code, area_code=match.area_code,
            content_code=match.content_code, subcontent_codes=match.subcontent_codes or None,
            classification_state=match.classification_state,
            classification_confidence=match.confidence, classification_source="DETERMINISTIC_MATCH",
            structure_issues=structure_issues or None,
            exercises_detected=len(parsed.questions),
        )
        self._session.add(review)
        await self._session.commit()
        return review, True

    async def _review_for_document(self, document_id: UUID) -> IngestionMaterialReview | None:
        return (await self._session.execute(
            select(IngestionMaterialReview).where(
                IngestionMaterialReview.ingestion_document_id == document_id)
        )).scalars().first()

    async def get_review(self, review_id: UUID) -> IngestionMaterialReview:
        review = await self._session.get(IngestionMaterialReview, review_id)
        if review is None:
            raise AuthorialIngestionNotFound("review not found")
        return review

    async def list_reviews(
        self, *, school_id: UUID | None, review_status: str | None = None,
    ) -> list[IngestionMaterialReview]:
        q = select(IngestionMaterialReview)
        if school_id is not None:
            q = q.where(IngestionMaterialReview.school_id == school_id)
        if review_status:
            q = q.where(IngestionMaterialReview.review_status == review_status)
        return list((await self._session.execute(
            q.order_by(IngestionMaterialReview.created_at.desc())
        )).scalars().all())

    async def detail(self, review_id: UUID) -> dict[str, Any]:
        review = await self.get_review(review_id)
        doc = await self._session.get(IngestionDocument, review.ingestion_document_id)
        sections = (await self._session.execute(
            select(IngestionSection).where(IngestionSection.document_id == doc.id)
            .order_by(IngestionSection.position)
        )).scalars().all()
        questions = (await self._session.execute(
            select(IngestionQuestion).where(IngestionQuestion.document_id == doc.id)
            .order_by(IngestionQuestion.position)
        )).scalars().all()
        return {
            "review": _review_to_dict(review),
            "document": {
                "id": str(doc.id), "filename": doc.filename, "document_type": doc.document_type,
                "document_hash": doc.document_hash, "file_size_bytes": doc.file_size_bytes,
                "title": doc.title, "page_count": doc.page_count, "status": doc.status,
                "storage_uri": doc.storage_uri, "created_at": doc.created_at.isoformat(),
            },
            "sections": [
                {"id": str(s.id), "position": s.position, "section_type": s.section_type,
                 "title": s.title, "section_number": s.section_number}
                for s in sections
            ],
            "exercises": [
                {"id": str(q.id), "question_number": q.question_number, "position": q.position,
                 "statement_preview": (q.statement_text or "")[:200],
                 "has_options": bool(q.alternatives_text),
                 "requires_review": bool((q.metadata_ or {}).get("requires_review"))}
                for q in questions
            ],
        }

    # ------------------------------------------------------------------
    # STEP 7: REVIEW - a human edits the suggested classification
    # ------------------------------------------------------------------
    async def update_classification(
        self, review_id: UUID, *, discipline_code: str | None = None,
        area_code: str | None = None, content_code: str | None = None,
        subcontent_codes: list[str] | None = None, notes: str | None = None,
        reviewed_by: str | None = None,
    ) -> IngestionMaterialReview:
        review = await self.get_review(review_id)
        if review.review_status in ("PUBLISHED",):
            raise AuthorialIngestionError(
                PUBLICATION_BLOCKED, "a published material's classification is frozen "
                "(spec s14: never alter retroactively what students already saw)")
        if discipline_code is not None:
            review.discipline_code = discipline_code or None
        if area_code is not None:
            review.area_code = area_code or None
        if content_code is not None:
            review.content_code = content_code or None
            review.classification_state = "MAPPED" if content_code else "TAXONOMY_GAP"
            review.classification_source = "MANUAL"
        if subcontent_codes is not None:
            review.subcontent_codes = subcontent_codes or None
        if notes is not None:
            review.notes = notes
        if review.classification_state == "MAPPED" and not review.structure_issues:
            review.review_status = "PENDING_REVIEW"
        review.reviewed_by_external_identity = reviewed_by
        review.reviewed_at = datetime.now(timezone.utc)
        await self._session.commit()
        return review

    # ------------------------------------------------------------------
    # STEP 7b: APPROVAL
    # ------------------------------------------------------------------
    async def approve(self, review_id: UUID, *, reviewed_by: str) -> IngestionMaterialReview:
        review = await self.get_review(review_id)
        if review.review_status not in ("PENDING_REVIEW", "NEEDS_REVIEW"):
            raise AuthorialIngestionError(
                PUBLICATION_BLOCKED, f"cannot approve from status {review.review_status!r}")
        if review.classification_state != "MAPPED":
            raise AuthorialIngestionError(
                PUBLICATION_BLOCKED, "cannot approve a TAXONOMY_GAP material without a "
                "manually-confirmed content_code first (never force a classification)")
        review.review_status = "APPROVED"
        review.reviewed_by_external_identity = reviewed_by
        review.reviewed_at = datetime.now(timezone.utc)
        await self._session.commit()
        return review

    async def reject(self, review_id: UUID, *, reviewed_by: str, reason: str | None = None) -> IngestionMaterialReview:
        review = await self.get_review(review_id)
        review.review_status = "REJECTED"
        review.reviewed_by_external_identity = reviewed_by
        review.reviewed_at = datetime.now(timezone.utc)
        if reason:
            review.notes = reason
        await self._session.commit()
        return review

    # ------------------------------------------------------------------
    # STEP 8: PUBLICATION - only from APPROVED, only via TheoryMaterialService
    # ------------------------------------------------------------------
    async def publish(self, review_id: UUID, *, published_by: str) -> IngestionMaterialReview:
        review = await self.get_review(review_id)
        if review.review_status == "PUBLISHED":
            return review  # idempotent - never a second material
        if review.review_status != "APPROVED":
            raise AuthorialIngestionError(
                PUBLICATION_BLOCKED, f"cannot publish from status {review.review_status!r} "
                "(ingestion != publication - approve first)")

        doc = await self._session.get(IngestionDocument, review.ingestion_document_id)
        sections = (await self._session.execute(
            select(IngestionSection).where(IngestionSection.document_id == doc.id)
            .order_by(IngestionSection.position)
        )).scalars().all()
        if not sections:
            raise AuthorialIngestionError(
                PUBLICATION_BLOCKED, "no structure to publish (0 sections) - a material with a "
                "grave structural error must not be published (spec s15)")
        questions = (await self._session.execute(
            select(IngestionQuestion).where(IngestionQuestion.document_id == doc.id)
            .order_by(IngestionQuestion.position)
        )).scalars().all()

        content_node_id = None
        if review.content_code:
            from ..db.models import CatalogNode
            content_node_id = await self._session.scalar(
                select(CatalogNode.id).where(CatalogNode.code == review.content_code))

        visibility = "SCHOOL" if review.school_id else "PRIVATE"
        material = await self._catalog.create_material(
            self._session, title=doc.title or doc.filename,
            created_by_external_identity=doc.ingested_by_external_identity,
            primary_content_node_id=content_node_id, school_id=review.school_id,
            material_kind="CHAPTER",
            authoring_source="TEACHER" if review.origin_type == "AUTHORIAL" else review.origin_type,
            visibility_scope=visibility,
        )
        from .catalog import TheoryMaterialRepository
        version = await TheoryMaterialRepository(self._session).get_latest_version(material.id)

        # ingestion_sections.position may start at 0 (this phase's parser
        # allows a leading "Introdução" at position 0) - MaterialSection
        # requires position > 0, so re-number with a running counter while
        # keeping a stable ingestion_section.id -> material_section.id map
        # for the exercise-linking pass below.
        material_section_id: dict[UUID, UUID] = {}
        for m_position, sec in enumerate(sections, start=1):
            # DocxParser stores a chapter's real subtitle in `description`
            # (`title` is the generic "Episódio N") - prefer the descriptive
            # text when present so the Material Player shows a real heading.
            section_title = sec.description or sec.title
            m_section = await self._catalog.add_section(
                self._session, material_version_id=version.id,
                section_type=_SECTION_TYPE_MAP.get((sec.section_type or "OTHER").upper(), "SECTION"),
                position=m_position, title=section_title, body=None,
                content_node_id=content_node_id if m_position == 1 else None,
                curriculum_relation_type="THEORY" if m_position == 1 else None,
            )
            material_section_id[sec.id] = m_section.id

            block_position = 0
            for raw_line in _reload_content_lines(sec):
                block_position += 1
                if raw_line.startswith("## "):
                    await self._catalog.add_block(
                        self._session, section_id=m_section.id, block_type="HEADING",
                        position=block_position, title=raw_line[3:].strip()[:500] or None,
                    )
                else:
                    await self._catalog.add_block(
                        self._session, section_id=m_section.id, block_type="TEXT",
                        position=block_position, body=raw_line,
                    )
            # an empty section (heading detected with no body extracted) is
            # allowed to publish with zero blocks - never a fabricated one.

        for ex_position, q in enumerate(questions, start=1):
            target_section_id = material_section_id.get(q.section_id) if q.section_id else None
            authored_text = q.statement_text or ""
            if q.alternatives_text:
                authored_text += "\n\n" + q.alternatives_text
            await self._catalog.add_exercise(
                self._session, material_version_id=version.id,
                source_type="AUTHORED", position=ex_position,
                section_id=target_section_id, authored_text=authored_text[:8000],
                relation_type="EXERCISE",
                is_required=False,
                metadata={"ingestion_question_id": str(q.id), "ingestion_document_id": str(doc.id)},
            )

        await self._catalog.submit_for_review(self._session, material_version_id=version.id)
        await self._catalog.approve_version(self._session, material_version_id=version.id)
        await self._catalog.publish_version(
            self._session, material_version_id=version.id,
            owner_external_id=published_by, visibility_scope=visibility,
        )

        review.theory_material_id = material.id
        review.theory_material_version_id = version.id
        review.review_status = "PUBLISHED"
        review.reviewed_by_external_identity = published_by
        review.reviewed_at = datetime.now(timezone.utc)
        await self._session.commit()
        return review


def _reload_content_lines(section: IngestionSection) -> list[str]:
    """The full extracted body, one paragraph per block-to-be (PHASE 26's
    additive ``content_text`` column - see migration 034). Falls back to the
    500-char content_preview only for a row that somehow has no content_text
    (never fabricates anything beyond what was actually extracted)."""
    if section.content_text:
        return [ln for ln in section.content_text.split("\n") if ln.strip()]
    return [section.content_preview] if section.content_preview else []


def _review_to_dict(review: IngestionMaterialReview) -> dict[str, Any]:
    return {
        "id": str(review.id),
        "ingestion_document_id": str(review.ingestion_document_id),
        "school_id": str(review.school_id) if review.school_id else None,
        "origin_type": review.origin_type,
        "review_status": review.review_status,
        "discipline_code": review.discipline_code,
        "area_code": review.area_code,
        "content_code": review.content_code,
        "subcontent_codes": review.subcontent_codes or [],
        "classification_state": review.classification_state,
        "classification_confidence": float(review.classification_confidence) if review.classification_confidence is not None else None,
        "classification_source": review.classification_source,
        "structure_issues": review.structure_issues or [],
        "exercises_detected": review.exercises_detected,
        "theory_material_id": str(review.theory_material_id) if review.theory_material_id else None,
        "theory_material_version_id": str(review.theory_material_version_id) if review.theory_material_version_id else None,
        "notes": review.notes,
        "created_at": review.created_at.isoformat(),
        "updated_at": review.updated_at.isoformat(),
    }
