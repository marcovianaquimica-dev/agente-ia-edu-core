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
    EssayPrompt,
    EssaySubmission,
    GradeLevel,
    Person,
    PromptAssignment,
    School,
    SchoolModule,
    Segment,
    Student,
    StudentEnrollment,
    User,
    UserSchoolLink,
)
from agente_ia_edu.identity import ExternalIdentityContext


def _ident(user: str) -> ExternalIdentityContext:
    return ExternalIdentityContext(provider="test", external_user_id=user)


class StudentEssayPromptsRouteTests(unittest.TestCase):
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
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    def _as(self, user: str):
        self.app.dependency_overrides[get_current_identity] = lambda: _ident(user)

    def _seed(self, code: str, *, with_submission: bool = False, assignment_status: str = "OPEN"):
        async def _seed_async():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"SEP-{code}", name=f"school-{code}")
                session.add(school)
                await session.flush()
                session.add(SchoolModule(
                    id=uuid.uuid4(), school_id=school.id, module_key="REDACAO_IA", enabled=True,
                ))
                session.add(UserSchoolLink(
                    external_user_id=f"student_{code}", school_id=school.id, role="STUDENT",
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
                person = Person(id=uuid.uuid4(), school_id=school.id, full_name=f"Aluno {code}")
                session.add(person)
                await session.flush()
                session.add(User(
                    id=uuid.uuid4(), school_id=school.id, person_id=person.id,
                    external_identity_provider="test", external_user_id=f"student_{code}",
                ))
                student = Student(id=uuid.uuid4(), school_id=school.id, person_id=person.id, student_code=f"ST-{code}")
                session.add(student)
                await session.flush()
                session.add(StudentEnrollment(
                    id=uuid.uuid4(), school_id=school.id, student_id=student.id, class_id=klass.id,
                    status="ACTIVE",
                ))
                prompt = EssayPrompt(
                    id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
                    year=2026, status="ACTIVE", created_by_external_identity="teacher:t",
                )
                session.add(prompt)
                await session.flush()
                assignment = PromptAssignment(
                    id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
                    class_id=klass.id, assigned_by_external_identity="teacher:t",
                    status=assignment_status,
                )
                session.add(assignment)
                await session.flush()

                submission_id = None
                if with_submission:
                    submission_id = uuid.uuid4()
                    session.add(EssaySubmission(
                        id=submission_id, essay_id=uuid.uuid4(), school_id=school.id,
                        prompt_assignment_id=assignment.id, student_id=student.id,
                        mode="TYPED", anchor_mode="TEXT_OFFSET", status="SUBMITTED",
                        canonical_text="Redacao.", normalized_text_hash="a" * 64,
                        submitted_at=datetime.now(timezone.utc),
                    ))

                await session.commit()
                return assignment.id, submission_id

        return self.loop.run_until_complete(_seed_async())

    def test_lists_open_assignment_with_no_submission_yet(self):
        assignment_id, _submission_id = self._seed("1")
        self._as("student_1")
        resp = self.client.get("/api/v1/student/essay-prompts")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["prompt_assignment_id"], str(assignment_id))
        self.assertEqual(body[0]["title"], "Tema")
        self.assertIsNone(body[0]["my_submission"])

    def test_reflects_existing_submission_status(self):
        _assignment_id, submission_id = self._seed("2", with_submission=True)
        self._as("student_2")
        resp = self.client.get("/api/v1/student/essay-prompts")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(len(body), 1)
        self.assertIsNotNone(body[0]["my_submission"])
        self.assertEqual(body[0]["my_submission"]["status"], "SUBMITTED")
        self.assertEqual(body[0]["my_submission"]["id"], str(submission_id))
        self.assertEqual(body[0]["my_submission"]["anchor_mode"], "TEXT_OFFSET")
        self.assertEqual(body[0]["my_submission"]["mode"], "TYPED")

    def test_closed_assignment_is_not_listed(self):
        self._seed("closed", assignment_status="CLOSED")
        self._as("student_closed")
        resp = self.client.get("/api/v1/student/essay-prompts")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json(), [])

    def test_does_not_leak_another_school_or_class_assignment(self):
        self._seed("3")
        self._seed("4")
        self._as("student_3")
        resp = self.client.get("/api/v1/student/essay-prompts")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(len(resp.json()), 1)

    def test_requires_student_role(self):
        self._seed("5")
        self._as("teacher_5")
        resp = self.client.get("/api/v1/student/essay-prompts")
        self.assertEqual(resp.status_code, 403)


if __name__ == "__main__":
    unittest.main()
