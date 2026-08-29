"""
Shared answer-key resolution logic.

Single source of truth for "what is the official correct option for a given
question version", reused by both the assessment engine and the practice
(learning path) correction flow. Keeps correction rules in one place.
"""

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AnswerKeyEntry, AnswerKeyRevision, BookletQuestion


async def resolve_official_correct_option_id(
    session: AsyncSession,
    question_version_id: uuid.UUID,
) -> Optional[uuid.UUID]:
    """
    Resolve the official correct QuestionOption.id for a question version.

    Uses the latest official AnswerKeyRevision associated with any booklet
    occurrence of the question version, matching the same rule used by the
    assessment engine (AssessmentAnswerRepository.correct_objective).

    Returns None when no official answer key is available for this question
    version (e.g. not yet linked to a booklet, or no official revision).
    """
    stmt = (
        select(AnswerKeyEntry)
        .join(AnswerKeyEntry.booklet_question)
        .join(
            AnswerKeyRevision,
            AnswerKeyEntry.answer_key_revision_id == AnswerKeyRevision.id,
        )
        .where(
            BookletQuestion.question_version_id == question_version_id,
            AnswerKeyRevision.is_official.is_(True),
        )
        .order_by(AnswerKeyRevision.revision_number.desc())
        .limit(1)
    )
    entry = await session.scalar(stmt)
    if entry is None:
        return None
    return entry.resolved_option_id
