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
from agente_ia_edu.rubrics.loader import load_rubric_file, parse_rubric_mapping
from agente_ia_edu.services.essay_rubric_seed import (
    EssayRubricSeeder,
    IncompleteRubricSeedError,
)


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

    async def test_a_level_with_a_non_default_provenance_is_carried_through_to_the_database(
        self,
    ):
        """LevelEntry.provenance defaults to OFICIAL_INEP, but the seeder must
        not overwrite an explicitly declared non-default value with a hardcoded
        literal - the exact gap the review flagged: every level used to be
        stamped OFICIAL_INEP regardless of what the rubric file declared, which
        made the mislabelling invisible to the provenance CHECK. This rubric is
        built by hand (not enem_2025.yaml, which must stay untouched) so the
        loader and the seeder can be exercised on a level that genuinely
        declares a non-default provenance."""
        custom = {
            "rubric_version": "CUSTOM_TEST_RUBRIC",
            "label": "régua de teste com proveniência não padrão",
            "competencies": [
                {
                    "code": code, "ordinal": i, "official_title": "t", "source_page": 1,
                    "levels": [
                        {
                            "points": p, "descriptor": "d", "source_page": 1,
                            **(
                                {"provenance": "INTERPRETACAO_PEDAGOGICA"}
                                if code == "C1" and p == 200
                                else {}
                            ),
                        }
                        for p in (0, 40, 80, 120, 160, 200)
                    ],
                    "signals": [],
                }
                for i, code in enumerate(["C1", "C2", "C3", "C4", "C5"], start=1)
            ],
            "scoring_rules": [],
        }
        rubric_file = parse_rubric_mapping(custom)

        # The loader itself must carry the declared value through, not silently
        # default it - and must still default the levels that say nothing.
        c1 = next(c for c in rubric_file.competencies if c.code == "C1")
        declared_level = next(level for level in c1.levels if level.points == 200)
        default_level = next(level for level in c1.levels if level.points == 160)
        self.assertEqual(declared_level.provenance, "INTERPRETACAO_PEDAGOGICA")
        self.assertEqual(default_level.provenance, "OFICIAL_INEP")

        async with self.session_factory() as session:
            await EssayRubricSeeder(session).seed(rubric_file)
            await session.commit()

            rubric = await session.scalar(
                select(EssayRubric).where(
                    EssayRubric.rubric_version == "CUSTOM_TEST_RUBRIC"
                )
            )
            competency = await session.scalar(
                select(EssayRubricCompetency).where(
                    EssayRubricCompetency.rubric_id == rubric.id,
                    EssayRubricCompetency.code == "C1",
                )
            )
            stored_declared = await session.scalar(
                select(EssayRubricLevel).where(
                    EssayRubricLevel.competency_id == competency.id,
                    EssayRubricLevel.points == 200,
                )
            )
            stored_default = await session.scalar(
                select(EssayRubricLevel).where(
                    EssayRubricLevel.competency_id == competency.id,
                    EssayRubricLevel.points == 160,
                )
            )
            self.assertEqual(stored_declared.provenance, "INTERPRETACAO_PEDAGOGICA")
            self.assertEqual(stored_default.provenance, "OFICIAL_INEP")

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

    async def test_a_bare_header_row_with_no_children_raises_on_reseed(self):
        async with self.session_factory() as session:
            session.add(
                EssayRubric(
                    rubric_version=self.rubric_file.rubric_version,
                    label=self.rubric_file.label,
                    effective_year=self.rubric_file.effective_year,
                    max_total_points=self.rubric_file.max_total_points,
                    official_source_title=self.rubric_file.official_source_title,
                    official_source_url=self.rubric_file.official_source_url,
                    official_source_sha256=self.rubric_file.official_source_sha256,
                    status="ACTIVE",
                )
            )
            await session.commit()

            with self.assertRaises(IncompleteRubricSeedError) as ctx:
                await EssayRubricSeeder(session).seed(self.rubric_file)

            message = str(ctx.exception)
            self.assertIn("ENEM_2025", message)
            self.assertIn("0", message)
            self.assertIn("5", message)

            # Nothing was written by the failed call, and the header row
            # from before the call is still the only rubric row present.
            rubrics = await session.scalar(select(func.count()).select_from(EssayRubric))
            competencies = await session.scalar(
                select(func.count()).select_from(EssayRubricCompetency)
            )
            self.assertEqual(rubrics, 1)
            self.assertEqual(competencies, 0)

    async def test_a_fully_seeded_rubric_passes_the_completeness_check(self):
        async with self.session_factory() as session:
            seeder = EssayRubricSeeder(session)
            rubric = await seeder.seed(self.rubric_file)
            await session.commit()

            # Re-seeding a complete rubric must not raise, and must still be
            # the silent no-op idempotence relies on.
            again = await seeder.seed(self.rubric_file)
            self.assertEqual(again.id, rubric.id)

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
