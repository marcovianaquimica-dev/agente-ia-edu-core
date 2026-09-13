import unittest
import uuid

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    EssayRubric,
    EssayRubricCompetency,
    EssayRubricLevel,
    EssayRubricSignal,
    EssayRubricScoringRule,
)


class TestEssayRubricModels(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.execute(text("PRAGMA foreign_keys=ON"))
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _rubric(self, session) -> EssayRubric:
        rubric = EssayRubric(
            rubric_version="ENEM_2025",
            label="Matriz de Referência ENEM - Cartilha do Participante 2025",
            effective_year=2025,
            official_source_sha256="d8ab44dcbf5af808829d9dee89d23e7efa4f59df022b99102fac87489b870288",
            status="ACTIVE",
        )
        session.add(rubric)
        await session.flush()
        return rubric

    async def test_rubric_version_is_unique(self):
        async with self.session_factory() as session:
            await self._rubric(session)
            session.add(EssayRubric(rubric_version="ENEM_2025", label="duplicate"))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_level_points_outside_the_six_official_values_are_rejected(self):
        async with self.session_factory() as session:
            rubric = await self._rubric(session)
            competency = EssayRubricCompetency(
                rubric_id=rubric.id, code="C1", ordinal=1,
                official_title="Demonstrar domínio da modalidade escrita formal da língua portuguesa.",
                source_page=14,
            )
            session.add(competency)
            await session.flush()

            session.add(EssayRubricLevel(
                competency_id=competency.id, points=137,
                descriptor="valor inexistente na matriz", source_page=14,
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_level_with_an_unknown_provenance_is_rejected(self):
        async with self.session_factory() as session:
            rubric = await self._rubric(session)
            competency = EssayRubricCompetency(
                rubric_id=rubric.id, code="C1", ordinal=1,
                official_title="Demonstrar domínio da modalidade escrita formal da língua portuguesa.",
                source_page=14,
            )
            session.add(competency)
            await session.flush()

            session.add(EssayRubricLevel(
                competency_id=competency.id, points=200,
                descriptor="descritor qualquer", source_page=14,
                provenance="NAO_EXISTE",
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_official_signal_without_source_ref_is_rejected(self):
        async with self.session_factory() as session:
            rubric = await self._rubric(session)
            competency = EssayRubricCompetency(
                rubric_id=rubric.id, code="C2", ordinal=2,
                official_title="Compreender a proposta de redação...", source_page=22,
            )
            session.add(competency)
            await session.flush()

            session.add(EssayRubricSignal(
                competency_id=competency.id, key="repertorio_pertinencia",
                label="Pertinência do repertório", provenance="OFICIAL_INEP",
                source_ref=None,
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_engine_heuristic_without_rationale_is_rejected(self):
        async with self.session_factory() as session:
            rubric = await self._rubric(session)
            competency = EssayRubricCompetency(
                rubric_id=rubric.id, code="C3", ordinal=3,
                official_title="Selecionar, relacionar, organizar e interpretar...", source_page=30,
            )
            session.add(competency)
            await session.flush()

            session.add(EssayRubricSignal(
                competency_id=competency.id, key="progressao_tematica",
                label="Progressão temática", provenance="HEURISTICA_MOTOR",
                source_ref="decisão interna", rationale=None,
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_scoring_rule_records_its_effect(self):
        async with self.session_factory() as session:
            rubric = await self._rubric(session)
            session.add(EssayRubricScoringRule(
                rubric_id=rubric.id, key="fuga_ao_tema",
                label="Fuga ao tema", effect="ANULA_REDACAO",
                competency_code=None, source_page=9, provenance="OFICIAL_INEP",
            ))
            await session.flush()

    async def test_scoring_rule_with_an_unknown_provenance_is_rejected(self):
        async with self.session_factory() as session:
            rubric = await self._rubric(session)
            session.add(EssayRubricScoringRule(
                rubric_id=rubric.id, key="fuga_ao_tema",
                label="Fuga ao tema", effect="ANULA_REDACAO",
                competency_code=None, source_page=9, provenance="NAO_EXISTE",
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_limita_pontuacao_rule_accepts_an_official_max_points(self):
        async with self.session_factory() as session:
            rubric = await self._rubric(session)
            session.add(EssayRubricScoringRule(
                rubric_id=rubric.id, key="tangenciamento_teto_c3",
                label="Tangenciamento ao tema - teto na Competência III",
                effect="LIMITA_PONTUACAO", competency_code="C3", max_points=40,
                source_page=27, provenance="OFICIAL_INEP",
            ))
            await session.flush()

    async def test_limita_pontuacao_rule_without_max_points_is_rejected(self):
        async with self.session_factory() as session:
            rubric = await self._rubric(session)
            session.add(EssayRubricScoringRule(
                rubric_id=rubric.id, key="teto_sem_max_points",
                label="teto sem max_points", effect="LIMITA_PONTUACAO",
                competency_code="C3", max_points=None,
                source_page=27, provenance="OFICIAL_INEP",
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_limita_pontuacao_rule_rejects_an_off_scale_max_points(self):
        async with self.session_factory() as session:
            rubric = await self._rubric(session)
            session.add(EssayRubricScoringRule(
                rubric_id=rubric.id, key="teto_fora_da_escala",
                label="teto fora da escala", effect="LIMITA_PONTUACAO",
                competency_code="C3", max_points=137,
                source_page=27, provenance="OFICIAL_INEP",
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_non_limita_pontuacao_rule_with_max_points_is_rejected(self):
        async with self.session_factory() as session:
            rubric = await self._rubric(session)
            session.add(EssayRubricScoringRule(
                rubric_id=rubric.id, key="anula_com_max_points",
                label="anula com max_points indevido", effect="ANULA_REDACAO",
                competency_code=None, max_points=40,
                source_page=9, provenance="OFICIAL_INEP",
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()
