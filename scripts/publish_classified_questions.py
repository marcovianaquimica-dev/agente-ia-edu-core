"""Publish the already-classified official questions so the diagnostic and
practice selection engines can actually pick them.

QuestionSelectionRepository.list_eligible_candidate_versions (the shared
selection path for the initial diagnostic and adaptive practice) requires,
for every official_original QuestionVersion it considers:

    1. Question.status == "PUBLISHED"
    2. Question.validation_status in ("valid", "acceptable", "approved")
    3. QuestionVersion.recommended_difficulty IS NOT NULL
    4. Question.visibility_scope == "PUBLIC" (or "SCHOOL" matching the
       requester's own school_id)

None of the 479 official questions in this database satisfy any of these -
"validation_status" was populated as "validated" by the ingestion pipeline
(meaning "the extracted text/answer-key is correct"), which is a different,
earlier-stage marker than the "valid/acceptable/approved" pedagogical
eligibility QuestionGovernanceService documents and expects further
curation to set - and recommended_difficulty was never estimated for any
of them.

Rather than invent governance data for the whole 479-question corpus, this
script narrows to the questions that HAVE already been through real
curriculum classification (PedagogicalClassification, status=CLASSIFIED,
lifecycle=ACTIVE, taxonomy_version=curriculum-v2 - the same set
scripts/seed_demo_data.py links via ContentQuestionLink) and:

  - sets QuestionVersion.recommended_difficulty to "MEDIUM" where null -
    the PedagogicalClassifier's own documented default
    (services/pedagogical_classifier.py: default_difficulty="MEDIUM",
    default_difficulty_confidence=0.72) when no better estimate exists,
    not an invented value;
  - sets Question.validation_status to "valid";
  - sets Question.status to "PUBLISHED" if not already;
  - sets Question.visibility_scope to "PUBLIC" if not already, for questions
    with origin_type="IMPORTED" - all 332 imported official questions
    default to "PRIVATE", which the selection query's visibility OR-clause
    never matches (only PUBLIC, or SCHOOL scoped to the requester's own
    school). Official exam questions aren't tied to any one school, so
    PUBLIC is the scope this same query already treats as eligible for any
    requester, not an invented one.

The remaining ~435 official questions that have not been classified are
left untouched - they stay correctly gated out until someone actually
reviews them.

Safe to re-run: only touches rows that still need a change.

Usage:
    source .env
    export DATABASE_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:5433/${POSTGRES_DB}"
    .venv/bin/python scripts/publish_classified_questions.py
"""

from __future__ import annotations

import asyncio
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agente_ia_edu.db.models import PedagogicalClassification, Question, QuestionVersion


async def main() -> None:
    database_url = os.environ["DATABASE_URL"]
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        classified_version_ids = (await session.execute(
            select(PedagogicalClassification.question_version_id).where(
                PedagogicalClassification.status == "CLASSIFIED",
                PedagogicalClassification.lifecycle == "ACTIVE",
                PedagogicalClassification.metadata_["taxonomy_version"].as_string() == "curriculum-v2",
            )
        )).scalars().all()

        versions = (await session.scalars(
            select(QuestionVersion).where(
                QuestionVersion.id.in_(classified_version_ids),
                QuestionVersion.version_kind == "official_original",
            )
        )).all()

        difficulty_set = 0
        for version in versions:
            if version.recommended_difficulty is None:
                version.recommended_difficulty = "MEDIUM"
                difficulty_set += 1

        question_ids = {v.question_id for v in versions}
        questions = (await session.scalars(select(Question).where(Question.id.in_(question_ids)))).all()

        validation_set = 0
        published_set = 0
        visibility_set = 0
        for question in questions:
            if question.validation_status != "valid":
                question.validation_status = "valid"
                validation_set += 1
            if question.status != "PUBLISHED":
                question.status = "PUBLISHED"
                published_set += 1
            # All 332 IMPORTED (official ENEM) questions default to PRIVATE,
            # which the selection query's visibility OR-clause never matches
            # (it only accepts PUBLIC, or SCHOOL scoped to the requester's
            # own school_id). Official exam questions aren't tied to any one
            # school, so PUBLIC is the correct scope, not an invented one -
            # it's the only scope this same query already treats as always
            # eligible regardless of requester.
            if question.origin_type == "IMPORTED" and question.visibility_scope != "PUBLIC":
                question.visibility_scope = "PUBLIC"
                visibility_set += 1

        await session.commit()
        print(
            f"[publish-classified-questions] {len(versions)} classified question version(s) in scope: "
            f"difficulty set on {difficulty_set}, validation_status set on {validation_set}, "
            f"published {published_set}, visibility_scope set to PUBLIC on {visibility_set}"
        )

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
