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
    Person,
    PromptAssignment,
    School,
    Segment,
    Student,
    StudentEnrollment,
)
from agente_ia_edu.services.essay_teacher_dashboard import (
    EssayDashboardPolicy,
    build_essay_prompt_dashboard,
)


class EssayTeacherDashboardTests(unittest.IsolatedAsyncioTestCase):
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

    async def _base_school(self, session, code):
        school = School(id=uuid.uuid4(), code=f"DASH-{code}", name=f"school-{code}")
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
        return school, segment, grade, year

    async def _class(self, session, school, grade, year, code):
        klass = Class(
            id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
            grade_level_id=grade.id, name=f"turma-{code}", external_id=f"TURMA-{code}",
        )
        session.add(klass)
        await session.flush()
        return klass

    async def _enrolled_student(self, session, school, klass, code, name):
        person = Person(id=uuid.uuid4(), school_id=school.id, full_name=name)
        session.add(person)
        await session.flush()
        student = Student(
            id=uuid.uuid4(), school_id=school.id, person_id=person.id, student_code=f"ST-{code}",
        )
        session.add(student)
        await session.flush()
        session.add(StudentEnrollment(
            id=uuid.uuid4(), school_id=school.id, student_id=student.id, class_id=klass.id,
            status="ACTIVE",
        ))
        await session.flush()
        return student

    async def _submit_and_approve(
        self, session, *, school, assignment, student, total, per_competency=None,
    ):
        submission = EssaySubmission(
            id=uuid.uuid4(), essay_id=uuid.uuid4(), school_id=school.id,
            prompt_assignment_id=assignment.id, student_id=student.id,
            mode="TYPED", anchor_mode="TEXT_OFFSET", status="SUBMITTED",
            canonical_text="Redacao.", normalized_text_hash="a" * 64,
            submitted_at=datetime.now(timezone.utc),
        )
        session.add(submission)
        await session.flush()
        per_competency = per_competency or {
            code: total // 5 for code in ("C1", "C2", "C3", "C4", "C5")
        }
        now = datetime.now(timezone.utc)
        session.add(EssayCorrection(
            id=uuid.uuid4(), school_id=school.id, essay_submission_id=submission.id,
            correction_key="k" * 64, rubric_version="ENEM_2025", model_version="gpt-test",
            prompt_version="essay_correction_v1", engine_version="r3_correction_engine_v1",
            ai_output={"annotations": [], "rewrites": [], "intervention": {}, "alerts": []},
            final_scores={
                "total": total,
                "per_competency": {code: {"points": p} for code, p in per_competency.items()},
            },
            final_feedback={}, status="APPROVED", reviewed_at=now, published_at=now,
        ))
        await session.flush()
        return submission

    async def test_dashboard_counts_submitted_vs_not_and_averages_only_approved_scores(self):
        async with self.session_factory() as session:
            school, _seg, grade, year = await self._base_school(session, "1")
            klass = await self._class(session, school, grade, year, "1")
            s1 = await self._enrolled_student(session, school, klass, "1a", "Aluno A")
            s2 = await self._enrolled_student(session, school, klass, "1b", "Aluno B")
            s3 = await self._enrolled_student(session, school, klass, "1c", "Aluno C")

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

            await self._submit_and_approve(session, school=school, assignment=assignment, student=s1, total=800)
            # s2 submits but has no correction yet (still processing) - counts
            # as submitted, but must not pollute the average.
            session.add(EssaySubmission(
                id=uuid.uuid4(), essay_id=uuid.uuid4(), school_id=school.id,
                prompt_assignment_id=assignment.id, student_id=s2.id,
                mode="TYPED", anchor_mode="TEXT_OFFSET", status="SUBMITTED",
                canonical_text="Redacao.", normalized_text_hash="b" * 64,
                submitted_at=datetime.now(timezone.utc),
            ))
            # s3 never submits.
            await session.commit()

            result = await build_essay_prompt_dashboard(
                session, school_id=school.id, essay_prompt_id=prompt.id,
            )
            self.assertEqual(result.total_students, 3)
            self.assertEqual(result.submitted_count, 2)
            self.assertAlmostEqual(result.submitted_percentage, 66.7, places=1)
            self.assertEqual(result.average_total_score, 800.0)
            not_submitted = [r for r in result.students if not r.submitted]
            self.assertEqual(len(not_submitted), 1)
            self.assertEqual(not_submitted[0].student_id, s3.id)

    async def test_class_filter_narrows_to_one_class_of_a_multi_class_prompt(self):
        async with self.session_factory() as session:
            school, _seg, grade, year = await self._base_school(session, "2")
            class_a = await self._class(session, school, grade, year, "2a")
            class_b = await self._class(session, school, grade, year, "2b")
            student_a = await self._enrolled_student(session, school, class_a, "2a1", "Aluno A")
            student_b = await self._enrolled_student(session, school, class_b, "2b1", "Aluno B")

            prompt = EssayPrompt(
                id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
                year=2026, status="ACTIVE", created_by_external_identity="teacher:t",
            )
            session.add(prompt)
            await session.flush()
            assignment_a = PromptAssignment(
                id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
                class_id=class_a.id, assigned_by_external_identity="teacher:t",
            )
            assignment_b = PromptAssignment(
                id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
                class_id=class_b.id, assigned_by_external_identity="teacher:t",
            )
            session.add_all([assignment_a, assignment_b])
            await session.flush()
            await self._submit_and_approve(session, school=school, assignment=assignment_a, student=student_a, total=600)
            await session.commit()

            result = await build_essay_prompt_dashboard(
                session, school_id=school.id, essay_prompt_id=prompt.id, class_id=class_b.id,
            )
            self.assertEqual(result.total_students, 1)
            self.assertEqual(result.students[0].student_id, student_b.id)
            self.assertEqual(result.submitted_count, 0)

    async def test_student_filter_returns_only_that_student(self):
        async with self.session_factory() as session:
            school, _seg, grade, year = await self._base_school(session, "3")
            klass = await self._class(session, school, grade, year, "3")
            s1 = await self._enrolled_student(session, school, klass, "3a", "Aluno A")
            s2 = await self._enrolled_student(session, school, klass, "3b", "Aluno B")
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
            await session.commit()

            result = await build_essay_prompt_dashboard(
                session, school_id=school.id, essay_prompt_id=prompt.id, student_id=s1.id,
            )
            self.assertEqual(result.total_students, 1)
            self.assertEqual(result.students[0].student_id, s1.id)

    async def test_no_matching_assignment_returns_zero_state_not_an_error(self):
        async with self.session_factory() as session:
            school, _seg, grade, year = await self._base_school(session, "4")
            klass = await self._class(session, school, grade, year, "4")
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
            await session.commit()

            other_class_id = uuid.uuid4()
            result = await build_essay_prompt_dashboard(
                session, school_id=school.id, essay_prompt_id=prompt.id, class_id=other_class_id,
            )
            self.assertEqual(result.total_students, 0)
            self.assertEqual(result.submitted_count, 0)
            self.assertEqual(result.students, [])
            self.assertEqual(result.action_plan, [])

    async def test_wrong_school_raises(self):
        async with self.session_factory() as session:
            school, _seg, grade, year = await self._base_school(session, "5")
            prompt = EssayPrompt(
                id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
                year=2026, status="ACTIVE", created_by_external_identity="teacher:t",
            )
            session.add(prompt)
            await session.commit()

            with self.assertRaises(ValueError):
                await build_essay_prompt_dashboard(
                    session, school_id=uuid.uuid4(), essay_prompt_id=prompt.id,
                )


