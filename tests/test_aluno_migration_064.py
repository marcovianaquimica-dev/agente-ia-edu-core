"""Migration 064 - verificada contra um PostgreSQL de verdade.

Por que isto nao e paranoia: ler a migration nao prova que ela roda. Esta
mesma migration passou na leitura e quebrou no banco real - o id de revisao
original tinha 34 caracteres e ``alembic_version.version_num`` e
``varchar(32)``. O DDL aplicava e o CARIMBO da versao estourava, deixando o
banco migrado mas marcado com a revisao ANTERIOR. Nenhum teste de SQLite
pegaria isso: o SQLite nao tem essa tabela com esse limite, e nao aplica
CheckConstraint com a mesma severidade.

O banco e DESCARTAVEL e construido aqui, pela cadeia real de migrations -
mesmo padrao de tests/test_phase7_assessment_postgresql_e2e.py. Nao depende
do banco de desenvolvimento estar em head, e nao escreve nele.
"""

from __future__ import annotations

import asyncio
import os
import unittest

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agente_ia_edu.services.curriculum_taxonomy import CurriculumTaxonomyService

REVISAO = "064_study_session_readiness"
ANTERIOR = "063_embedding_activation"

ROTAS_VALIDAS = ("DIRECT", "DIAGNOSTIC", "PREREQUISITE_PREPARATION")
COLUNAS = ("readiness_route", "objective_assignment_id", "objective_completed")

_INSERE = (
    "INSERT INTO study_sessions "
    "(id, student_external_id, source, session_date, timer_mode, "
    " break_minutes, effective_study_minutes, status, current_block_index, "
    " readiness_route, objective_assignment_id, objective_completed) "
    "VALUES (gen_random_uuid(), :aluno, 'STUDENT_DEFINED', '2026-01-01', "
    "'TIMED', 0, 0, 'SCHEDULED', 0, :rota, :alvo, :feito)"
)


class LimiteDoIdDeRevisaoTests(unittest.TestCase):
    """Roda sem banco nenhum: e aritmetica, e foi o defeito real."""

    def test_o_id_da_revisao_cabe_na_tabela_de_versao(self):
        self.assertLessEqual(
            len(REVISAO), 32,
            f"id com {len(REVISAO)} caracteres nao cabe em "
            "alembic_version.version_num (varchar 32): a migration aplica o "
            "DDL e falha ao carimbar a versao, deixando o banco inconsistente",
        )


def _admin_url() -> str:
    user = os.getenv("POSTGRES_USER", "agenteedu")
    senha = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
    return f"postgresql+psycopg://{user}:{senha}@localhost:5433/postgres"


def _url_do_banco(nome: str) -> str:
    user = os.getenv("POSTGRES_USER", "agenteedu")
    senha = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
    return f"postgresql+psycopg://{user}:{senha}@localhost:5433/{nome}"


async def _semear_catalogo(url_async: str) -> None:
    engine = create_async_engine(url_async)
    try:
        fabrica = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with fabrica() as s:
            await CurriculumTaxonomyService(s).seed_reference_fixture()
    finally:
        await engine.dispose()


