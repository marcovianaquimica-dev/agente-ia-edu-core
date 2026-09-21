import asyncio
import unittest
import uuid

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import get_session_factory, get_current_identity
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import AcademicYear, Class, GradeLevel, School, Segment, UserSchoolLink
from agente_ia_edu.identity import ExternalIdentityContext


def _ident(user: str) -> ExternalIdentityContext:
    return ExternalIdentityContext(provider="test", external_user_id=user)


class EssayPromptsRoutesTests(unittest.TestCase):
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
        cls.app.dependency_overrides[get_current_identity] = lambda: _ident("prof_r2")
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    def _as(self, user: str):
        self.app.dependency_overrides[get_current_identity] = lambda: _ident(user)

    def _seed_school_teacher_and_class(self, code: str):
        async def _seed():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"EPR-{code}", name=f"school-{code}")
                session.add(school)
                await session.flush()
                session.add(UserSchoolLink(
                    external_user_id="prof_r2", school_id=school.id, role="TEACHER",
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
                await session.commit()
                return school.id, klass.id

        return self.loop.run_until_complete(_seed())

    def test_full_management_flow(self):
        school_id, class_id = self._seed_school_teacher_and_class("1")
        self._as("prof_r2")

        create_resp = self.client.post(
            "/api/v1/catalog/essay-prompts",
            json={"title": "Tema", "statement": "Disserte.", "year": 2026},
        )
        self.assertEqual(create_resp.status_code, 201, create_resp.text)
        prompt_id = create_resp.json()["id"]
        self.assertEqual(create_resp.json()["status"], "DRAFT")

        material_resp = self.client.post(
            f"/api/v1/catalog/essay-prompts/{prompt_id}/materials",
            json={"material_type": "TEXT", "content": "Apoio.", "position": 0},
        )
        self.assertEqual(material_resp.status_code, 201, material_resp.text)

        assignment_resp = self.client.post(
            f"/api/v1/catalog/essay-prompts/{prompt_id}/assignments",
            json={"class_id": str(class_id)},
        )
        self.assertEqual(assignment_resp.status_code, 201, assignment_resp.text)
        self.assertEqual(assignment_resp.json()["status"], "OPEN")

    def test_student_cannot_create_prompt(self):
        self._seed_school_teacher_and_class("2")
        self._as("student_r2")
        resp = self.client.post(
            "/api/v1/catalog/essay-prompts",
            json={"title": "Tema", "statement": "Disserte.", "year": 2026},
        )
        self.assertEqual(resp.status_code, 403)
        self._as("prof_r2")


if __name__ == "__main__":
    unittest.main()
