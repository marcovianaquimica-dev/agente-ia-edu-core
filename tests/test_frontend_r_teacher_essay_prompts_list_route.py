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
    PromptAssignment,
    PromptMaterial,
    School,
    Segment,
    UserSchoolLink,
)
from agente_ia_edu.identity import ExternalIdentityContext


def _ident(user: str) -> ExternalIdentityContext:
    return ExternalIdentityContext(provider="test", external_user_id=user)


class TeacherEssayPromptsListRouteTests(unittest.TestCase):
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

    def _seed(self, code: str, *, with_material_and_assignment: bool = False):
        async def _seed_async():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"TEP-{code}", name=f"school-{code}")
                session.add(school)
                await session.flush()
                session.add(UserSchoolLink(
                    external_user_id=f"teacher_{code}", school_id=school.id, role="TEACHER",
                    scope_type="SCHOOL", active=True,
                ))
                prompt = EssayPrompt(
                    id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
                    year=2026, status="DRAFT", created_by_external_identity="teacher:t",
                )
                session.add(prompt)
                await session.flush()

                if with_material_and_assignment:
                    session.add(PromptMaterial(
                        id=uuid.uuid4(), essay_prompt_id=prompt.id, material_type="TEXT",
                        content="Texto de apoio.", position=0,
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
                    session.add(PromptAssignment(
                        id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
                        class_id=klass.id, assigned_by_external_identity="teacher:t",
                    ))

                await session.commit()
                return school.id, prompt.id

        return self.loop.run_until_complete(_seed_async())

    def test_list_scopes_to_own_school(self):
        school_id, prompt_id = self._seed("1")
        self._seed("2")
        self._as("teacher_1")
        resp = self.client.get("/api/v1/catalog/essay-prompts")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["id"], str(prompt_id))

    def test_detail_includes_materials_and_assignments(self):
        school_id, prompt_id = self._seed("3", with_material_and_assignment=True)
        self._as("teacher_3")
        resp = self.client.get(f"/api/v1/catalog/essay-prompts/{prompt_id}")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(len(body["materials"]), 1)
        self.assertEqual(len(body["assignments"]), 1)

    def test_detail_from_another_school_is_403(self):
        _school_id, prompt_id = self._seed("4")
        self._seed("5")
        self._as("teacher_5")
        resp = self.client.get(f"/api/v1/catalog/essay-prompts/{prompt_id}")
        self.assertEqual(resp.status_code, 403)


if __name__ == "__main__":
    unittest.main()
