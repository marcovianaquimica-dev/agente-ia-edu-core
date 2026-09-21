import asyncio
import unittest
import uuid

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


class EssaySubmissionAuthorizationTests(unittest.TestCase):
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

    def _seed_school_with_class_and_assignment(self, code: str, *, module_enabled: bool = True):
        async def _seed():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"AUT-{code}", name=f"school-{code}")
                session.add(school)
                await session.flush()
                if module_enabled:
                    session.add(SchoolModule(
                        id=uuid.uuid4(), school_id=school.id, module_key="REDACAO_IA", enabled=True,
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
                return school.id, klass.id, assignment.id

        return self.loop.run_until_complete(_seed())

    def _enroll_student(self, school_id, class_id, external_user_id: str):
        async def _seed():
            async with self.factory() as session:
                session.add(UserSchoolLink(
                    external_user_id=external_user_id, school_id=school_id, role="STUDENT",
                    scope_type="SCHOOL", active=True,
                ))
                person = Person(id=uuid.uuid4(), school_id=school_id, full_name=external_user_id)
                session.add(person)
                await session.flush()
                session.add(User(
                    id=uuid.uuid4(), school_id=school_id, person_id=person.id,
                    external_identity_provider="test", external_user_id=external_user_id,
                ))
                student = Student(
                    id=uuid.uuid4(), school_id=school_id, person_id=person.id,
                    student_code=external_user_id,
                )
                session.add(student)
                await session.flush()
                session.add(StudentEnrollment(
                    id=uuid.uuid4(), school_id=school_id, student_id=student.id, class_id=class_id,
                    status="ACTIVE",
                ))
                await session.commit()

        self.loop.run_until_complete(_seed())

    def test_a_student_with_no_enrollment_at_all_is_denied(self):
        _school_id, _class_id, assignment_id = self._seed_school_with_class_and_assignment("1")
        self._as("ghost_student")
        resp = self.client.post(
            "/api/v1/student/essay-submissions",
            json={"prompt_assignment_id": str(assignment_id), "mode": "TYPED", "text": "x"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_a_student_enrolled_in_a_different_class_is_denied_not_404(self):
        school_id, _class_id, assignment_id = self._seed_school_with_class_and_assignment("2")

        async def _other_class():
            async with self.factory() as session:
                segment = Segment(id=uuid.uuid4(), school_id=school_id, name="seg2", external_id="SEG-2B")
                session.add(segment)
                await session.flush()
                grade = GradeLevel(
                    id=uuid.uuid4(), school_id=school_id, segment_id=segment.id,
                    name="grade2", external_id="GRADE-2B",
                )
                # NOTE: deviates from the task-11 brief's literal test code, which used
                # year=2026 here - that collides with the uq_academic_years_school_year
                # unique constraint (school_id, year) added in Task 2, since the base
                # fixture already created a 2026 AcademicYear for this same school. Using
                # a different year is the minimal fix; it does not change what this test
                # verifies (cross-class enrollment must be denied with 403).
                year = AcademicYear(id=uuid.uuid4(), school_id=school_id, year=2027, external_id="YEAR-2B")
                session.add_all([grade, year])
                await session.flush()
                other_class = Class(
                    id=uuid.uuid4(), school_id=school_id, academic_year_id=year.id,
                    grade_level_id=grade.id, name="outra-turma", external_id="TURMA-2B",
                )
                session.add(other_class)
                await session.commit()
                return other_class.id

        other_class_id = self.loop.run_until_complete(_other_class())
        self._enroll_student(school_id, other_class_id, "wrong_class_student")
        self._as("wrong_class_student")

        resp = self.client.post(
            "/api/v1/student/essay-submissions",
            json={"prompt_assignment_id": str(assignment_id), "mode": "TYPED", "text": "x"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_module_disabled_denies_submission(self):
        school_id, class_id, assignment_id = self._seed_school_with_class_and_assignment(
            "3", module_enabled=False
        )
        self._enroll_student(school_id, class_id, "no_module_student")
        self._as("no_module_student")

        resp = self.client.post(
            "/api/v1/student/essay-submissions",
            json={"prompt_assignment_id": str(assignment_id), "mode": "TYPED", "text": "x"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_a_teacher_cannot_submit_an_essay(self):
        school_id, class_id, assignment_id = self._seed_school_with_class_and_assignment("4")

        async def _link_teacher():
            async with self.factory() as session:
                session.add(UserSchoolLink(
                    external_user_id="teacher_not_student", school_id=school_id, role="TEACHER",
                    scope_type="SCHOOL", active=True,
                ))
                await session.commit()

        self.loop.run_until_complete(_link_teacher())
        self._as("teacher_not_student")

        resp = self.client.post(
            "/api/v1/student/essay-submissions",
            json={"prompt_assignment_id": str(assignment_id), "mode": "TYPED", "text": "x"},
        )
        self.assertEqual(resp.status_code, 403)


if __name__ == "__main__":
    unittest.main()
