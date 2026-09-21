"""R3 - EssayCorrection: what the AI produced, what the teacher decided, and
whether the student can see it yet.

Single parent (EssaySubmission), so no composite FK - same reasoning as
PromptMaterial/EssaySubmissionPage in R2. ``school_id`` is a plain column
(never a composite FK target: EssaySubmission itself has no
UniqueConstraint(school_id, id) to compose against), always set server-side
from the submission being corrected, kept only so a "all pending corrections
at this school" query doesn't need a join.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ..types import JSONBCompatible


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EssayCorrection(Base):
    __tablename__ = "essay_corrections"
    __table_args__ = (
        UniqueConstraint(
            "essay_submission_id", name="uq_essay_corrections_submission"
        ),
        CheckConstraint(
            "status IN ('PENDING_REVIEW', 'NEEDS_REVIEW', 'APPROVED', 'REJECTED')",
            name="ck_essay_corrections_status",
        ),
        CheckConstraint(
            "(status = 'APPROVED') = (published_at IS NOT NULL)",
            name="ck_essay_corrections_approved_has_published_at",
        ),
        CheckConstraint(
            "(status IN ('APPROVED', 'REJECTED')) = (reviewed_at IS NOT NULL)",
            name="ck_essay_corrections_terminal_has_reviewed_at",
        ),
        CheckConstraint(
            "status = 'NEEDS_REVIEW' OR failure_reason IS NULL",
            name="ck_essay_corrections_failure_reason_requires_needs_review",
        ),
        CheckConstraint(
            "status = 'NEEDS_REVIEW' OR (ai_output IS NOT NULL AND ai_output != 'null')",
            name="ck_essay_corrections_non_failed_has_ai_output",
        ),
        CheckConstraint(
            "(ai_output IS NULL OR ai_output = 'null') OR (correction_key IS NOT NULL AND model_version IS NOT NULL)",
            name="ck_essay_corrections_success_has_key_and_model",
        ),
        Index("ix_essay_corrections_school_id", "school_id"),
        Index("ix_essay_corrections_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    essay_submission_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("essay_submissions.id", ondelete="RESTRICT"), nullable=False
    )
    # correction_key/model_version are nullable: both depend on a round trip
    # that actually reached a model (correction_key is computed FROM
    # model_version). A failure before any model responded - a provider
    # timeout, a rubric that failed to load - still needs a NEEDS_REVIEW row
    # to surface to a teacher, with neither value known yet. rubric_version/
    # prompt_version/engine_version stay NOT NULL: all three are static
    # config, known before the AI call is ever made, so every row - success
    # or failure - can carry them.
    correction_key: Mapped[str | None] = mapped_column(String(64))
    rubric_version: Mapped[str] = mapped_column(String(50), nullable=False)
    model_version: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(50), nullable=False)
    ai_output: Mapped[dict[str, Any] | None] = mapped_column(JSONBCompatible)
    final_scores: Mapped[dict[str, Any] | None] = mapped_column(JSONBCompatible)
    final_feedback: Mapped[dict[str, Any] | None] = mapped_column(JSONBCompatible)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING_REVIEW")
    failure_reason: Mapped[str | None] = mapped_column(Text)
    reviewed_by_external_identity: Mapped[str | None] = mapped_column(String(255))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


__all__ = ["EssayCorrection"]
