"""Aggregates a student's approved essay corrections into evolution data:
one entry per APPROVED correction (most-recent first), plus the total-score
delta between the newest and oldest scored entry. Shared by the student and
teacher evolution routes (api/routes/essay_submissions.py and
api/routes/essay_corrections.py) - same shape both sides return, so neither
route repeats the aggregation query or the delta rule.

A FORMATIVO-mode correction (institution configured with no grading, spec
essay_engine_contract/v1.py) has final_scores=None - such an entry keeps
total/per_competency as None rather than defaulting to 0, so the frontend
can skip that point on every line chart instead of plotting a misleading
zero (devolutiva-rica-redacao spec's edge case §3.3, dashboard spec §3.3).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import EssayCorrection, EssayPrompt, EssaySubmission, PromptAssignment
from ..essay_engine_contract.v1 import COMPETENCY_CODES


class EssayEvolutionEntry(BaseModel):
    essay_submission_id: uuid.UUID
    prompt_title: str
    published_at: datetime
    total: Optional[int] = None
    per_competency: Optional[dict[str, int]] = None


class EssayEvolutionResponse(BaseModel):
    entries: list[EssayEvolutionEntry]
    total_delta: Optional[int] = None


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


async def build_evolution(
    session: AsyncSession, *, school_id: uuid.UUID, student_id: uuid.UUID,
) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(EssayCorrection, EssayPrompt.title)
            .join(EssaySubmission, EssaySubmission.id == EssayCorrection.essay_submission_id)
            .join(PromptAssignment, PromptAssignment.id == EssaySubmission.prompt_assignment_id)
            .join(EssayPrompt, EssayPrompt.id == PromptAssignment.essay_prompt_id)
            .where(
                EssaySubmission.school_id == school_id,
                EssaySubmission.student_id == student_id,
                EssayCorrection.status == "APPROVED",
            )
            .order_by(EssayCorrection.published_at.desc())
        )
    ).all()

    entries: list[dict[str, Any]] = []
    for correction, prompt_title in rows:
        scores = _as_dict(correction.final_scores)
        per_competency_raw = _as_dict(scores.get("per_competency"))
        per_competency = (
            {code: _as_dict(per_competency_raw.get(code)).get("points", 0) for code in COMPETENCY_CODES}
            if per_competency_raw
            else None
        )
        entries.append({
            "essay_submission_id": correction.essay_submission_id,
            "prompt_title": prompt_title,
            "published_at": correction.published_at,
            "total": scores.get("total"),
            "per_competency": per_competency,
        })

    # entries is newest-first (query order); the delta compares the oldest
    # SCORED entry to the newest SCORED entry, skipping any FORMATIVO gaps
    # in between - reversed() walks it chronologically without a second query.
    scored_chronological = [e["total"] for e in reversed(entries) if e["total"] is not None]
    total_delta = (
        scored_chronological[-1] - scored_chronological[0]
        if len(scored_chronological) >= 2
        else None
    )

    return {"entries": entries, "total_delta": total_delta}


__all__ = ["EssayEvolutionEntry", "EssayEvolutionResponse", "build_evolution"]
