import unittest

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    EssayRubric,
    EssayRubricCompetency,
    EssayRubricLevel,
    EssayRubricScoringRule,
)
from agente_ia_edu.rubrics.loader import load_rubric_file
from agente_ia_edu.services.essay_rubric_seed import EssayRubricSeeder


class TestEssayRubricSeed(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self.rubric_file = load_rubric_file("enem_2025")

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_seeds_five_competencies_and_thirty_levels(self):
        async with self.session_factory() as session:
            await EssayRubricSeeder(session).seed(self.rubric_file)
            await session.commit()

            competencies = await session.scalar(
                select(func.count()).select_from(EssayRubricCompetency)
            )
            levels = await session.scalar(
                select(func.count()).select_from(EssayRubricLevel)
            )
            self.assertEqual(competencies, 5)
            self.assertEqual(levels, 30)

    async def test_seeding_twice_does_not_duplicate(self):
        async with self.session_factory() as session:
            seeder = EssayRubricSeeder(session)
            await seeder.seed(self.rubric_file)
            await session.commit()
            await seeder.seed(self.rubric_file)
            await session.commit()

            rubrics = await session.scalar(select(func.count()).select_from(EssayRubric))
            levels = await session.scalar(
                select(func.count()).select_from(EssayRubricLevel)
            )
            self.assertEqual(rubrics, 1)
            self.assertEqual(levels, 30)

    async def test_records_the_official_source_hash(self):
        async with self.session_factory() as session:
            rubric = await EssayRubricSeeder(session).seed(self.rubric_file)
            await session.commit()
            self.assertEqual(
                rubric.official_source_sha256,
                self.rubric_file.official_source_sha256,
            )
            self.assertEqual(rubric.status, "ACTIVE")

    async def test_a_superseded_rubric_stays_readable(self):
        async with self.session_factory() as session:
            seeder = EssayRubricSeeder(session)
            rubric = await seeder.seed(self.rubric_file)
            await session.commit()

            await seeder.supersede(rubric.rubric_version)
            await session.commit()

            stored = await session.scalar(
                select(EssayRubric).where(EssayRubric.rubric_version == "ENEM_2025")
            )
            self.assertEqual(stored.status, "SUPERSEDED")
            self.assertIsNotNone(stored.superseded_at)
            levels = await session.scalar(
                select(func.count()).select_from(EssayRubricLevel)
            )
            self.assertEqual(levels, 30)

    async def test_seeds_thirteen_scoring_rules_with_max_points_only_on_caps(self):
        async with self.session_factory() as session:
            await EssayRubricSeeder(session).seed(self.rubric_file)
            await session.commit()

            rules = (
                await session.scalars(select(EssayRubricScoringRule))
            ).all()
            self.assertEqual(len(rules), 13)

            by_effect = {}
            for rule in rules:
                by_effect.setdefault(rule.effect, []).append(rule)
            self.assertEqual(len(by_effect["ANULA_REDACAO"]), 10)
            self.assertEqual(len(by_effect["ZERA_COMPETENCIA"]), 1)
            self.assertEqual(len(by_effect["LIMITA_PONTUACAO"]), 2)

            for rule in rules:
                if rule.effect == "LIMITA_PONTUACAO":
                    self.assertIsNotNone(rule.max_points)
                else:
                    self.assertIsNone(rule.max_points)

            caps = {
                rule.competency_code: rule.max_points
                for rule in rules
                if rule.effect == "LIMITA_PONTUACAO"
            }
            self.assertEqual(caps, {"C3": 40, "C5": 40})


if __name__ == "__main__":
    unittest.main()
