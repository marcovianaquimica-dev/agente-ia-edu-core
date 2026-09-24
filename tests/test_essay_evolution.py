import asyncio
import unittest
import uuid
from datetime import datetime, timedelta, timezone

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
    Person,
    PromptAssignment,
    School,
    Segment,
    Student,
)
from agente_ia_edu.services.essay_evolution import build_evolution


class EssayEvolutionServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        cls.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        cls.factory = async_sessionmaker(cls.engine, class_=AsyncSession, expire_on_commit=False)

        async def _prep():
            async with cls.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        cls.loop.run_until_complete(_prep())

    @classmethod
    def tearDownClass(cls):
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    def _seed_school_and_student(self, code: str):
        async def _seed():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"SEV-{code}", name=f"school-{code}")
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
                person = Person(id=uuid.uuid4(), school_id=school.id, full_name=f"Aluno {code}")
                session.add(person)
                await session.flush()
                student = Student(id=uuid.uuid4(), school_id=school.id, person_id=person.id, student_code=f"ST-{code}")
                session.add(student)
                prompt = EssayPrompt(
                    id=uuid.uuid4(), school_id=school.id, title=f"Tema {code}", statement="Disserte.",
                    year=2026, status="ACTIVE", created_by_external_identity="teacher:t",
                )
                session.add(prompt)
                await session.flush()
                assignment = PromptAssignment(
                    id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
                    class_id=klass.id, assigned_by_external_identity="teacher:t",
                )
                session.add(assignment)
                await session.commit()
                return school.id, student.id, assignment.id

        return self.loop.run_until_complete(_seed())

    def _add_submission_with_correction(
        self, *, school_id, student_id, assignment_id, published_at, total=None, formativo=False,
    ):
        async def _add():
            async with self.factory() as session:
                submission = EssaySubmission(
                    id=uuid.uuid4(), essay_id=uuid.uuid4(), school_id=school_id,
                    prompt_assignment_id=assignment_id, student_id=student_id,
                    mode="TYPED", anchor_mode="TEXT_OFFSET", status="SUBMITTED",
                    canonical_text="Redacao.", normalized_text_hash="a" * 64,
                    submitted_at=published_at,
                )
                session.add(submission)
                await session.flush()
                final_scores = None if formativo else {
                    "total": total,
                    "per_competency": {
                        code: {"points": total // 5, "confidence": 1.0}
                        for code in ("C1", "C2", "C3", "C4", "C5")
                    },
                }
                session.add(EssayCorrection(
                    id=uuid.uuid4(), school_id=school_id, essay_submission_id=submission.id,
                    correction_key="k" * 64, rubric_version="ENEM_2025", model_version="gpt-test",
                    prompt_version="essay_correction_v1", engine_version="r3_correction_engine_v1",
                    ai_output={"annotations": [], "rewrites": [], "intervention": {}, "alerts": []},
                    final_scores=final_scores, final_feedback={},
                    status="APPROVED", reviewed_at=published_at, published_at=published_at,
                ))
                await session.commit()
                return submission.id

        return self.loop.run_until_complete(_add())

    async def _call(self, school_id, student_id):
        async with self.factory() as session:
            return await build_evolution(session, school_id=school_id, student_id=student_id)

    def test_no_approved_corrections_returns_empty(self):
        school_id, student_id, _assignment_id = self._seed_school_and_student("1")
        result = self.loop.run_until_complete(self._call(school_id, student_id))
        self.assertEqual(result["entries"], [])
        self.assertIsNone(result["total_delta"])

    def test_single_correction_has_no_delta(self):
        school_id, student_id, assignment_id = self._seed_school_and_student("2")
        self._add_submission_with_correction(
            school_id=school_id, student_id=student_id, assignment_id=assignment_id,
            published_at=datetime.now(timezone.utc), total=620,
        )
        result = self.loop.run_until_complete(self._call(school_id, student_id))
        self.assertEqual(len(result["entries"]), 1)
        self.assertIsNone(result["total_delta"])
        self.assertEqual(result["entries"][0]["total"], 620)
        self.assertEqual(result["entries"][0]["per_competency"]["C1"], 124)

    def test_multiple_corrections_ordered_newest_first_with_delta(self):
        school_id, student_id, assignment_id = self._seed_school_and_student("3")
        base = datetime.now(timezone.utc)
        self._add_submission_with_correction(
            school_id=school_id, student_id=student_id, assignment_id=assignment_id,
            published_at=base, total=620,
        )
        self._add_submission_with_correction(
            school_id=school_id, student_id=student_id, assignment_id=assignment_id,
            published_at=base + timedelta(days=10), total=800,
        )
        result = self.loop.run_until_complete(self._call(school_id, student_id))
        self.assertEqual(len(result["entries"]), 2)
        self.assertEqual(result["entries"][0]["total"], 800)
        self.assertEqual(result["entries"][1]["total"], 620)
        self.assertEqual(result["total_delta"], 180)

    def test_formativo_correction_has_no_total_or_per_competency(self):
        school_id, student_id, assignment_id = self._seed_school_and_student("4")
        self._add_submission_with_correction(
            school_id=school_id, student_id=student_id, assignment_id=assignment_id,
            published_at=datetime.now(timezone.utc), formativo=True,
        )
        result = self.loop.run_until_complete(self._call(school_id, student_id))
        self.assertIsNone(result["entries"][0]["total"])
        self.assertIsNone(result["entries"][0]["per_competency"])

    def test_formativo_excluded_from_total_delta(self):
        school_id, student_id, assignment_id = self._seed_school_and_student("5")
        base = datetime.now(timezone.utc)
        self._add_submission_with_correction(
            school_id=school_id, student_id=student_id, assignment_id=assignment_id,
            published_at=base, total=620,
        )
        self._add_submission_with_correction(
            school_id=school_id, student_id=student_id, assignment_id=assignment_id,
            published_at=base + timedelta(days=5), formativo=True,
        )
        self._add_submission_with_correction(
            school_id=school_id, student_id=student_id, assignment_id=assignment_id,
            published_at=base + timedelta(days=10), total=800,
        )
        result = self.loop.run_until_complete(self._call(school_id, student_id))
        self.assertEqual(result["total_delta"], 180)

    def test_other_students_corrections_are_excluded(self):
        school_id, student_id, assignment_id = self._seed_school_and_student("6")
        _other_school_id, other_student_id, other_assignment_id = self._seed_school_and_student("7")
        self._add_submission_with_correction(
            school_id=school_id, student_id=student_id, assignment_id=assignment_id,
            published_at=datetime.now(timezone.utc), total=620,
        )
        self._add_submission_with_correction(
            school_id=_other_school_id, student_id=other_student_id, assignment_id=other_assignment_id,
            published_at=datetime.now(timezone.utc), total=999,
        )
        result = self.loop.run_until_complete(self._call(school_id, student_id))
        self.assertEqual(len(result["entries"]), 1)
        self.assertEqual(result["entries"][0]["total"], 620)


if __name__ == "__main__":
    unittest.main()
