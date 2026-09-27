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

import asyncio
import logging
import mimetypes
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import EssaySubmission, EssaySubmissionPage
from ..providers.contracts import EssayTranscriptionProvider
from ..providers.errors import ProviderError
from ..providers.factory import build_essay_transcriber
from ..providers.models import EssayPageTranscriptionRequest
from .essay_correction_key import essay_text_hash, normalize_essay_text
from .material_storage import MaterialStorage

logger = logging.getLogger(__name__)


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
        previous = None
        if essay_id is not None:
            # TYPED submits atomically (the new row is SUBMITTED in this
            # same call), so unlike start_photo_submission it's safe to
            # supersede the old row right here rather than deferring to
            # confirm_submission - there's no in-between state where neither
            # row is SUBMITTED.
            previous = await self._validate_resubmission(
                essay_id, correction_mode=correction_mode or "FORMATIVO"
            )

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
        if previous is not None:
            previous.status = "SUPERSEDED"
        await self.session.flush()
        return submission

    async def _validate_resubmission(
        self, essay_id: uuid.UUID, *, correction_mode: str
    ) -> EssaySubmission:
        """Confirms essay_id is eligible for a new version, WITHOUT
        mutating anything - actually superseding the previous row is the
        caller's job, at whatever point the new version is guaranteed to
        replace it (start_typed_submission does it immediately since TYPED
        submits atomically; start_photo_submission defers it to
        confirm_submission, since photo/PDF submissions can be abandoned
        mid-upload)."""
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
            # Validate eligibility only - do NOT supersede the previous
            # version yet. That happens in confirm_submission, once this new
            # version is actually about to become SUBMITTED. Superseding here
            # would leave the essay with zero SUBMITTED versions if the
            # student never finishes uploading/reviewing (spec §5.4).
            await self._validate_resubmission(essay_id, correction_mode=correction_mode or "FORMATIVO")

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

    _UPLOADABLE_STATUSES = ("PENDING_TRANSCRIPTION", "PENDING_CONFIRMATION")

    # Below this, a PDF page's extracted text layer is treated as too sparse
    # to trust on its own (e.g. a mostly-blank page, or a scanned PDF whose
    # "text layer" is just a stray OCR artifact from the scanner) and the
    # page still goes through vision transcription instead.
    _MIN_EXTRACTED_TEXT_CHARS = 30

    async def upload_page(
        self,
        *,
        essay_submission_id: uuid.UUID,
        page_number: int,
        source_path: Path,
        extracted_text: str | None = None,
    ) -> EssaySubmissionPage:
        submission = await self.session.get(EssaySubmission, essay_submission_id)
        if submission is None:
            raise ValueError(f"EssaySubmission not found: {essay_submission_id}")
        if submission.status not in self._UPLOADABLE_STATUSES:
            raise ValueError(
                f"EssaySubmission {essay_submission_id} is {submission.status} - pages can "
                "only be uploaded while a submission is pending confirmation (spec §5.2 step 3)."
            )
        # Derived from the submission's own frozen anchor_mode, never from a
        # live settings read - a school toggling transcription_enabled mid-
        # flow must not change how an in-progress submission behaves.
        transcription_enabled = submission.anchor_mode == "TEXT_OFFSET"

        dest, _digest = self._storage.store(source_path)
        # Offloaded like _split_pdf_pages below - image decoding is CPU-bound.
        width, height = await asyncio.to_thread(self._measure_page_image, dest)

        existing = await self.session.scalar(
            select(EssaySubmissionPage).where(
                EssaySubmissionPage.essay_submission_id == essay_submission_id,
                EssaySubmissionPage.page_number == page_number,
            )
        )
        if existing is not None:
            existing.storage_uri = str(dest)
            existing.width = width
            existing.height = height
            existing.ocr_tokens = None
            existing.reviewed_text = None
            page = existing
        else:
            page = EssaySubmissionPage(
                id=uuid.uuid4(),
                essay_submission_id=essay_submission_id,
                page_number=page_number,
                storage_uri=str(dest),
                width=width,
                height=height,
            )
            self.session.add(page)
        await self.session.flush()

        if transcription_enabled:
            if extracted_text is not None and len(extracted_text.strip()) >= self._MIN_EXTRACTED_TEXT_CHARS:
                # A digitally-typed PDF already has an exact text layer -
                # confirmed live (2026-09-25), running it through vision OCR
                # anyway produced spurious low-confidence flags on perfectly
                # typed words (there is nothing uncertain to read) and cost a
                # needless round trip to the vision model. Use the extracted
                # text directly, at full confidence, the same shape
                # _tokens_from_logprobs falls back to when a provider has no
                # per-token signal of its own.
                page.ocr_tokens = [{
                    "text": extracted_text, "confidence": 1.0,
                    "start": 0, "end": len(extracted_text),
                }]
            else:
                # Explicit product decision (2026-09-25): always transcribe,
                # regardless of image quality - never silently fall back to
                # IMAGE_REGION (that mode's own anchor precision is worse, and
                # switching modes mid-flow surprised the student). A refusal or
                # any other transcription failure must surface as a clear error
                # (ProviderError propagates to the route's 502 handler below) so
                # the student retries with a better photo - it must not
                # silently downgrade to a less precise correction.
                await self._ocr_page(page, dest)
            submission.status = "PENDING_CONFIRMATION"
            await self.session.flush()
        return page

    async def upload_document(
        self,
        *,
        essay_submission_id: uuid.UUID,
        source_path: Path,
    ) -> list[EssaySubmissionPage]:
        """PDF mode: split ``source_path`` into one page-image per PDF page
        (off the event loop - rasterization is CPU-bound) and upload each
        through the same path :meth:`upload_page` uses. Also extracts each
        page's embedded text layer, when it has one, so upload_page can skip
        vision OCR entirely for a digitally-typed PDF."""
        split_pages = await asyncio.to_thread(self._split_pdf_pages, source_path)
        pages = []
        for index, (image_path, extracted_text) in enumerate(split_pages, start=1):
            page = await self.upload_page(
                essay_submission_id=essay_submission_id, page_number=index,
                source_path=image_path, extracted_text=extracted_text,
            )
            pages.append(page)
        return pages

    # Confirmed live (2026-09-25): a transcription refusal/failure is often
    # transient - the same photo frequently succeeds on a second or third
    # attempt with no change at all. Retrying here, inside one upload call,
    # spares the student from re-clicking upload themselves.
    _OCR_ATTEMPTS = 3

    # Confirmed live (2026-09-26): the SAME photo that transcribed at ~99%
    # average confidence in one call came back at 0.427 average confidence
    # (159 of 243 tokens below 60%) in another, unrelated call - a vision
    # model can return a well-formed, non-refusing response that is mostly
    # confabulated rather than actually read. A refusal check never catches
    # this: the call succeeds, the JSON is fine, the words are just wrong.
    # Average confidence is the only signal already available that flags it,
    # so a page-average below this floor is treated like a failed attempt
    # and retried, instead of being accepted and handed to the student as a
    # near-unreadable wall of red text.
    _MIN_AVERAGE_CONFIDENCE = 0.6

    async def _ocr_page(self, page: EssaySubmissionPage, image_path: Path) -> None:
        last_error: ProviderError | None = None
        best_tokens: tuple | None = None
        best_average_confidence = -1.0
        for attempt in range(1, self._OCR_ATTEMPTS + 1):
            try:
                result = await self._get_transcriber().transcribe_page(
                    EssayPageTranscriptionRequest(
                        image_path=image_path, mime_type=_guess_mime(image_path)
                    )
                )
                tokens = result.tokens
                average_confidence = (
                    sum(t.confidence for t in tokens) / len(tokens) if tokens else 0.0
                )
                if average_confidence > best_average_confidence:
                    best_tokens = tokens
                    best_average_confidence = average_confidence
                if average_confidence >= self._MIN_AVERAGE_CONFIDENCE:
                    page.ocr_tokens = [
                        {"text": t.text, "confidence": t.confidence, "start": t.start, "end": t.end}
                        for t in tokens
                    ]
                    return
                logger.warning(
                    "transcription attempt %d/%d for page %s scored low average "
                    "confidence %.3f, retrying",
                    attempt, self._OCR_ATTEMPTS, page.id, average_confidence,
                )
            except ProviderError as exc:
                last_error = exc
                logger.warning(
                    "transcription attempt %d/%d failed for page %s: %s",
                    attempt, self._OCR_ATTEMPTS, page.id, exc,
                )
        # Every attempt scored below the floor (or errored): the explicit
        # product decision is to always transcribe regardless of quality, so
        # a low-confidence result still beats no result at all - use
        # whichever attempt scored best rather than whichever ran last.
        if best_tokens is not None:
            page.ocr_tokens = [
                {"text": t.text, "confidence": t.confidence, "start": t.start, "end": t.end}
                for t in best_tokens
            ]
            return
        raise last_error

    def _get_transcriber(self) -> EssayTranscriptionProvider:
        if self._transcriber is None:
            self._transcriber = build_essay_transcriber()
        return self._transcriber

    _MAX_PDF_PAGES = 20

    @classmethod
    def _split_pdf_pages(cls, pdf_path: Path) -> list[tuple[Path, str | None]]:
        try:
            import pymupdf as _mu
        except ImportError:
            import fitz as _mu  # type: ignore

        dest_dir = pdf_path.parent / f"{pdf_path.stem}_pages"
        dest_dir.mkdir(exist_ok=True)
        doc = _mu.open(str(pdf_path))
        try:
            if len(doc) > cls._MAX_PDF_PAGES:
                raise ValueError(
                    f"PDF has {len(doc)} pages, more than the {cls._MAX_PDF_PAGES}-page limit"
                )
            results: list[tuple[Path, str | None]] = []
            for index in range(len(doc)):
                page = doc[index]
                pix = page.get_pixmap(dpi=200)
                page_path = dest_dir / f"page_{index + 1}.png"
                pix.save(str(page_path))
                # A digitally-typed PDF has an exact embedded text layer;
                # get_text() returns "" (never raises) for a purely scanned
                # page with no such layer, so this is a safe, cheap probe -
                # upload_page decides whether the result is substantial
                # enough to trust over running vision OCR on the rasterized
                # image above.
                extracted_text = page.get_text().strip() or None
                results.append((page_path, extracted_text))
            return results
        finally:
            doc.close()

    @staticmethod
    def _measure_page_image(image_path: Path) -> tuple[float, float]:
        """Pixel dimensions of an already-stored page image, via pymupdf -
        never Pillow (see this plan's Global Constraints). Needed so R3 can
        validate IMAGE_REGION annotations against real page bounds instead
        of leaving width/height permanently NULL, as R2 did."""
        try:
            import pymupdf as _mu
        except ImportError:
            import fitz as _mu  # type: ignore

        try:
            pix = _mu.Pixmap(str(image_path))
        except Exception as exc:
            # pymupdf raises its own exception hierarchy (e.g.
            # pymupdf.mupdf.FzErrorFormat), not ValueError, for an
            # undecodable image - normalize it so callers that only catch
            # ValueError (the route layer) still get a handled 422 instead
            # of an unhandled 500.
            raise ValueError(f"{image_path.name} is not a readable image file") from exc
        return float(pix.width), float(pix.height)

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
        submission = await self.session.get(EssaySubmission, essay_submission_id)
        if submission is None:
            raise ValueError(f"EssaySubmission not found: {essay_submission_id}")
        if submission.status not in self._UPLOADABLE_STATUSES:
            raise ValueError(
                f"EssaySubmission {essay_submission_id} is {submission.status} - pages can "
                "only be reviewed while a submission is pending confirmation (spec §5.2 step 3)."
            )

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
        if submission.status not in self._UPLOADABLE_STATUSES:
            raise ValueError(
                f"EssaySubmission {essay_submission_id} is {submission.status}, not pending "
                "confirmation - a SUBMITTED or SUPERSEDED essay cannot be confirmed again."
            )

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

        # Resubmission (photo/PDF only - TYPED supersedes immediately in
        # start_typed_submission since it submits atomically): supersede the
        # previous SUBMITTED version of this essay_id now, in the same flush
        # as this one becoming SUBMITTED. start_photo_submission validated
        # eligibility but deliberately never superseded, so a resubmission
        # the student never finishes never leaves the essay with zero
        # SUBMITTED versions (spec §5.4).
        previous = await self.session.scalar(
            select(EssaySubmission).where(
                EssaySubmission.essay_id == submission.essay_id,
                EssaySubmission.status == "SUBMITTED",
                EssaySubmission.id != submission.id,
            )
        )
        if previous is not None:
            previous.status = "SUPERSEDED"

        submission.status = "SUBMITTED"
        submission.submitted_at = _utcnow()
        await self.session.flush()
        return submission


def _guess_mime(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


__all__ = ["EssayResubmissionBlockedError", "EssaySubmissionService"]
