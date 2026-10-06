# tests/test_essay_quality_gate_integration.py
"""Task 8 (spec Fase B, Quality Gate): end-to-end through
EssayCorrectionService.correct() - the Quality Gate must decide BEFORE any
phase-2 pedagogical scoring runs, and an UNRELIABLE_NEEDS_REVIEW verdict must
never let a score reach final_scores, no matter what the model's own phase-1
``scores`` said.

Harness: unittest.IsolatedAsyncioTestCase with an in-memory SQLite engine -
the same pattern tests/test_r3_essay_correction_service.py already
establishes for this service (no ``seed_session``/``make_essay_submission``
pytest fixtures exist anywhere in this project). Reuses that file's own
``_happy_payload`` (phase-1 payload builder, now with an
``input_reliability`` parameter - Task 8) and ``_StubTextProvider`` (routes
phase-1/phase-2a/phase-2b calls by prompt marker) directly, instead of
inventing a parallel fake provider or payload shape - same cross-module
import precedent as tests/test_mass_correction_batch_scoring.py already
uses for ``_happy_payload``.
"""
import unittest
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear,
    Class,
    EssayPrompt,
    EssaySubmission,
    GradeLevel,
    PromptAssignment,
    School,
    Segment,
    Student,
)
from agente_ia_edu.rubrics.loader import load_rubric_file
from agente_ia_edu.services.essay_correction import EssayCorrectionService
from agente_ia_edu.services.essay_rubric_seed import EssayRubricSeeder

from test_r3_essay_correction_service import _StubTextProvider, _happy_payload


class EssayQualityGateIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        async with self.session_factory() as session:
            await EssayRubricSeeder(session).seed(load_rubric_file("enem_2025"))
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _submission(self, session, code: str, *, canonical_text: str) -> EssaySubmission:
        """Minimal TEXT_OFFSET submission - same shape as
        test_r3_essay_correction_service.EssayCorrectionServiceTests._submission,
        trimmed to what these two tests need (no IMAGE_REGION, no
        validation-policy knobs - the Quality Gate short-circuits before any
        of that policy is ever consulted)."""
        school = School(id=uuid.uuid4(), code=f"QG-{code}", name=f"school-{code}")
        session.add(school)
        await session.flush()
        segment = Segment(id=uuid.uuid4(), school_id=school.id, name="seg", external_id=f"SEG-{code}")
        session.add(segment)
        await session.flush()
        grade = GradeLevel(
            id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
            name="grade", external_id=f"GRADE-{code}",
        )
        year = AcademicYear(id=uuid.uuid4(), school_id=school.id, year=2026, external_id=f"YEAR-{code}")
        session.add_all([grade, year])
        await session.flush()
        klass = Class(
            id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
            grade_level_id=grade.id, name="turma", external_id=f"TURMA-{code}",
        )
        session.add(klass)
        student = Student(id=uuid.uuid4(), school_id=school.id, person_id=uuid.uuid4(), student_code=f"ST-{code}")
        session.add(student)
        await session.flush()
        prompt = EssayPrompt(
            id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte sobre X.",
            year=2026, status="ACTIVE", created_by_external_identity="teacher:t",
        )
        session.add(prompt)
        await session.flush()
        assignment = PromptAssignment(
            id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
            class_id=klass.id, assigned_by_external_identity="teacher:t",
            validation_enabled=True,
        )
        session.add(assignment)
        await session.flush()

        submission = EssaySubmission(
            id=uuid.uuid4(), essay_id=uuid.uuid4(), school_id=school.id,
            prompt_assignment_id=assignment.id, student_id=student.id,
            mode="TYPED", anchor_mode="TEXT_OFFSET", status="SUBMITTED",
            canonical_text=canonical_text, normalized_text_hash="a" * 64,
            submitted_at=datetime.now(timezone.utc),
        )
        session.add(submission)
        await session.flush()
        await session.commit()
        return submission

    async def test_unreliable_input_short_circuits_to_needs_review_without_a_score(self):
        async with self.session_factory() as session:
            text = "texto corrompido qualquer, irrelevante para este teste"
            submission = await self._submission(session, "1", canonical_text=text)
            payload = _happy_payload(
                anchor_mode="TEXT_OFFSET", text=text,
                per_competency_points={c: 80 for c in ("C1", "C2", "C3", "C4", "C5")},
                input_reliability={
                    "status": "UNRELIABLE_NEEDS_REVIEW",
                    "rationale": "Texto apresenta corrupcao extensa de leitura.",
                },
            )
            provider = _StubTextProvider(text=payload)
            service = EssayCorrectionService(session, text_provider=provider)

            correction = await service.correct(submission.id)

            self.assertEqual(correction.status, "NEEDS_REVIEW")
            # NUNCA publica nota como se fosse confiavel, mesmo que o
            # modelo tenha devolvido scores no payload de fase 1.
            self.assertIsNone(correction.final_scores)
            self.assertEqual(correction.quality_gate_status, "UNRELIABLE_NEEDS_REVIEW")
            self.assertIn("QUALITY_GATE_UNRELIABLE", correction.failure_reason or "")
            # Phase 2 must never have been reached: the stub only answers
            # SCORING_RULES:/ALERTS_TO_REVIEW: prompts after phase 1, so a
            # call_count of 1 proves no phase-2 call happened.
            self.assertEqual(provider.call_count, 1)

    async def test_reliable_input_proceeds_to_normal_scoring(self):
        async with self.session_factory() as session:
            text = "redacao normal e legivel sobre o tema proposto"
            submission = await self._submission(session, "2", canonical_text=text)
            payload = _happy_payload(
                anchor_mode="TEXT_OFFSET", text=text,
                per_competency_points={c: 120 for c in ("C1", "C2", "C3", "C4", "C5")},
                input_reliability={
                    "status": "RELIABLE", "rationale": "Texto legivel sem ressalvas.",
                },
            )
            provider = _StubTextProvider(text=payload)
            service = EssayCorrectionService(session, text_provider=provider)

            correction = await service.correct(submission.id)

            self.assertEqual(correction.quality_gate_status, "RELIABLE")
            self.assertIsNotNone(correction.final_scores)


if __name__ == "__main__":
    unittest.main()
