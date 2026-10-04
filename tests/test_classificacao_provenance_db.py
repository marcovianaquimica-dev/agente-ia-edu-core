"""A proveniencia no banco, e o que a selecao de questoes enxerga.

Dois assuntos que so fazem sentido juntos:

1. HUMAN_VALIDATED e irrepresentavel sem uma pessoa. Nao por convencao: por
   CHECK constraint, como `curriculum_bncc_links` ja fazia para
   `status='VALIDATED'`. Um backfill distraido nao consegue carimbar
   validacao humana em material que ninguem olhou.

2. O que a `PracticeSelectionPolicy` ENXERGA. Aprovar uma classificacao sem
   que o aluno consiga receber a questao e inutil; deixar uma rejeitada
   visivel e pior que inutil.

O banco e descartavel e construido pela cadeia real de migrations.
"""

from __future__ import annotations

import asyncio
import os
import unittest
import uuid as _uuid

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agente_ia_edu.services.curriculum_taxonomy import CurriculumTaxonomyService

REVISAO = "065_classification_provenance"
PROVENIENCIAS = ("AI_SUGGESTED", "AI_VERIFIED", "HUMAN_VALIDATED")


def _admin_url() -> str:
    u = os.getenv("POSTGRES_USER", "agenteedu")
    p = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
    return f"postgresql+psycopg://{u}:{p}@localhost:5433/postgres"


def _url(nome: str) -> str:
    u = os.getenv("POSTGRES_USER", "agenteedu")
    p = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
    return f"postgresql+psycopg://{u}:{p}@localhost:5433/{nome}"


async def _semear_catalogo(url_async: str) -> None:
    engine = create_async_engine(url_async)
    try:
        fabrica = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with fabrica() as s:
            await CurriculumTaxonomyService(s).seed_reference_fixture()
    finally:
        await engine.dispose()


