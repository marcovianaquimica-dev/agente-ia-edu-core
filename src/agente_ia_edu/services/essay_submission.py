"""R2 - EssaySubmission/EssaySubmissionPage: the "enviar redação" side of R2
(spec §5). One class, built up across this plan's Tasks 7-10:

  Task 7:  TYPED (§5.1) + resubmission plumbing shared by every mode (§5.4)
  Task 8:  PHOTO/PDF upload + synchronous OCR trigger (§5.2 steps 1-2, §5.3)
  Task 9:  page listing + review (§5.2 step 3)
  Task 10: confirm (§5.2 step 4, §5.3's direct-to-SUBMITTED path)

Authorization (role, module gate, enrollment, prompt_assignment ownership)
lives in the route layer (Task 11) - this service only enforces rules the
database can't express as a CHECK constraint.
"""

from __future__ import annotations

import mimetypes
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import EssaySubmission, EssaySubmissionPage
from ..providers.contracts import EssayTranscriptionProvider
from ..providers.factory import build_essay_transcriber
from ..providers.models import EssayPageTranscriptionRequest
from .essay_correction_key import essay_text_hash, normalize_essay_text
from .material_storage import MaterialStorage


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EssayResubmissionBlockedError(RuntimeError):
    """AVALIATIVO: a SUBMITTED essay is definitive and cannot be resubmitted
    (spec §5.4). Mapped to 409 in the route, not 422 - this is a conflict
    with existing state, not a malformed request."""


