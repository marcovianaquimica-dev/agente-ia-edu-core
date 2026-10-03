"""PlatformEssayPromptService - lado do admin (criar / listar / arquivar).

SQLite in-memory, mesmo padrao de tests/test_r2_essay_proposal_service.py.
"""

import unittest
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import EssayPrompt, PromptMaterial, School
from agente_ia_edu.services.platform_essay_prompt import (
    PlatformEssayPromptService,
    materialization_year,
)


class PlatformEssayPromptServiceTests(unittest.IsolatedAsyncioTestCase):
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
        school = School(id=uuid.uuid4(), code=f"PSVC-{code}", name=f"school-{code}")
        session.add(school)
        await session.flush()
        return school

    async def test_create_prompt_is_born_active(self):
        async with self.session_factory() as session:
            svc = PlatformEssayPromptService(session)
            prompt = await svc.create_prompt(
                title="Mobilidade urbana",
                statement="A partir dos textos, disserte.",
                created_by_external_identity="user:ADMIN",
            )
            self.assertEqual(prompt.status, "ACTIVE")
            self.assertEqual(prompt.title, "Mobilidade urbana")
            self.assertEqual(prompt.created_by_external_identity, "user:ADMIN")

    async def test_list_returns_archived_too_newest_first(self):
        async with self.session_factory() as session:
            svc = PlatformEssayPromptService(session)
            older = await svc.create_prompt(
                title="Antiga", statement="s", created_by_external_identity="user:ADMIN"
            )
            older.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
            newer = await svc.create_prompt(
                title="Recente", statement="s", created_by_external_identity="user:ADMIN"
            )
            newer.created_at = datetime(2026, 9, 1, tzinfo=timezone.utc)
            await svc.archive_prompt(platform_essay_prompt_id=older.id)

            listed = await svc.list_with_materialization_counts()
            self.assertEqual([p.title for p, _ in listed], ["Recente", "Antiga"])
            self.assertEqual([p.status for p, _ in listed], ["ACTIVE", "ARCHIVED"])

    async def test_list_counts_how_many_schools_materialized_each_prompt(self):
        async with self.session_factory() as session:
            svc = PlatformEssayPromptService(session)
            used = await svc.create_prompt(
                title="Usada", statement="s", created_by_external_identity="user:ADMIN"
            )
            unused = await svc.create_prompt(
                title="Nunca usada", statement="s", created_by_external_identity="user:ADMIN"
            )
            school_a = await self._school(session, "A")
            school_b = await self._school(session, "B")
            for school in (school_a, school_b):
                session.add(EssayPrompt(
                    id=uuid.uuid4(), school_id=school.id, title="Usada", statement="s",
                    year=2026, status="ACTIVE", created_by_external_identity="teacher:p1",
                    materialized_from_platform_prompt_id=used.id,
                ))
            # Uma proposta normal do professor nunca entra na contagem.
            session.add(EssayPrompt(
                id=uuid.uuid4(), school_id=school_a.id, title="Propria", statement="s",
                year=2026, status="DRAFT", created_by_external_identity="teacher:p1",
            ))
            await session.flush()

            counts = {p.id: count for p, count in await svc.list_with_materialization_counts()}
            self.assertEqual(counts[used.id], 2)
            self.assertEqual(counts[unused.id], 0)

    async def test_archive_prompt_flips_status_and_leaves_copies_untouched(self):
        async with self.session_factory() as session:
            svc = PlatformEssayPromptService(session)
            origin = await svc.create_prompt(
                title="Tema", statement="s", created_by_external_identity="user:ADMIN"
            )
            school = await self._school(session, "C")
            copy = EssayPrompt(
                id=uuid.uuid4(), school_id=school.id, title="Tema", statement="s",
                year=2026, status="ACTIVE", created_by_external_identity="teacher:p1",
                materialized_from_platform_prompt_id=origin.id,
            )
            session.add(copy)
            await session.flush()

            archived = await svc.archive_prompt(platform_essay_prompt_id=origin.id)
            self.assertEqual(archived.status, "ARCHIVED")

            refreshed = await session.get(EssayPrompt, copy.id)
            self.assertEqual(refreshed.status, "ACTIVE")
            self.assertIsNone(refreshed.deleted_at)

    async def test_archive_unknown_prompt_raises_value_error(self):
        async with self.session_factory() as session:
            svc = PlatformEssayPromptService(session)
            with self.assertRaises(ValueError):
                await svc.archive_prompt(platform_essay_prompt_id=uuid.uuid4())

    async def test_materialization_year_is_the_current_utc_year(self):
        self.assertEqual(materialization_year(), datetime.now(timezone.utc).year)

    async def test_add_material_attaches_a_file_to_the_platform_prompt(self):
        async with self.session_factory() as session:
            svc = PlatformEssayPromptService(session)
            prompt = await svc.create_prompt(
                title="Tema", statement="s", created_by_external_identity="user:ADMIN"
            )
            material = await svc.add_material(
                platform_essay_prompt_id=prompt.id, material_type="FILE",
                storage_uri="var/materials/x.pdf", position=0,
            )
            self.assertEqual(material.platform_essay_prompt_id, prompt.id)
            self.assertIsNone(material.essay_prompt_id)
            self.assertEqual(material.storage_uri, "var/materials/x.pdf")

    async def test_add_material_rejects_text_without_content(self):
        async with self.session_factory() as session:
            svc = PlatformEssayPromptService(session)
            prompt = await svc.create_prompt(
                title="Tema", statement="s", created_by_external_identity="user:ADMIN"
            )
            with self.assertRaises(ValueError):
                await svc.add_material(
                    platform_essay_prompt_id=prompt.id, material_type="TEXT", position=0,
                )

    async def test_add_material_to_unknown_prompt_raises_value_error(self):
        async with self.session_factory() as session:
            svc = PlatformEssayPromptService(session)
            with self.assertRaises(ValueError):
                await svc.add_material(
                    platform_essay_prompt_id=uuid.uuid4(), material_type="FILE",
                    storage_uri="x.pdf", position=0,
                )

    async def test_add_material_twice_at_the_same_position_raises_value_error(self):
        async with self.session_factory() as session:
            svc = PlatformEssayPromptService(session)
            prompt = await svc.create_prompt(
                title="Tema", statement="s", created_by_external_identity="user:ADMIN"
            )
            await svc.add_material(
                platform_essay_prompt_id=prompt.id, material_type="FILE",
                storage_uri="a.pdf", position=0,
            )
            with self.assertRaises(ValueError):
                await svc.add_material(
                    platform_essay_prompt_id=prompt.id, material_type="FILE",
                    storage_uri="b.pdf", position=0,
                )

    async def test_list_materials_returns_them_ordered_by_position(self):
        async with self.session_factory() as session:
            svc = PlatformEssayPromptService(session)
            prompt = await svc.create_prompt(
                title="Tema", statement="s", created_by_external_identity="user:ADMIN"
            )
            await svc.add_material(
                platform_essay_prompt_id=prompt.id, material_type="FILE",
                storage_uri="b.pdf", position=1,
            )
            await svc.add_material(
                platform_essay_prompt_id=prompt.id, material_type="FILE",
                storage_uri="a.pdf", position=0,
            )
            materials = await svc.list_materials(platform_essay_prompt_id=prompt.id)
            self.assertEqual([m.storage_uri for m in materials], ["a.pdf", "b.pdf"])

    async def test_materialize_for_school_copies_the_platform_prompts_materials(self):
        """A copia da escola precisa ver o mesmo material anexado na origem -
        reaproveita o mesmo storage_uri (storage e content-addressed, nao
        precisa duplicar o arquivo fisico), nao os IDs das linhas."""
        async with self.session_factory() as session:
            svc = PlatformEssayPromptService(session)
            origin = await svc.create_prompt(
                title="Tema", statement="s", created_by_external_identity="user:ADMIN"
            )
            await svc.add_material(
                platform_essay_prompt_id=origin.id, material_type="FILE",
                storage_uri="var/materials/origem.pdf", position=0,
            )
            school = await self._school(session, "MAT")
            copy = await svc.materialize_for_school(
                platform_essay_prompt_id=origin.id, school_id=school.id,
                created_by_external_identity="teacher:p1",
            )
            copied_materials = (await session.execute(
                select(PromptMaterial).where(PromptMaterial.essay_prompt_id == copy.id)
            )).scalars().all()
            self.assertEqual(len(copied_materials), 1)
            self.assertEqual(copied_materials[0].storage_uri, "var/materials/origem.pdf")
            self.assertEqual(copied_materials[0].material_type, "FILE")
            self.assertIsNone(copied_materials[0].platform_essay_prompt_id)

    async def test_materialize_for_school_is_idempotent_and_does_not_duplicate_materials(self):
        async with self.session_factory() as session:
            svc = PlatformEssayPromptService(session)
            origin = await svc.create_prompt(
                title="Tema", statement="s", created_by_external_identity="user:ADMIN"
            )
            await svc.add_material(
                platform_essay_prompt_id=origin.id, material_type="FILE",
                storage_uri="var/materials/origem.pdf", position=0,
            )
            school = await self._school(session, "IDEMP")
            first = await svc.materialize_for_school(
                platform_essay_prompt_id=origin.id, school_id=school.id,
                created_by_external_identity="teacher:p1",
            )
            second = await svc.materialize_for_school(
                platform_essay_prompt_id=origin.id, school_id=school.id,
                created_by_external_identity="teacher:p1",
            )
            self.assertEqual(first.id, second.id)
            copied_materials = (await session.execute(
                select(PromptMaterial).where(PromptMaterial.essay_prompt_id == first.id)
            )).scalars().all()
            self.assertEqual(len(copied_materials), 1)


if __name__ == "__main__":
    unittest.main()