class Migration064Tests(unittest.TestCase):
    banco = "agente_ia_edu_aluno_064_test"

    @classmethod
    def setUpClass(cls):
        cls.url = _url_do_banco(cls.banco)
        try:
            cls._admin("SELECT 1")
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest("PostgreSQL indisponivel na 5433") from exc
        cls._admin(f'DROP DATABASE IF EXISTS "{cls.banco}"')
        cls._admin(f'CREATE DATABASE "{cls.banco}"')
        # 024 e migration de DADOS e exige o no de catalogo semeado antes.
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", cls.url)
        command.upgrade(cfg, "023_curriculum_taxonomy")
        asyncio.run(_semear_catalogo(cls.url))
        command.upgrade(cfg, "head")
        cls.cfg = cfg
        cls.engine = sa.create_engine(cls.url)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "engine"):
            cls.engine.dispose()
        if hasattr(cls, "url"):
            cls._admin(f'DROP DATABASE IF EXISTS "{cls.banco}"')

    @classmethod
    def _admin(cls, sql: str):
        motor = sa.create_engine(_admin_url(), isolation_level="AUTOCOMMIT")
        try:
            with motor.connect() as c:
                return c.execute(sa.text(sql))
        finally:
            motor.dispose()

    def _colunas(self) -> dict[str, dict]:
        with self.engine.connect() as c:
            return {col["name"]: col for col in sa.inspect(c).get_columns("study_sessions")}

    # -- a migration chegou mesmo ao fim -----------------------------------

    def test_o_banco_ficou_carimbado_e_a_cadeia_passou_pela_064(self):
        """O defeito original passava NESTE ponto: DDL aplicado, carimbo nao.

        A primeira versao exigia `version_num == 064`, o que valia enquanto a
        064 era head. Quando a 065 entrou, o teste quebrou sem que nada
        estivesse errado - ele media "qual e a ultima migration" em vez de "a
        064 foi aplicada e carimbada". Agora exige que o carimbo exista e que
        as colunas da 064 estejam la, que e o que ele sempre quis dizer.
        """
        with self.engine.connect() as c:
            versao = c.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
        self.assertTrue(versao, "o banco nao ficou carimbado")
        self.assertLessEqual(len(versao), 32)
        faltando = [col for col in COLUNAS if col not in self._colunas()]
        self.assertEqual(faltando, [],
                         f"carimbado em {versao} mas sem as colunas da 064")

    def test_as_tres_colunas_existem(self):
        faltando = [c for c in COLUNAS if c not in self._colunas()]
        self.assertEqual(faltando, [], f"faltam {faltando}")

    def test_e_aditiva_de_verdade(self):
        # Linha antiga nao pode ter virado invalida.
        colunas = self._colunas()
        self.assertTrue(colunas["readiness_route"]["nullable"])
        self.assertTrue(colunas["objective_assignment_id"]["nullable"])
        self.assertFalse(colunas["objective_completed"]["nullable"])
        self.assertIsNotNone(
            colunas["objective_completed"]["default"],
            "coluna NOT NULL sem server_default quebraria as linhas existentes")

    # -- a trava ------------------------------------------------------------

    def test_o_check_recusa_uma_rota_inventada(self):
        with self.engine.begin() as c:
            with self.assertRaises(sa.exc.IntegrityError):
                c.execute(sa.text(_INSERE), {
                    "aluno": "x", "rota": "ROTA_QUE_NAO_EXISTE",
                    "alvo": None, "feito": False})

    def test_o_check_aceita_as_tres_rotas_e_NULL(self):
        for rota in (*ROTAS_VALIDAS, None):
            with self.engine.begin() as c:
                c.execute(sa.text(_INSERE), {
                    "aluno": f"stu-{rota}", "rota": rota, "alvo": None, "feito": False})
        with self.engine.connect() as c:
            total = c.execute(sa.text(
                "SELECT count(*) FROM study_sessions WHERE student_external_id LIKE 'stu-%'"
            )).scalar()
        self.assertEqual(total, 4)

    # -- a razao de existir --------------------------------------------------

    def test_distingue_nao_fez_de_esta_se_preparando(self):
        alvo = "11111111-1111-1111-1111-111111111111"
        with self.engine.begin() as c:
            for aluno, rota, feito in (
                ("prep-1", "PREREQUISITE_PREPARATION", False),
                ("prep-2", "PREREQUISITE_PREPARATION", False),
                ("feito-1", "DIRECT", True),
            ):
                c.execute(sa.text(_INSERE), {
                    "aluno": aluno, "rota": rota, "alvo": alvo, "feito": feito})
        with self.engine.connect() as c:
            preparando = c.execute(sa.text(
                "SELECT count(*) FROM study_sessions "
                "WHERE objective_assignment_id = :a AND objective_completed = false "
                "  AND readiness_route = 'PREREQUISITE_PREPARATION'"), {"a": alvo}).scalar()
            concluiram = c.execute(sa.text(
                "SELECT count(*) FROM study_sessions "
                "WHERE objective_assignment_id = :a AND objective_completed = true"),
                {"a": alvo}).scalar()
        self.assertEqual((preparando, concluiram), (2, 1),
                         "a consulta que Professor/Coordenacao farao nao funciona")

    # -- ida e volta ---------------------------------------------------------

    def test_downgrade_devolve_a_tabela_ao_estado_anterior(self):
        try:
            command.downgrade(self.cfg, ANTERIOR)
            sobraram = [c for c in COLUNAS if c in self._colunas()]
            self.assertEqual(sobraram, [], f"downgrade deixou {sobraram} para tras")
            with self.engine.connect() as c:
                self.assertEqual(
                    c.execute(sa.text("SELECT version_num FROM alembic_version")).scalar(),
                    ANTERIOR)
        finally:
            command.upgrade(self.cfg, "head")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