class EssayDashboardPolicyTests(unittest.TestCase):
    def test_low_submission_rate_is_high_priority(self):
        policy = EssayDashboardPolicy()
        items = policy.build_action_plan(
            submitted_count=2, total_students=10, average_total=None, average_per_competency=None,
        )
        self.assertEqual(items[0]["priority"], "HIGH")
        self.assertIn("entrega", items[0]["recommended_action"].lower())

    def test_weak_competency_is_flagged(self):
        policy = EssayDashboardPolicy()
        items = policy.build_action_plan(
            submitted_count=10, total_students=10, average_total=700,
            average_per_competency={"C1": 80.0, "C2": 180.0, "C3": 180.0, "C4": 180.0, "C5": 180.0},
        )
        c1_items = [i for i in items if "C1" in i["recommended_action"]]
        self.assertEqual(len(c1_items), 1)
        self.assertEqual(c1_items[0]["priority"], "HIGH")

    def test_healthy_class_gets_a_low_priority_consolidation_item(self):
        policy = EssayDashboardPolicy()
        items = policy.build_action_plan(
            submitted_count=10, total_students=10, average_total=900,
            average_per_competency={code: 180.0 for code in ("C1", "C2", "C3", "C4", "C5")},
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["priority"], "LOW")

    def test_no_submissions_at_all_produces_no_action_plan(self):
        policy = EssayDashboardPolicy()
        items = policy.build_action_plan(
            submitted_count=0, total_students=0, average_total=None, average_per_competency=None,
        )
        self.assertEqual(items, [])


if __name__ == "__main__":
    unittest.main()
