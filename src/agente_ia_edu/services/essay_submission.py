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

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import EssaySubmission
from ..providers.contracts import EssayTranscriptionProvider
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


__all__ = ["EssayResubmissionBlockedError", "EssaySubmissionService"]
