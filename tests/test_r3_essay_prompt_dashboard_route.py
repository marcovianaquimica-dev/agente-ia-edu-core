import asyncio
import unittest
import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
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
    UserSchoolLink,
)
from agente_ia_edu.identity import ExternalIdentityContext


def _ident(user: str) -> ExternalIdentityContext:
    return ExternalIdentityContext(provider="test", external_user_id=user)


class EssayPromptDashboardRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        cls.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        cls.factory = async_sessionmaker(cls.engine, class_=AsyncSession, expire_on_commit=False)

        async def _prep():
            async with cls.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        cls.loop.run_until_complete(_prep())
        cls.app = create_app()
        cls.app.dependency_overrides[get_session_factory] = lambda: cls.factory
        cls.app.dependency_overrides[get_current_identity] = lambda: _ident("prof_dash")
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    def _seed(self, code: str):
        async def _seed_async():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"EPD-{code}", name=f"school-{code}")
                session.add(school)
                await session.flush()
                session.add(UserSchoolLink(
                    external_user_id="prof_dash", school_id=school.id, role="TEACHER",
                    scope_type="SCHOOL", active=True,
                ))
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
                await session.flush()

                students = []
                for i in range(2):
                    person = Person(id=uuid.uuid4(), school_id=school.id, full_name=f"Aluno {code}-{i}")
                    session.add(person)
                    await session.flush()
                    student = Student(
                        id=uuid.uuid4(), school_id=school.id, person_id=person.id,
                        student_code=f"ST-{code}-{i}",
                    )
                    session.add(student)
                    await session.flush()
                    session.add(StudentEnrollment(
                        id=uuid.uuid4(), school_id=school.id, student_id=student.id,
                        class_id=klass.id, status="ACTIVE",
                    ))
                    students.append(student)

                prompt = EssayPrompt(
                    id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
                    year=2026, status="ACTIVE", created_by_external_identity="teacher:prof_dash",
                )
                session.add(prompt)
                await session.flush()
                assignment = PromptAssignment(
                    id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
                    class_id=klass.id, assigned_by_external_identity="teacher:prof_dash",
                )
                session.add(assignment)
                await session.flush()

                submission = EssaySubmission(
                    id=uuid.uuid4(), essay_id=uuid.uuid4(), school_id=school.id,
                    prompt_assignment_id=assignment.id, student_id=students[0].id,
                    mode="TYPED", anchor_mode="TEXT_OFFSET", status="SUBMITTED",
                    canonical_text="Redacao.", normalized_text_hash="a" * 64,
                    submitted_at=datetime.now(timezone.utc),
                )
                session.add(submission)
                await session.flush()
                now = datetime.now(timezone.utc)
                session.add(EssayCorrection(
                    id=uuid.uuid4(), school_id=school.id, essay_submission_id=submission.id,
                    correction_key="k" * 64, rubric_version="ENEM_2025", model_version="gpt-test",
                    prompt_version="essay_correction_v1", engine_version="r3_correction_engine_v1",
                    ai_output={"annotations": [], "rewrites": [], "intervention": {}, "alerts": []},
                    final_scores={
                        "total": 640,
                        "per_competency": {c: {"points": 128} for c in ("C1", "C2", "C3", "C4", "C5")},
                    },
                    final_feedback={}, status="APPROVED", reviewed_at=now, published_at=now,
                ))
                await session.commit()
                return prompt.id, klass.id, students

        return self.loop.run_until_complete(_seed_async())

    def test_dashboard_route_returns_aggregate_stats(self):
        prompt_id, class_id, students = self._seed("1")
        resp = self.client.get(f"/api/v1/catalog/essay-prompts/{prompt_id}/dashboard")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["total_students"], 2)
        self.assertEqual(body["submitted_count"], 1)
        self.assertEqual(body["submitted_percentage"], 50.0)
        self.assertEqual(body["average_total_score"], 640.0)
        self.assertEqual(len(body["students"]), 2)
        self.assertTrue(len(body["action_plan"]) >= 1)

    def test_dashboard_route_applies_student_filter(self):
        prompt_id, class_id, students = self._seed("2")
        resp = self.client.get(
            f"/api/v1/catalog/essay-prompts/{prompt_id}/dashboard",
            params={"student_id": str(students[1].id)},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["total_students"], 1)
        self.assertEqual(body["students"][0]["student_id"], str(students[1].id))

    def test_dashboard_route_403s_for_a_prompt_from_another_school(self):
        prompt_id, _class_id, _students = self._seed("3")
        # A second, unrelated school+teacher with no link to the prompt above.
        async def _seed_other():
            async with self.factory() as session:
                other_school = School(id=uuid.uuid4(), code="EPD-OTHER", name="other")
                session.add(other_school)
                await session.flush()
                session.add(UserSchoolLink(
                    external_user_id="prof_other", school_id=other_school.id, role="TEACHER",
                    scope_type="SCHOOL", active=True,
                ))
                await session.commit()

        self.loop.run_until_complete(_seed_other())
        self.app.dependency_overrides[get_current_identity] = lambda: _ident("prof_other")
        try:
            resp = self.client.get(f"/api/v1/catalog/essay-prompts/{prompt_id}/dashboard")
            self.assertEqual(resp.status_code, 403)
        finally:
            self.app.dependency_overrides[get_current_identity] = lambda: _ident("prof_dash")


    def test_export_xlsx_route_returns_a_real_xlsx_file(self):
        prompt_id, class_id, students = self._seed("4")
        resp = self.client.get(
            f"/api/v1/catalog/essay-prompts/{prompt_id}/dashboard/export.xlsx",
            params={"report_type": "grades_per_competency"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(
            resp.headers["content-type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertIn("attachment", resp.headers["content-disposition"])
        self.assertTrue(resp.content.startswith(b"PK"))  # XLSX is a zip container

    def test_export_xlsx_route_rejects_unknown_report_type(self):
        prompt_id, _class_id, _students = self._seed("5")
        resp = self.client.get(
            f"/api/v1/catalog/essay-prompts/{prompt_id}/dashboard/export.xlsx",
            params={"report_type": "not_a_real_type"},
        )
        self.assertEqual(resp.status_code, 422)


if __name__ == "__main__":
    unittest.main()