class EssaySubmissionService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        storage: MaterialStorage | None = None,
        transcriber: EssayTranscriptionProvider | None = None,
    ) -> None:
        self.session = session
        self._storage = storage or MaterialStorage()
        # Lazily built (Task 8) so TYPED submissions and schools with
        # transcription disabled never require OPENAI_API_KEY to be set.
        self._transcriber = transcriber

    async def start_typed_submission(
        self,
        *,
        school_id: uuid.UUID,
        prompt_assignment_id: uuid.UUID,
        student_id: uuid.UUID,
        text: str,
        essay_id: uuid.UUID | None = None,
        correction_mode: str | None = None,
    ) -> EssaySubmission:
        if essay_id is not None:
            await self._supersede_previous(essay_id, correction_mode=correction_mode or "FORMATIVO")

        submission = EssaySubmission(
            id=uuid.uuid4(),
            essay_id=essay_id or uuid.uuid4(),
            school_id=school_id,
            prompt_assignment_id=prompt_assignment_id,
            student_id=student_id,
            mode="TYPED",
            anchor_mode="TEXT_OFFSET",
            status="SUBMITTED",
            canonical_text=normalize_essay_text(text),
            normalized_text_hash=essay_text_hash(text),
            submitted_at=_utcnow(),
        )
        self.session.add(submission)
        await self.session.flush()
        return submission

    async def _supersede_previous(
        self, essay_id: uuid.UUID, *, correction_mode: str
    ) -> EssaySubmission:
        previous = await self.session.scalar(
            select(EssaySubmission).where(
                EssaySubmission.essay_id == essay_id, EssaySubmission.status == "SUBMITTED"
            )
        )
        if previous is None:
            raise ValueError(f"No SUBMITTED version exists for essay_id={essay_id} to resubmit")
        if correction_mode == "AVALIATIVO":
            raise EssayResubmissionBlockedError(
                f"AVALIATIVO: essay_id={essay_id} is already SUBMITTED and cannot be resubmitted"
            )
        previous.status = "SUPERSEDED"
        await self.session.flush()
        return previous

    async def start_photo_submission(
        self,
        *,
        school_id: uuid.UUID,
        prompt_assignment_id: uuid.UUID,
        student_id: uuid.UUID,
        mode: str,
        transcription_enabled: bool,
        essay_id: uuid.UUID | None = None,
        correction_mode: str | None = None,
    ) -> EssaySubmission:
        if mode not in ("PHOTO", "PDF"):
            raise ValueError(f"start_photo_submission requires mode PHOTO or PDF, got {mode!r}")
        if essay_id is not None:
            await self._supersede_previous(essay_id, correction_mode=correction_mode or "FORMATIVO")

        submission = EssaySubmission(
            id=uuid.uuid4(),
            essay_id=essay_id or uuid.uuid4(),
            school_id=school_id,
            prompt_assignment_id=prompt_assignment_id,
            student_id=student_id,
            mode=mode,
            anchor_mode="TEXT_OFFSET" if transcription_enabled else "IMAGE_REGION",
            status="PENDING_TRANSCRIPTION",
        )
        self.session.add(submission)
        await self.session.flush()
        return submission

    async def upload_page(
        self,
        *,
        essay_submission_id: uuid.UUID,
        page_number: int,
        source_path: Path,
        transcription_enabled: bool,
    ) -> EssaySubmissionPage:
        dest, _digest = self._storage.store(source_path)

        existing = await self.session.scalar(
            select(EssaySubmissionPage).where(
                EssaySubmissionPage.essay_submission_id == essay_submission_id,
                EssaySubmissionPage.page_number == page_number,
            )
        )
        if existing is not None:
            existing.storage_uri = str(dest)
            existing.ocr_tokens = None
            existing.reviewed_text = None
            page = existing
        else:
            page = EssaySubmissionPage(
                id=uuid.uuid4(),
                essay_submission_id=essay_submission_id,
                page_number=page_number,
                storage_uri=str(dest),
            )
            self.session.add(page)
        await self.session.flush()

        if transcription_enabled:
            await self._ocr_page(page, dest)
            submission = await self.session.get(EssaySubmission, essay_submission_id)
            submission.status = "PENDING_CONFIRMATION"
            await self.session.flush()
        return page

    async def upload_document(
        self,
        *,
        essay_submission_id: uuid.UUID,
        source_path: Path,
        transcription_enabled: bool,
    ) -> list[EssaySubmissionPage]:
        """PDF mode: split ``source_path`` into one page-image per PDF page
        and upload each through the same path :meth:`upload_page` uses."""
        page_image_paths = self._split_pdf_pages(source_path)
        pages = []
        for index, image_path in enumerate(page_image_paths, start=1):
            page = await self.upload_page(
                essay_submission_id=essay_submission_id, page_number=index,
                source_path=image_path, transcription_enabled=transcription_enabled,
            )
            pages.append(page)
        return pages

    async def _ocr_page(self, page: EssaySubmissionPage, image_path: Path) -> None:
        result = await self._get_transcriber().transcribe_page(
            EssayPageTranscriptionRequest(image_path=image_path, mime_type=_guess_mime(image_path))
        )
        page.ocr_tokens = [
            {"text": t.text, "confidence": t.confidence, "start": t.start, "end": t.end}
            for t in result.tokens
        ]

    def _get_transcriber(self) -> EssayTranscriptionProvider:
        if self._transcriber is None:
            self._transcriber = build_essay_transcriber()
        return self._transcriber

    @staticmethod
    def _split_pdf_pages(pdf_path: Path) -> list[Path]:
        try:
            import pymupdf as _mu
        except ImportError:
            import fitz as _mu  # type: ignore

        dest_dir = pdf_path.parent / f"{pdf_path.stem}_pages"
        dest_dir.mkdir(exist_ok=True)
        doc = _mu.open(str(pdf_path))
        try:
            paths = []
            for index in range(len(doc)):
                pix = doc[index].get_pixmap(dpi=200)
                page_path = dest_dir / f"page_{index + 1}.png"
                pix.save(str(page_path))
                paths.append(page_path)
            return paths
        finally:
            doc.close()

    async def list_pages(self, essay_submission_id: uuid.UUID) -> list[EssaySubmissionPage]:
        result = await self.session.execute(
            select(EssaySubmissionPage)
            .where(EssaySubmissionPage.essay_submission_id == essay_submission_id)
            .order_by(EssaySubmissionPage.page_number)
        )
        return list(result.scalars().all())

    async def review_page(
        self, *, essay_submission_id: uuid.UUID, page_number: int, reviewed_text: str
    ) -> EssaySubmissionPage:
        page = await self.session.scalar(
            select(EssaySubmissionPage).where(
                EssaySubmissionPage.essay_submission_id == essay_submission_id,
                EssaySubmissionPage.page_number == page_number,
            )
        )
        if page is None:
            raise ValueError(
                f"No page {page_number} on submission {essay_submission_id}"
            )
        page.reviewed_text = reviewed_text
        await self.session.flush()
        return page

    async def confirm_submission(self, essay_submission_id: uuid.UUID) -> EssaySubmission:
        submission = await self.session.get(EssaySubmission, essay_submission_id)
        if submission is None:
            raise ValueError(f"EssaySubmission not found: {essay_submission_id}")

        pages = await self.list_pages(essay_submission_id)
        if not pages:
            raise ValueError(f"EssaySubmission {essay_submission_id} has no pages to confirm")

        if submission.anchor_mode == "TEXT_OFFSET":
            missing = [p.page_number for p in pages if p.reviewed_text is None]
            if missing:
                raise ValueError(
                    f"Pages not yet reviewed: {missing} - every page needs reviewed_text "
                    "before a transcribed submission can be confirmed"
                )
            full_text = "\n\n".join(p.reviewed_text for p in pages)
            submission.canonical_text = normalize_essay_text(full_text)
            submission.normalized_text_hash = essay_text_hash(full_text)
        # anchor_mode == "IMAGE_REGION": no transcription ran, canonical_text/
        # normalized_text_hash stay NULL - spec §5.3's documented consequence.

        submission.status = "SUBMITTED"
        submission.submitted_at = _utcnow()
        await self.session.flush()
        return submission


def _guess_mime(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


__all__ = ["EssayResubmissionBlockedError", "EssaySubmissionService"]
