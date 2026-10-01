"""Listagem e detalhe do professor com propostas da plataforma misturadas.

TestClient + dependency_overrides, mesmo padrao de
tests/test_r2_essay_prompts_routes.py.
"""

import asyncio
import unittest
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import PlatformEssayPrompt, School, UserSchoolLink
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.services.platform_essay_prompt import materialization_year

PROMPTS = "/api/v1/catalog/essay-prompts"


def _ident(user: str) -> ExternalIdentityContext:
    return ExternalIdentityContext(provider="test", external_user_id=user)


class PlatformEssayPromptsTeacherListingTests(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        self.factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

        async def _prep():
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        self.loop.run_until_complete(_prep())
        self.app = create_app()
        self.app.dependency_overrides[get_session_factory] = lambda: self.factory
        self.app.dependency_overrides[get_current_identity] = lambda: _ident("prof_r5")
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.app.dependency_overrides.clear()
        self.loop.run_until_complete(self.engine.dispose())
        self.loop.close()

    def _seed_school_and_teacher(self, code: str, user: str = "prof_r5"):
        async def _seed():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"PTL-{code}", name=f"school-{code}")
                session.add(school)
                await session.flush()
                session.add(UserSchoolLink(
                    external_user_id=user, school_id=school.id, role="TEACHER",
                    scope_type="SCHOOL", active=True,
                ))
                await session.commit()
                return school.id

        return self.loop.run_until_complete(_seed())

    def _seed_platform_prompt(self, title: str, status: str = "ACTIVE"):
        async def _seed():
            async with self.factory() as session:
                prompt = PlatformEssayPrompt(
                    id=uuid.uuid4(), title=title, statement=f"Enunciado de {title}.",
                    status=status, created_by_external_identity="user:ADMIN",
                )
                session.add(prompt)
                await session.commit()
                return prompt.id

        return self.loop.run_until_complete(_seed())

    def _as(self, user: str):
        self.app.dependency_overrides[get_current_identity] = lambda: _ident(user)

    def test_active_platform_prompts_appear_flagged_next_to_the_schools_own(self):
        self._seed_school_and_teacher("1")
        origin_id = self._seed_platform_prompt("Mobilidade urbana")
        own = self.client.post(
            PROMPTS, json={"title": "Minha proposta", "statement": "Disserte.", "year": 2026}
        )
        self.assertEqual(own.status_code, 201, own.text)

        rows = self.client.get(PROMPTS).json()
        by_title = {row["title"]: row for row in rows}
        self.assertEqual(set(by_title), {"Minha proposta", "Mobilidade urbana"})

        self.assertFalse(by_title["Minha proposta"]["is_platform"])
        self.assertIsNone(by_title["Minha proposta"]["platform_prompt_id"])

        platform_row = by_title["Mobilidade urbana"]
        self.assertTrue(platform_row["is_platform"])
        self.assertEqual(platform_row["platform_prompt_id"], str(origin_id))
        self.assertEqual(platform_row["id"], str(origin_id))
        self.assertEqual(platform_row["status"], "ACTIVE")
        self.assertEqual(platform_row["year"], materialization_year())

    def test_archived_platform_prompt_never_appears(self):
        self._seed_school_and_teacher("2")
        self._seed_platform_prompt("Arquivada", status="ARCHIVED")
        rows = self.client.get(PROMPTS).json()
        self.assertEqual(rows, [])

    def test_a_materialized_prompt_appears_once_still_flagged_as_platform(self):
        self._seed_school_and_teacher("3")
        origin_id = self._seed_platform_prompt("Adotada")

        detail = self.client.get(f"{PROMPTS}/{origin_id}").json()
        self.assertFalse(detail["materialized"])
        self.assertTrue(detail["is_platform"])
        self.assertEqual(detail["materials"], [])
        self.assertEqual(detail["assignments"], [])

        # Esta tarefa cobre so listagem/detalhe (a materializacao pela rota de
        # atribuicao e a Task 7), entao usamos o servico diretamente.
        async def _materialize():
            from agente_ia_edu.services.platform_essay_prompt import PlatformEssayPromptService
            async with self.factory() as session:
                school_id = (await session.execute(
                    select(School.id).where(School.code == "PTL-3")
                )).scalar_one()
                copy = await PlatformEssayPromptService(session).materialize_for_school(
                    platform_essay_prompt_id=origin_id, school_id=school_id,
                    created_by_external_identity="teacher:prof_r5",
                )
                copy_id = copy.id
                await session.commit()
                return copy_id

        copy_id = self.loop.run_until_complete(_materialize())

        rows = self.client.get(PROMPTS).json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], str(copy_id))
        self.assertTrue(rows[0]["is_platform"])
        self.assertEqual(rows[0]["platform_prompt_id"], str(origin_id))

    def test_detail_by_origin_id_returns_the_real_copy_once_materialized(self):
        self._seed_school_and_teacher("4")
        origin_id = self._seed_platform_prompt("Adotada 2")

        async def _materialize():
            from agente_ia_edu.services.platform_essay_prompt import PlatformEssayPromptService
            async with self.factory() as session:
                school_id = (await session.execute(
                    select(School.id).where(School.code == "PTL-4")
                )).scalar_one()
                copy = await PlatformEssayPromptService(session).materialize_for_school(
                    platform_essay_prompt_id=origin_id, school_id=school_id,
                    created_by_external_identity="teacher:prof_r5",
                )
                copy_id = copy.id
                await session.commit()
                return copy_id

        copy_id = self.loop.run_until_complete(_materialize())

        detail = self.client.get(f"{PROMPTS}/{origin_id}").json()
        self.assertEqual(detail["id"], str(copy_id))
        self.assertTrue(detail["materialized"])
        self.assertTrue(detail["is_platform"])

    def test_one_schools_adoption_does_not_hide_the_prompt_from_another_school(self):
        school_a = self._seed_school_and_teacher("5A", user="prof_a")
        self._seed_school_and_teacher("5B", user="prof_b")
        origin_id = self._seed_platform_prompt("Compartilhada")

        async def _materialize():
            from agente_ia_edu.services.platform_essay_prompt import PlatformEssayPromptService
            async with self.factory() as session:
                await PlatformEssayPromptService(session).materialize_for_school(
                    platform_essay_prompt_id=origin_id, school_id=school_a,
                    created_by_external_identity="teacher:prof_a",
                )
                await session.commit()

        self.loop.run_until_complete(_materialize())

        self._as("prof_b")
        rows = self.client.get(PROMPTS).json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], str(origin_id))
        self.assertTrue(rows[0]["is_platform"])


if __name__ == "__main__":
    unittest.main()
