"""Modelo novo (platform_essay_prompts) + coluna de proveniencia em
essay_prompts.

SQLite in-memory, mesmo padrao de tests/test_r2_essay_proposal_service.py.
A unicidade de (school_id, materialized_from_platform_prompt_id) e testada
aqui no nivel de metadata; a validacao contra o banco real (Postgres) vem na
Task 2, porque so o upgrade de verdade prova que a constraint existe na
tabela ja existente.
"""

import unittest
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import EssayPrompt, PlatformEssayPrompt, School


class PlatformEssayPromptModelTests(unittest.IsolatedAsyncioTestCase):
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
        school = School(id=uuid.uuid4(), code=f"PEP-{code}", name=f"school-{code}")
        session.add(school)
        await session.flush()
        return school

    async def _origin(self, session, title="Tema da plataforma"):
        origin = PlatformEssayPrompt(
            id=uuid.uuid4(),
            title=title,
            statement="Disserte sobre o tema.",
            created_by_external_identity="user:ADMIN",
        )
        session.add(origin)
        await session.flush()
        return origin

    def _copy(self, *, school_id, origin_id, title="Tema da plataforma"):
        return EssayPrompt(
            id=uuid.uuid4(),
            school_id=school_id,
            title=title,
            statement="Disserte sobre o tema.",
            year=2026,
            status="ACTIVE",
            created_by_external_identity="teacher:p1",
            materialized_from_platform_prompt_id=origin_id,
        )

    async def test_platform_prompt_is_born_active_with_a_created_at(self):
        async with self.session_factory() as session:
            origin = await self._origin(session)
            self.assertEqual(origin.status, "ACTIVE")
            self.assertIsNotNone(origin.created_at)
            self.assertEqual(origin.created_by_external_identity, "user:ADMIN")

    async def test_a_normal_teacher_prompt_has_no_platform_origin(self):
        async with self.session_factory() as session:
            school = await self._school(session, "1")
            prompt = EssayPrompt(
                id=uuid.uuid4(), school_id=school.id, title="Proposta do professor",
                statement="Disserte.", year=2026, status="DRAFT",
                created_by_external_identity="teacher:p1",
            )
            session.add(prompt)
            await session.flush()
            self.assertIsNone(prompt.materialized_from_platform_prompt_id)

    async def test_the_same_school_cannot_materialize_the_same_origin_twice(self):
        async with self.session_factory() as session:
            school = await self._school(session, "2")
            origin = await self._origin(session)
            session.add(self._copy(school_id=school.id, origin_id=origin.id))
            await session.flush()
            session.add(self._copy(school_id=school.id, origin_id=origin.id))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_two_schools_materialize_the_same_origin_independently(self):
        async with self.session_factory() as session:
            school_a = await self._school(session, "3A")
            school_b = await self._school(session, "3B")
            origin = await self._origin(session)
            copy_a = self._copy(school_id=school_a.id, origin_id=origin.id)
            copy_b = self._copy(school_id=school_b.id, origin_id=origin.id)
            session.add_all([copy_a, copy_b])
            await session.flush()
            self.assertNotEqual(copy_a.id, copy_b.id)
            self.assertEqual(
                copy_a.materialized_from_platform_prompt_id,
                copy_b.materialized_from_platform_prompt_id,
            )

    async def test_two_normal_prompts_in_the_same_school_never_collide(self):
        # NULL nunca e igual a NULL para fins de unicidade - e por isso que a
        # UNIQUE comum basta, sem indice parcial (spec s2).
        async with self.session_factory() as session:
            school = await self._school(session, "4")
            for index in range(3):
                session.add(EssayPrompt(
                    id=uuid.uuid4(), school_id=school.id, title=f"Proposta {index}",
                    statement="Disserte.", year=2026, status="DRAFT",
                    created_by_external_identity="teacher:p1",
                ))
            await session.flush()  # nao deve levantar nada


if __name__ == "__main__":
    unittest.main()