class ProvenanceNoBancoTests(unittest.TestCase):
    banco = "agente_ia_edu_provenance_test"

    @classmethod
    def setUpClass(cls):
        cls.url = _url(cls.banco)
        try:
            cls._admin("SELECT 1")
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest("PostgreSQL indisponivel na 5433") from exc
        cls._admin(f'DROP DATABASE IF EXISTS "{cls.banco}"')
        cls._admin(f'CREATE DATABASE "{cls.banco}"')
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

    def _colunas(self) -> dict:
        with self.engine.connect() as c:
            return {col["name"]: col
                    for col in sa.inspect(c).get_columns("pedagogical_classifications")}

    def _inserir(self, *, provenance, identidade=None, status="CLASSIFIED"):
        """Insere uma classificacao minima. Levanta IntegrityError se o banco
        recusar - que e o que varios testes abaixo esperam."""
        qv = self._question_version()
        with self.engine.begin() as c:
            c.execute(sa.text("""
                INSERT INTO pedagogical_classifications
                  (id, question_version_id, discipline, content, subcontent,
                   difficulty, reasoning_type, prerequisites, keywords,
                   competencies, skills, status, source, lifecycle,
                   provenance, validated_by_external_identity, created_at)
                VALUES (gen_random_uuid(), :qv, 'CURRICULUM_PROPOSAL',
                        'CHEMISTRY-PHYSICAL-STOICHIOMETRY',
                        'CHEMISTRY-PHYSICAL-STOICHIOMETRY', 'MEDIUM', 'U',
                        '[]', '[]', '[]', '[]', :st, 'ai', 'ACTIVE',
                        :prov, :ident, now())
            """), {"qv": qv, "st": status, "prov": provenance, "ident": identidade})

    def _question_version(self):
        """Uma QuestionVersion minima, so para satisfazer a FK."""
        with self.engine.begin() as c:
            qid = _uuid.uuid4()
            vid = _uuid.uuid4()
            c.execute(sa.text(
                "INSERT INTO questions (id, validation_status, origin_type, "
                "status, visibility_scope, created_at, updated_at) "
                "VALUES (:i,'validated','IMPORTED','PUBLISHED','PUBLIC',now(),now())"
                ), {"i": qid})
            c.execute(sa.text(
                "INSERT INTO question_versions (id, question_id, version_kind, "
                "canonical_text, statement, content_hash, is_immutable, "
                "created_at) "
                "VALUES (:v,:q,'official_original','t','t',:h,true,now())"),
                {"v": vid, "q": qid, "h": str(vid)})
            return vid

    # -- a migration chegou ao fim -----------------------------------------

    def test_o_banco_ficou_carimbado_na_revisao_nova(self):
        with self.engine.connect() as c:
            self.assertEqual(
                c.execute(sa.text("SELECT version_num FROM alembic_version")).scalar(),
                REVISAO)

    def test_as_colunas_existem_e_sao_aditivas(self):
        cols = self._colunas()
        self.assertIn("provenance", cols)
        self.assertIn("validated_by_external_identity", cols)
        self.assertFalse(cols["provenance"]["nullable"])
        self.assertIsNotNone(cols["provenance"]["default"],
                             "sem server_default as linhas existentes quebrariam")
        self.assertTrue(cols["validated_by_external_identity"]["nullable"])

    # -- A TRAVA ------------------------------------------------------------

    def test_validacao_humana_sem_pessoa_e_recusada_pelo_banco(self):
        with self.assertRaises(sa.exc.IntegrityError):
            self._inserir(provenance="HUMAN_VALIDATED", identidade=None)

    def test_validacao_humana_com_pessoa_e_aceita(self):
        self._inserir(provenance="HUMAN_VALIDATED", identidade="prof_marco")
        with self.engine.connect() as c:
            n = c.execute(sa.text(
                "SELECT count(*) FROM pedagogical_classifications "
                "WHERE provenance='HUMAN_VALIDATED' "
                "  AND validated_by_external_identity='prof_marco'")).scalar()
        self.assertEqual(n, 1)

    def test_ai_verified_NAO_exige_identidade(self):
        """Verificacao automatica e legitima e tem proveniencia propria. O que
        ela nao pode e se disfarcar de validacao humana."""
        self._inserir(provenance="AI_VERIFIED", identidade=None)

    def test_proveniencia_inventada_e_recusada(self):
        # Valor CURTO de proposito: um longo seria recusado pelo varchar(20)
        # antes de chegar ao CHECK, e o teste mediria o comprimento em vez da
        # trava.
        with self.assertRaises(sa.exc.IntegrityError):
            self._inserir(provenance="INVENTADO")

    def test_as_tres_proveniencias_cabem_na_coluna(self):
        """varchar(20) e apertado: HUMAN_VALIDATED ja usa 15."""
        for prov in PROVENIENCIAS:
            self.assertLessEqual(len(prov), 20, f"{prov} nao cabe na coluna")

    def test_as_tres_proveniencias_do_contrato_sao_aceitas(self):
        for prov in PROVENIENCIAS:
            with self.subTest(prov=prov):
                self._inserir(provenance=prov,
                              identidade="prof_marco" if prov == "HUMAN_VALIDATED" else None)

    # -- o que a selecao enxerga -------------------------------------------

    def test_aprovada_pela_IA_fica_visivel_para_a_selecao(self):
        """K: a selecao filtra por status='CLASSIFIED'. AI_VERIFIED com esse
        status TEM de aparecer, senao aprovar nao serve para nada."""
        self._inserir(provenance="AI_VERIFIED", status="CLASSIFIED")
        with self.engine.connect() as c:
            n = c.execute(sa.text(
                "SELECT count(*) FROM pedagogical_classifications "
                "WHERE status='CLASSIFIED' AND lifecycle='ACTIVE' "
                "  AND provenance='AI_VERIFIED'")).scalar()
        self.assertGreaterEqual(n, 1)

    def test_mandada_para_revisao_NAO_fica_visivel_para_a_selecao(self):
        """L: requires_review grava status='NEEDS_REVIEW', que o filtro
        `pc.status == 'CLASSIFIED'` do QuestionBankService nao alcanca."""
        self._inserir(provenance="AI_SUGGESTED", status="NEEDS_REVIEW")
        with self.engine.connect() as c:
            vazou = c.execute(sa.text(
                "SELECT count(*) FROM pedagogical_classifications "
                "WHERE status='CLASSIFIED' AND provenance='AI_SUGGESTED' "
                "  AND validated_by_external_identity IS NULL "
                "  AND id IN (SELECT id FROM pedagogical_classifications "
                "             WHERE status='NEEDS_REVIEW')")).scalar()
        self.assertEqual(vazou, 0)

    def test_sugerida_pela_IA_sozinha_nao_sai_aprovada_do_contrato(self):
        """AI_SUGGESTED e proposta, nao aprovacao.

        Esta propriedade e do SERVICO, nao do schema: o banco aceita
        AI_SUGGESTED com qualquer status, e seria errado travar isso - uma
        classificacao pode ser promovida depois por outro caminho. Quem
        garante e `decidir`, que so devolve AI_SUGGESTED junto com
        requires_review=True.

        (A primeira versao deste teste contava linhas AI_SUGGESTED com
        status CLASSIFIED no banco inteiro, e falhava por causa de linhas que
        OUTRO teste da mesma classe inseriu - media estado compartilhado, nao
        comportamento.)
        """
        from agente_ia_edu.services.classification_verification import (
            PROV_AI_SUGGESTED, ClassificacaoCandidata, ContratoDeAprovacao,
            Veredito, decidir,
        )

        d = decidir(
            ClassificacaoCandidata(question_version_id="q", primary="A"),
            Veredito(primary="B", evidencias=["x"]),
            taxonomia={"A", "B"}, contrato=ContratoDeAprovacao.default())
        self.assertEqual(d.provenance, PROV_AI_SUGGESTED)
        self.assertTrue(d.requires_review,
                        "AI_SUGGESTED saiu sem exigir revisao")

    # -- ida e volta --------------------------------------------------------

    def test_downgrade_devolve_a_tabela_ao_estado_anterior(self):
        try:
            command.downgrade(self.cfg, "064_study_session_readiness")
            cols = self._colunas()
            self.assertNotIn("provenance", cols)
            self.assertNotIn("validated_by_external_identity", cols)
        finally:
            command.upgrade(self.cfg, "head")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
