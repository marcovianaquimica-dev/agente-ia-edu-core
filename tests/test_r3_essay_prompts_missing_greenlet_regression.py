"""Regression test: create_essay_prompt, add_prompt_material, and
create_prompt_assignment used to read ORM attributes (via inline
EssayPromptResponse/PromptMaterialResponse/PromptAssignmentResponse
construction) AFTER session.commit(). In production the app's real session
uses expire_on_commit=True (see db/session.py), so that post-commit access
triggers a synchronous lazy-load and raises MissingGreenlet under the async
engine - a 500 on every essay-prompt management write. Every other test file
for these routes (e.g. test_r2_essay_prompts_routes.py) builds its
session_factory with expire_on_commit=False, which silently masks this. This
file is the only one that mirrors the production session configuration."""

import asyncio
import unittest
import uuid

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import AcademicYear, Class, GradeLevel, School, Segment, UserSchoolLink
from agente_ia_edu.identity import ExternalIdentityContext


def _ident(user: str) -> ExternalIdentityContext:
    return ExternalIdentityContext(provider="test", external_user_id=user)


class EssayPromptsRoutesExpireOnCommitTests(unittest.TestCase):
    """Same route exercise as test_r2_essay_prompts_routes.py, but with
    expire_on_commit=True to match production and actually catch the
    MissingGreenlet regression that expire_on_commit=False fixtures hide."""

    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        cls.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        cls.factory = async_sessionmaker(cls.engine, class_=AsyncSession, expire_on_commit=True)

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

    def _seed_school_teacher_and_class(self, code: str):
        async def _seed():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"EPREOC-{code}", name=f"school-{code}")
                session.add(school)
                await session.flush()
                session.add(UserSchoolLink(
                    external_user_id=f"prof_eoc_{code}", school_id=school.id, role="TEACHER",
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
                # Capture ids into plain local variables BEFORE commit:
                # expire_on_commit=True expires every attribute on commit,
                # including client-side-assigned ids, and re-reading them
                # after commit would itself trigger the same MissingGreenlet
                # this whole file exists to catch - unrelated to what these
                # tests are actually exercising (the route layer).
                school_id, class_id = school.id, klass.id
                await session.commit()
                return school_id, class_id

        return self.loop.run_until_complete(_seed())

    def test_create_prompt_does_not_500_under_expire_on_commit(self):
        self._seed_school_teacher_and_class("1")
        self._as("prof_eoc_1")

        resp = self.client.post(
            "/api/v1/catalog/essay-prompts",
            json={"title": "Tema", "statement": "Disserte.", "year": 2026},
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "DRAFT")
        self.assertEqual(body["title"], "Tema")

    def test_add_material_does_not_500_under_expire_on_commit(self):
        self._seed_school_teacher_and_class("2")
        self._as("prof_eoc_2")

        create_resp = self.client.post(
            "/api/v1/catalog/essay-prompts",
            json={"title": "Tema", "statement": "Disserte.", "year": 2026},
        )
        self.assertEqual(create_resp.status_code, 201, create_resp.text)
        prompt_id = create_resp.json()["id"]

        material_resp = self.client.post(
            f"/api/v1/catalog/essay-prompts/{prompt_id}/materials",
            json={"material_type": "TEXT", "content": "Apoio.", "position": 0},
        )
        self.assertEqual(material_resp.status_code, 201, material_resp.text)
        body = material_resp.json()
        self.assertEqual(body["material_type"], "TEXT")
        self.assertEqual(body["content"], "Apoio.")

    def test_create_assignment_does_not_500_under_expire_on_commit(self):
        _school_id, class_id = self._seed_school_teacher_and_class("3")
        self._as("prof_eoc_3")

        create_resp = self.client.post(
            "/api/v1/catalog/essay-prompts",
            json={"title": "Tema", "statement": "Disserte.", "year": 2026},
        )
        self.assertEqual(create_resp.status_code, 201, create_resp.text)
        prompt_id = create_resp.json()["id"]

        assignment_resp = self.client.post(
            f"/api/v1/catalog/essay-prompts/{prompt_id}/assignments",
            json={"class_id": str(class_id)},
        )
        self.assertEqual(assignment_resp.status_code, 201, assignment_resp.text)
        body = assignment_resp.json()
        self.assertEqual(body["status"], "OPEN")
        self.assertEqual(body["class_id"], str(class_id))


if __name__ == "__main__":
    unittest.main()
