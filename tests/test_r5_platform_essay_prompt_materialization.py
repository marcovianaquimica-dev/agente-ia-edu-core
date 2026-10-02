"""Materializacao de uma proposta da plataforma numa escola (spec s4).

O contrato inteiro do fluxo novo esta aqui: buscar-ou-criar idempotente,
isolamento entre escolas e o que entra/sai da lista de disponiveis.
"""

import unittest
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import EssayPrompt, School
from agente_ia_edu.services.platform_essay_prompt import (
    PlatformEssayPromptService,
    materialization_year,
)


class PlatformEssayPromptMaterializationTests(unittest.IsolatedAsyncioTestCase):
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

    async def _school(self, session, code):
        school = School(id=uuid.uuid4(), code=f"PMAT-{code}", name=f"school-{code}")
        session.add(school)
        await session.flush()
        return school

    async def test_materialize_copies_title_and_statement_into_the_school(self):
        async with self.session_factory() as session:
            svc = PlatformEssayPromptService(session)
            school = await self._school(session, "1")
            origin = await svc.create_prompt(
                title="Mobilidade urbana", statement="A partir dos textos, disserte.",
                created_by_external_identity="user:ADMIN",
            )
            copy = await svc.materialize_for_school(
                platform_essay_prompt_id=origin.id, school_id=school.id,
                created_by_external_identity="teacher:p1",
            )
            self.assertEqual(copy.school_id, school.id)
            self.assertEqual(copy.title, origin.title)
            self.assertEqual(copy.statement, origin.statement)
            self.assertEqual(copy.status, "ACTIVE")
            self.assertEqual(copy.year, materialization_year())
            self.assertEqual(copy.materialized_from_platform_prompt_id, origin.id)
            self.assertFalse(copy.is_free_theme)
            self.assertIsNone(copy.deleted_at)

    async def test_materializing_twice_in_the_same_school_reuses_the_same_row(self):
        async with self.session_factory() as session:
            svc = PlatformEssayPromptService(session)
            school = await self._school(session, "2")
            origin = await svc.create_prompt(
                title="Tema", statement="s", created_by_external_identity="user:ADMIN"
            )
            first = await svc.materialize_for_school(
                platform_essay_prompt_id=origin.id, school_id=school.id,
                created_by_external_identity="teacher:p1",
            )
            second = await svc.materialize_for_school(
                platform_essay_prompt_id=origin.id, school_id=school.id,
                created_by_external_identity="teacher:p2",
            )
            self.assertEqual(first.id, second.id)

            rows = (await session.execute(
                select(EssayPrompt).where(
                    EssayPrompt.school_id == school.id,
                    EssayPrompt.materialized_from_platform_prompt_id == origin.id,
                )
            )).scalars().all()
            self.assertEqual(len(rows), 1)

    async def test_two_schools_get_independent_copies(self):
        async with self.session_factory() as session:
            svc = PlatformEssayPromptService(session)
            school_a = await self._school(session, "3A")
            school_b = await self._school(session, "3B")
            origin = await svc.create_prompt(
                title="Tema", statement="s", created_by_external_identity="user:ADMIN"
            )
            copy_a = await svc.materialize_for_school(
                platform_essay_prompt_id=origin.id, school_id=school_a.id,
                created_by_external_identity="teacher:a",
            )
            copy_b = await svc.materialize_for_school(
                platform_essay_prompt_id=origin.id, school_id=school_b.id,
                created_by_external_identity="teacher:b",
            )
            self.assertNotEqual(copy_a.id, copy_b.id)
            self.assertEqual(copy_a.school_id, school_a.id)
            self.assertEqual(copy_b.school_id, school_b.id)

    async def test_materialize_unknown_origin_raises_value_error(self):
        async with self.session_factory() as session:
            svc = PlatformEssayPromptService(session)
            school = await self._school(session, "4")
            with self.assertRaises(ValueError):
                await svc.materialize_for_school(
                    platform_essay_prompt_id=uuid.uuid4(), school_id=school.id,
                    created_by_external_identity="teacher:p1",
                )

    async def test_get_platform_prompt_returns_none_for_a_normal_essay_prompt_id(self):
        async with self.session_factory() as session:
            svc = PlatformEssayPromptService(session)
            school = await self._school(session, "5")
            own = EssayPrompt(
                id=uuid.uuid4(), school_id=school.id, title="Propria", statement="s",
                year=2026, status="DRAFT", created_by_external_identity="teacher:p1",
            )
            session.add(own)
            await session.flush()
            self.assertIsNone(await svc.get_platform_prompt(own.id))

    async def test_available_list_hides_archived_and_already_materialized(self):
        async with self.session_factory() as session:
            svc = PlatformEssayPromptService(session)
            school = await self._school(session, "6")
            other_school = await self._school(session, "6B")

            free = await svc.create_prompt(
                title="Disponivel", statement="s", created_by_external_identity="user:ADMIN"
            )
            free.created_at = datetime(2026, 9, 3, tzinfo=timezone.utc)
            archived = await svc.create_prompt(
                title="Arquivada", statement="s", created_by_external_identity="user:ADMIN"
            )
            archived.created_at = datetime(2026, 9, 2, tzinfo=timezone.utc)
            mine = await svc.create_prompt(
                title="Ja materializada", statement="s", created_by_external_identity="user:ADMIN"
            )
            mine.created_at = datetime(2026, 9, 1, tzinfo=timezone.utc)
            await svc.archive_prompt(platform_essay_prompt_id=archived.id)
            await svc.materialize_for_school(
                platform_essay_prompt_id=mine.id, school_id=school.id,
                created_by_external_identity="teacher:p1",
            )
            # Materializada por OUTRA escola nao afeta esta.
            await svc.materialize_for_school(
                platform_essay_prompt_id=free.id, school_id=other_school.id,
                created_by_external_identity="teacher:o",
            )

            available = await svc.list_available_for_school(school_id=school.id)
            self.assertEqual([p.title for p in available], ["Disponivel"])

    async def test_find_materialized_is_scoped_to_the_school(self):
        async with self.session_factory() as session:
            svc = PlatformEssayPromptService(session)
            school_a = await self._school(session, "7A")
            school_b = await self._school(session, "7B")
            origin = await svc.create_prompt(
                title="Tema", statement="s", created_by_external_identity="user:ADMIN"
            )
            await svc.materialize_for_school(
                platform_essay_prompt_id=origin.id, school_id=school_a.id,
                created_by_external_identity="teacher:a",
            )
            self.assertIsNotNone(await svc.find_materialized(
                platform_essay_prompt_id=origin.id, school_id=school_a.id
            ))
            self.assertIsNone(await svc.find_materialized(
                platform_essay_prompt_id=origin.id, school_id=school_b.id
            ))


if __name__ == "__main__":
    unittest.main()
