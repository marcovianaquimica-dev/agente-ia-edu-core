import unittest
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear,
    Class,
    EssayCorrection,
    EssayPrompt,
    EssaySubmission,
    GradeLevel,
    PromptAssignment,
    School,
    Segment,
    Student,
)


class EssayCorrectionModelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _submission(self, session, code):
        school = School(id=uuid.uuid4(), code=f"EC-{code}", name=f"school-{code}")
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
            id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
            year=2026, status="ACTIVE", created_by_external_identity="teacher:t",
        )
        session.add(prompt)
        await session.flush()
        assignment = PromptAssignment(
            id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
            class_id=klass.id, assigned_by_external_identity="teacher:t",
        )
        session.add(assignment)
        await session.flush()
        submission = EssaySubmission(
            id=uuid.uuid4(), essay_id=uuid.uuid4(), school_id=school.id,
            prompt_assignment_id=assignment.id, student_id=student.id,
            mode="TYPED", anchor_mode="TEXT_OFFSET", status="SUBMITTED",
            canonical_text="Redacao.", normalized_text_hash="a" * 64,
            submitted_at=datetime.now(timezone.utc),
        )
        session.add(submission)
        await session.commit()
        return submission

    async def test_pending_review_round_trips(self):
        async with self.session_factory() as session:
            submission = await self._submission(session, "1")
            correction = EssayCorrection(
                id=uuid.uuid4(), school_id=submission.school_id,
                essay_submission_id=submission.id, correction_key="k" * 64,
                rubric_version="ENEM_2025", model_version="gpt-4o-mini",
                prompt_version="essay_correction_v1", engine_version="r3_correction_engine_v1",
                ai_output={"scores": None}, status="PENDING_REVIEW",
            )
            session.add(correction)
            await session.commit()

            fetched = await session.get(EssayCorrection, correction.id)
            self.assertEqual(fetched.status, "PENDING_REVIEW")
            self.assertIsNone(fetched.published_at)
            self.assertIsNone(fetched.reviewed_at)

    async def test_approved_requires_published_at(self):
        async with self.session_factory() as session:
            submission = await self._submission(session, "2")
            correction = EssayCorrection(
                id=uuid.uuid4(), school_id=submission.school_id,
                essay_submission_id=submission.id, correction_key="k" * 64,
                rubric_version="ENEM_2025", model_version="gpt-4o-mini",
                prompt_version="essay_correction_v1", engine_version="r3_correction_engine_v1",
                ai_output={"scores": None}, status="APPROVED",
                reviewed_at=datetime.now(timezone.utc),
                # published_at deliberately omitted - must violate the CHECK
            )
            session.add(correction)
            with self.assertRaises(Exception):
                await session.flush()

    async def test_needs_review_requires_failure_reason_absent_elsewhere(self):
        async with self.session_factory() as session:
            submission = await self._submission(session, "3")
            correction = EssayCorrection(
                id=uuid.uuid4(), school_id=submission.school_id,
                essay_submission_id=submission.id, correction_key="k" * 64,
                rubric_version="ENEM_2025", model_version="gpt-4o-mini",
                prompt_version="essay_correction_v1", engine_version="r3_correction_engine_v1",
                ai_output=None, status="NEEDS_REVIEW", failure_reason="provider timeout",
            )
            session.add(correction)
            await session.flush()
            self.assertEqual(correction.status, "NEEDS_REVIEW")

    async def test_needs_review_from_a_failure_before_any_model_responded(self):
        """A provider timeout or rubric-load failure happens before
        correction_key/model_version can be computed - both must be
        nullable, and NEEDS_REVIEW with neither set must still be valid."""
        async with self.session_factory() as session:
            submission = await self._submission(session, "3b")
            correction = EssayCorrection(
                id=uuid.uuid4(), school_id=submission.school_id,
                essay_submission_id=submission.id, correction_key=None,
                rubric_version="ENEM_2025", model_version=None,
                prompt_version="essay_correction_v1", engine_version="r3_correction_engine_v1",
                ai_output=None, status="NEEDS_REVIEW", failure_reason="provider timeout",
            )
            session.add(correction)
            await session.flush()
            self.assertIsNone(correction.correction_key)
            self.assertIsNone(correction.model_version)

    async def test_one_correction_per_submission(self):
        async with self.session_factory() as session:
            submission = await self._submission(session, "4")
            first = EssayCorrection(
                id=uuid.uuid4(), school_id=submission.school_id,
                essay_submission_id=submission.id, correction_key="k" * 64,
                rubric_version="ENEM_2025", model_version="gpt-4o-mini",
                prompt_version="essay_correction_v1", engine_version="r3_correction_engine_v1",
                ai_output={"scores": None}, status="PENDING_REVIEW",
            )
            session.add(first)
            await session.commit()

            second = EssayCorrection(
                id=uuid.uuid4(), school_id=submission.school_id,
                essay_submission_id=submission.id, correction_key="k" * 64,
                rubric_version="ENEM_2025", model_version="gpt-4o-mini",
                prompt_version="essay_correction_v1", engine_version="r3_correction_engine_v1",
                ai_output={"scores": None}, status="PENDING_REVIEW",
            )
            session.add(second)
            with self.assertRaises(Exception):
                await session.flush()


if __name__ == "__main__":
    unittest.main()
