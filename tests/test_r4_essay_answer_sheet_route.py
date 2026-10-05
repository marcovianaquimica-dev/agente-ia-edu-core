"""R4 lote - rota da folha de resposta em branco e upload da logo da escola."""

import asyncio
import unittest
import uuid

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import EssayPrompt, School, UserSchoolLink
from agente_ia_edu.identity import ExternalIdentityContext


def _ident(user: str) -> ExternalIdentityContext:
    return ExternalIdentityContext(provider="test", external_user_id=user)


def _logo_bytes() -> bytes:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=120, height=60)
    page.insert_text((10, 35), "LOGO", fontsize=20)
    data = page.get_pixmap(dpi=150).tobytes("png")
    doc.close()
    return data


class AnswerSheetRouteTests(unittest.TestCase):
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
        cls.app.dependency_overrides[get_current_identity] = lambda: _ident("prof_folha")
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    def _seed(self, code: str):
        async def _run():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"AS-{code}", name=f"escola-{code}")
                session.add(school)
                await session.flush()
                session.add(UserSchoolLink(
                    external_user_id="prof_folha", school_id=school.id, role="TEACHER",
                    scope_type="SCHOOL", active=True,
                ))
                prompt = EssayPrompt(
                    id=uuid.uuid4(), school_id=school.id,
                    title="Os desafios da mobilidade urbana", statement="Disserte.",
                    year=2026, created_by_external_identity="prof_folha",
                )
                session.add(prompt)
                await session.commit()
                return str(school.id), str(prompt.id)

        return self.loop.run_until_complete(_run())

    def test_generates_the_requested_number_of_copies(self):
        import pymupdf

        _school_id, prompt_id = self._seed("1")
        response = self.client.get(
            f"/api/v1/catalog/essay-prompts/{prompt_id}/answer-sheet.pdf?copies=4"
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertIn("attachment", response.headers["content-disposition"])
        doc = pymupdf.open(stream=response.content, filetype="pdf")
        try:
            self.assertEqual(doc.page_count, 4)
        finally:
            doc.close()

    def test_defaults_to_one_copy(self):
        import pymupdf

        _school_id, prompt_id = self._seed("2")
        response = self.client.get(
            f"/api/v1/catalog/essay-prompts/{prompt_id}/answer-sheet.pdf"
        )
        doc = pymupdf.open(stream=response.content, filetype="pdf")
        try:
            self.assertEqual(doc.page_count, 1)
        finally:
            doc.close()

    def test_rejects_an_out_of_range_copies_value(self):
        _school_id, prompt_id = self._seed("3")
        self.assertEqual(
            self.client.get(
                f"/api/v1/catalog/essay-prompts/{prompt_id}/answer-sheet.pdf?copies=0"
            ).status_code, 422,
        )
        self.assertEqual(
            self.client.get(
                f"/api/v1/catalog/essay-prompts/{prompt_id}/answer-sheet.pdf?copies=201"
            ).status_code, 422,
        )

    def test_a_prompt_from_another_school_is_403(self):
        _school_id, prompt_id = self._seed("4")

        async def _other():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code="AS-OTHER", name="outra")
                session.add(school)
                await session.flush()
                session.add(UserSchoolLink(
                    external_user_id="prof_outro_folha", school_id=school.id, role="TEACHER",
                    scope_type="SCHOOL", active=True,
                ))
                await session.commit()

        self.loop.run_until_complete(_other())
        self.app.dependency_overrides[get_current_identity] = lambda: _ident("prof_outro_folha")
        try:
            response = self.client.get(
                f"/api/v1/catalog/essay-prompts/{prompt_id}/answer-sheet.pdf"
            )
            self.assertEqual(response.status_code, 403)
        finally:
            self.app.dependency_overrides[get_current_identity] = lambda: _ident("prof_folha")

    def test_uploading_a_logo_makes_the_sheet_carry_it(self):
        import pymupdf

        _school_id, prompt_id = self._seed("5")

        before = self.client.get("/api/v1/teacher/school/logo")
        self.assertEqual(before.status_code, 200, before.text)
        self.assertFalse(before.json()["has_logo"])

        without_logo = self.client.get(
            f"/api/v1/catalog/essay-prompts/{prompt_id}/answer-sheet.pdf"
        )
        doc = pymupdf.open(stream=without_logo.content, filetype="pdf")
        try:
            # Sem logo da escola, a folha sai com a logo do Nucleo Edu 360 no
            # lugar - nunca em branco.
            self.assertEqual(len(doc[0].get_images(full=True)), 1)
        finally:
            doc.close()

        upload = self.client.post(
            "/api/v1/teacher/school/logo",
            files={"file": ("logo.png", _logo_bytes(), "image/png")},
        )
        self.assertEqual(upload.status_code, 200, upload.text)
        self.assertTrue(upload.json()["has_logo"])
        self.assertTrue(self.client.get("/api/v1/teacher/school/logo").json()["has_logo"])

        with_logo = self.client.get(
            f"/api/v1/catalog/essay-prompts/{prompt_id}/answer-sheet.pdf"
        )
        doc = pymupdf.open(stream=with_logo.content, filetype="pdf")
        try:
            self.assertEqual(len(doc[0].get_images(full=True)), 1)
        finally:
            doc.close()

    def test_rejects_a_logo_that_is_not_an_image(self):
        self._seed("6")
        response = self.client.post(
            "/api/v1/teacher/school/logo",
            files={"file": ("logo.pdf", b"%PDF-1.4", "application/pdf")},
        )
        self.assertEqual(response.status_code, 422, response.text)


if __name__ == "__main__":
    unittest.main()
