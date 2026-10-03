"""Folha de resposta em branco, independente de proposta.

A folha nao menciona mais o tema de nenhuma proposta (services/essay_answer_
sheet.py), entao professor/coordenador/diretor e aluno podem baixar a mesma
folha sem escolher uma proposta especifica primeiro - so a logo da escola do
proprio contexto do chamador muda. Duas rotas:

- GET /api/v1/catalog/essay-prompts/answer-sheet.pdf (professor/coordenador/
  diretor/admin da plataforma - mesma autorizacao de
  get_essay_prompt_answer_sheet)
- GET /api/v1/student/essay-prompts/answer-sheet.pdf (aluno)
"""

import asyncio
import unittest
import uuid

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import School, SchoolModule, UserSchoolLink
from agente_ia_edu.identity import ExternalIdentityContext


def _ident(user: str) -> ExternalIdentityContext:
    return ExternalIdentityContext(provider="test", external_user_id=user)


class GenericAnswerSheetRoutesTests(unittest.TestCase):
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

    def _seed_school_with_role(self, code: str, *, role: str, with_redacao_module: bool = False):
        async def _seed():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"GAS-{code}", name=f"school-{code}")
                session.add(school)
                await session.flush()
                if with_redacao_module:
                    session.add(SchoolModule(
                        id=uuid.uuid4(), school_id=school.id, module_key="REDACAO_IA", enabled=True,
                    ))
                session.add(UserSchoolLink(
                    external_user_id=f"user_{code}", school_id=school.id, role=role,
                    scope_type="SCHOOL", active=True,
                ))
                await session.commit()
                return school.id

        return self.loop.run_until_complete(_seed())

    # ---- professor/coordenador/diretor -------------------------------------

    def test_teacher_downloads_the_generic_sheet_without_a_prompt(self):
        import pymupdf

        self._seed_school_with_role("1", role="TEACHER")
        self._as("user_1")
        response = self.client.get("/api/v1/catalog/essay-prompts/answer-sheet.pdf")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "application/pdf")
        doc = pymupdf.open(stream=response.content, filetype="pdf")
        try:
            self.assertEqual(doc.page_count, 1)
        finally:
            doc.close()

    def test_coordinator_can_download_the_generic_sheet_too(self):
        self._seed_school_with_role("2", role="COORDINATOR")
        self._as("user_2")
        response = self.client.get("/api/v1/catalog/essay-prompts/answer-sheet.pdf")
        self.assertEqual(response.status_code, 200, response.text)

    def test_copies_query_param_controls_the_page_count(self):
        import pymupdf

        self._seed_school_with_role("3", role="TEACHER")
        self._as("user_3")
        response = self.client.get("/api/v1/catalog/essay-prompts/answer-sheet.pdf?copies=5")
        doc = pymupdf.open(stream=response.content, filetype="pdf")
        try:
            self.assertEqual(doc.page_count, 5)
        finally:
            doc.close()

    def test_a_student_cannot_use_the_staff_route(self):
        self._seed_school_with_role("4", role="STUDENT")
        self._as("user_4")
        response = self.client.get("/api/v1/catalog/essay-prompts/answer-sheet.pdf")
        self.assertEqual(response.status_code, 403)

    # ---- aluno --------------------------------------------------------------

    def test_student_downloads_the_generic_sheet_without_a_prompt(self):
        import pymupdf

        self._seed_school_with_role("5", role="STUDENT", with_redacao_module=True)
        self._as("user_5")
        response = self.client.get("/api/v1/student/essay-prompts/answer-sheet.pdf")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "application/pdf")
        doc = pymupdf.open(stream=response.content, filetype="pdf")
        try:
            self.assertEqual(doc.page_count, 1)
        finally:
            doc.close()

    def test_student_without_the_redacao_module_is_403(self):
        self._seed_school_with_role("6", role="STUDENT", with_redacao_module=False)
        self._as("user_6")
        response = self.client.get("/api/v1/student/essay-prompts/answer-sheet.pdf")
        self.assertEqual(response.status_code, 403)

    def test_a_teacher_cannot_use_the_student_route(self):
        self._seed_school_with_role("7", role="TEACHER", with_redacao_module=True)
        self._as("user_7")
        response = self.client.get("/api/v1/student/essay-prompts/answer-sheet.pdf")
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
