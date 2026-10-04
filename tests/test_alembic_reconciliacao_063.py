"""A reconciliacao das duas linhagens Alembic, provada em PostgreSQL real.

O sintoma era "duas migrations 063". A forense mostrou que a bifurcacao e em
``055_essay_prompt_soft_delete``, com SETE e NOVE migrations correndo em
paralelo e reusando os numeros 057-063. As duas cadeias tocam tabelas
DISJUNTAS, entao um merge revision descreve a historia real.

O QUE ESTE ARQUIVO EXIGE
========================
Que um banco vindo de QUALQUER das duas linhagens - ou de lugar nenhum -
chegue ao mesmo schema. Nao basta "o upgrade nao explodiu": os tres caminhos
sao comparados tabela a tabela e coluna a coluna.

SEM SKIP SILENCIOSO
===================
Se o PostgreSQL nao estiver disponivel, estes testes FALHAM em vez de pular.
Um teste de migration que pula nao prova nada, e o bloco que pediu esta
reconciliacao disse explicitamente para nao aceitar skip como sucesso.
"""

from __future__ import annotations

import asyncio
import os
import unittest

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agente_ia_edu.services.curriculum_taxonomy import CurriculumTaxonomyService

RAMO_PLATAFORMA = "063_platform_material_target"
RAMO_CEREBRO = "065_classification_provenance"
MERGE = "066_merge_063_lineages"
BIFURCACAO = "055_essay_prompt_soft_delete"

# Antes de 024 e preciso semear o catalogo: ela e migration de DADOS e insere
# um no sob um pai que so o seed cria.
ANTES_DO_SEED = "023_curriculum_taxonomy"

# Tabelas CRIADAS por cada ramo - nao apenas tocadas. A distincao importa:
# `prompt_assignments` e alterada pela 062 do ramo plataforma mas foi criada
# bem antes da bifurcacao, entao ela sobrevive a um downgrade ate 055 e nao
# serve para provar que o ramo foi desfeito. (A primeira versao deste arquivo
# usava justamente ela, e o teste falhou com razao.)
TABELAS_PLATAFORMA = ("essay_batch_uploads", "essay_batch_pages",
                      "mass_correction_runs")
TABELAS_CEREBRO = ("knowledge_sources", "knowledge_chunks",
                   "knowledge_embedding_activations", "curriculum_bncc_links")


def _admin_url() -> str:
    u = os.getenv("POSTGRES_USER", "agenteedu")
    p = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
    return f"postgresql+psycopg://{u}:{p}@localhost:5433/postgres"


def _url(nome: str) -> str:
    u = os.getenv("POSTGRES_USER", "agenteedu")
    p = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
    return f"postgresql+psycopg://{u}:{p}@localhost:5433/{nome}"


def _admin(sql: str):
    motor = sa.create_engine(_admin_url(), isolation_level="AUTOCOMMIT")
    try:
        with motor.connect() as c:
            return c.execute(sa.text(sql))
    finally:
        motor.dispose()


async def _semear(url: str) -> None:
    engine = create_async_engine(url)
    try:
        fabrica = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with fabrica() as s:
            await CurriculumTaxonomyService(s).seed_reference_fixture()
    finally:
        await engine.dispose()


def _cfg(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _subir(url: str, ate: str) -> None:
    """Sobe ate `ate`, semeando o catalogo no ponto em que 024 precisa."""
    cfg = _cfg(url)
    command.upgrade(cfg, ANTES_DO_SEED)
    asyncio.run(_semear(url))
    command.upgrade(cfg, ate)


def _retrato(url: str) -> dict[str, set[str]]:
    """Tabela -> conjunto de colunas. E isto que precisa convergir."""
    motor = sa.create_engine(url)
    try:
        with motor.connect() as c:
            insp = sa.inspect(c)
            return {t: {col["name"] for col in insp.get_columns(t)}
                    for t in insp.get_table_names()
                    if t != "alembic_version"}
    finally:
        motor.dispose()


def _revisao(url: str) -> str:
    motor = sa.create_engine(url)
    try:
        with motor.connect() as c:
            return c.execute(sa.text("select version_num from alembic_version")).scalar()
    finally:
        motor.dispose()


class GrafoDeMigrationsTests(unittest.TestCase):
    """Nao precisa de banco: e leitura do grafo em disco."""

    def setUp(self):
        self.script = ScriptDirectory.from_config(_cfg("postgresql://x/y"))

    def test_existe_um_unico_head(self):
        heads = list(self.script.get_heads())
        self.assertEqual(heads, [MERGE],
                         f"esperava um head so, achei {heads}")

    def test_o_merge_declara_os_dois_ramos_como_pais(self):
        rev = self.script.get_revision(MERGE)
        self.assertEqual(set(rev.down_revision), {RAMO_PLATAFORMA, RAMO_CEREBRO})

    def test_o_merge_nao_tem_DDL(self):
        """Merge que mexe no schema esconde uma migration de verdade.

        Le o ARQUIVO, nao importa o modulo: migrations/versions nao e pacote
        Python, e o Alembic carrega as revisoes por caminho.
        """
        import ast
        import pathlib

        fonte = pathlib.Path(
            "migrations/versions/066_merge_063_lineages.py").read_text(
            encoding="utf-8")
        arvore = ast.parse(fonte)
        chamadas = [n for n in ast.walk(arvore)
                    if isinstance(n, ast.Attribute)
                    and isinstance(n.value, ast.Name) and n.value.id == "op"]
        self.assertEqual(chamadas, [],
                         "o merge revision tem DDL - deveria ser so historico")

    def test_as_duas_linhagens_bifurcam_no_mesmo_ponto(self):
        for ramo, primeiro in ((RAMO_PLATAFORMA, "057_essay_batch_upload"),
                               (RAMO_CEREBRO, "057_knowledge_engine_corpus")):
            with self.subTest(ramo=ramo):
                rev = self.script.get_revision(primeiro)
                self.assertEqual(rev.down_revision, BIFURCACAO)

    def test_064_e_065_continuam_no_ramo_cerebro(self):
        """Nao renumerei nem reescrevi ancestry: a 064 e a 065 seguem onde
        sempre estiveram."""
        self.assertEqual(
            self.script.get_revision("064_study_session_readiness").down_revision,
            "063_embedding_activation")
        self.assertEqual(
            self.script.get_revision("065_classification_provenance").down_revision,
            "064_study_session_readiness")


class ConvergenciaDeSchemaTests(unittest.TestCase):
    """Os tres caminhos tem de chegar ao mesmo schema. PostgreSQL de verdade."""

    bancos = {
        "A_vazio": "agente_reconc_a",
        "B_plataforma": "agente_reconc_b",
        "C_cerebro": "agente_reconc_c",
    }

    @classmethod
    def setUpClass(cls):
        try:
            _admin("SELECT 1")
        except Exception as exc:  # noqa: BLE001
            # NAO e skip: um teste de migration que pula nao prova nada.
            raise AssertionError(
                "PostgreSQL indisponivel na 5433 - a reconciliacao NAO foi "
                "verificada. Suba o banco antes de confiar neste arquivo."
            ) from exc

        cls.retratos: dict[str, dict[str, set[str]]] = {}
        for rotulo, banco in cls.bancos.items():
            _admin(f'DROP DATABASE IF EXISTS "{banco}"')
            _admin(f'CREATE DATABASE "{banco}"')

        # A: vazio -> head final, direto
        _subir(_url(cls.bancos["A_vazio"]), "heads")

        # B: percorre SO o ramo plataforma, depois reconcilia
        url_b = _url(cls.bancos["B_plataforma"])
        _subir(url_b, RAMO_PLATAFORMA)
        cls.revisao_b_antes = _revisao(url_b)
        cls.retrato_b_antes = _retrato(url_b)
        command.upgrade(_cfg(url_b), "heads")

        # C: percorre SO o ramo CEREBRO, depois reconcilia
        url_c = _url(cls.bancos["C_cerebro"])
        _subir(url_c, RAMO_CEREBRO)
        cls.revisao_c_antes = _revisao(url_c)
        command.upgrade(_cfg(url_c), "heads")

        for rotulo, banco in cls.bancos.items():
            cls.retratos[rotulo] = _retrato(_url(banco))

    @classmethod
    def tearDownClass(cls):
        for banco in cls.bancos.values():
            _admin(f'DROP DATABASE IF EXISTS "{banco}"')

    # -- CENARIO A ---------------------------------------------------------

    def test_A_banco_vazio_chega_ao_head(self):
        self.assertEqual(_revisao(_url(self.bancos["A_vazio"])), MERGE)

    def test_A_tem_as_tabelas_dos_DOIS_ramos(self):
        tabelas = set(self.retratos["A_vazio"])
        for t in TABELAS_PLATAFORMA + TABELAS_CEREBRO:
            self.assertIn(t, tabelas)

    # -- CENARIO B: veio da plataforma -------------------------------------

    def test_B_partiu_mesmo_do_ramo_plataforma(self):
        """Prova que o cenario e o que diz ser: antes de reconciliar, tinha as
        tabelas da plataforma e NAO tinha as do CEREBRO."""
        antes = set(self.retrato_b_antes)
        self.assertEqual(self.revisao_b_antes, RAMO_PLATAFORMA)
        for t in TABELAS_PLATAFORMA:
            self.assertIn(t, antes)
        for t in TABELAS_CEREBRO:
            self.assertNotIn(t, antes, f"{t} nao deveria existir ainda")

    def test_B_chega_ao_head_depois_da_reconciliacao(self):
        self.assertEqual(_revisao(_url(self.bancos["B_plataforma"])), MERGE)

    def test_B_ganhou_as_tabelas_do_outro_ramo(self):
        tabelas = set(self.retratos["B_plataforma"])
        for t in TABELAS_CEREBRO:
            self.assertIn(t, tabelas)

    # -- CENARIO C: veio do CEREBRO ----------------------------------------

    def test_C_partiu_mesmo_do_ramo_cerebro(self):
        self.assertEqual(self.revisao_c_antes, RAMO_CEREBRO)

    def test_C_chega_ao_head_depois_da_reconciliacao(self):
        self.assertEqual(_revisao(_url(self.bancos["C_cerebro"])), MERGE)

    # -- CONVERGENCIA ------------------------------------------------------

    def test_os_tres_caminhos_chegam_ao_MESMO_conjunto_de_tabelas(self):
        a = set(self.retratos["A_vazio"])
        b = set(self.retratos["B_plataforma"])
        c = set(self.retratos["C_cerebro"])
        self.assertEqual(a, b, f"A x B diferem em {a ^ b}")
        self.assertEqual(a, c, f"A x C diferem em {a ^ c}")

    def test_os_tres_caminhos_chegam_as_MESMAS_colunas(self):
        a, b, c = (self.retratos[k] for k in
                   ("A_vazio", "B_plataforma", "C_cerebro"))
        divergencias = []
        for tabela in sorted(set(a) | set(b) | set(c)):
            cols = {k: r.get(tabela, set()) for k, r in
                    (("A", a), ("B", b), ("C", c))}
            if cols["A"] != cols["B"] or cols["A"] != cols["C"]:
                divergencias.append(
                    f"{tabela}: A-B={cols['A'] ^ cols['B']} "
                    f"A-C={cols['A'] ^ cols['C']}")
        self.assertEqual(divergencias, [], "\n".join(divergencias))

    # -- o que a 064 e a 065 prometeram continua valendo -------------------

    def test_as_colunas_da_064_existem_nos_tres(self):
        for rotulo, retrato in self.retratos.items():
            with self.subTest(caminho=rotulo):
                cols = retrato.get("study_sessions", set())
                for c in ("readiness_route", "objective_assignment_id",
                          "objective_completed"):
                    self.assertIn(c, cols)

    def test_as_colunas_da_065_existem_nos_tres(self):
        for rotulo, retrato in self.retratos.items():
            with self.subTest(caminho=rotulo):
                cols = retrato.get("pedagogical_classifications", set())
                self.assertIn("provenance", cols)
                self.assertIn("validated_by_external_identity", cols)

    def test_a_trava_de_validacao_humana_continua_funcionando(self):
        """O CHECK da 065 nao pode ter se perdido na reconciliacao."""
        for rotulo, banco in self.bancos.items():
            with self.subTest(caminho=rotulo):
                motor = sa.create_engine(_url(banco))
                try:
                    with motor.begin() as c:
                        c.execute(sa.text(
                            "INSERT INTO questions (id, validation_status, "
                            "origin_type, status, visibility_scope, created_at, "
                            "updated_at) VALUES (gen_random_uuid(),'validated',"
                            "'GENERATED','PUBLISHED','PUBLIC',now(),now())"))
                        qid = c.execute(sa.text(
                            "SELECT id FROM questions LIMIT 1")).scalar()
                        c.execute(sa.text(
                            "INSERT INTO question_versions (id, question_id, "
                            "version_kind, canonical_text, statement, "
                            "content_hash, is_immutable, created_at) VALUES "
                            "(gen_random_uuid(),:q,'official_original','t','t',"
                            "gen_random_uuid()::text,true,now())"), {"q": qid})
                        vid = c.execute(sa.text(
                            "SELECT id FROM question_versions LIMIT 1")).scalar()
                    with motor.begin() as c:
                        with self.assertRaises(sa.exc.IntegrityError):
                            c.execute(sa.text(
                                "INSERT INTO pedagogical_classifications "
                                "(id, question_version_id, discipline, content, "
                                " subcontent, difficulty, reasoning_type, "
                                " prerequisites, keywords, competencies, skills, "
                                " status, source, lifecycle, provenance, "
                                " validated_by_external_identity, created_at) "
                                "VALUES (gen_random_uuid(), :v, 'D','C','S',"
                                "'MEDIUM','U','[]','[]','[]','[]','CLASSIFIED',"
                                "'ai','ACTIVE','HUMAN_VALIDATED', NULL, now())"),
                                {"v": vid})
                finally:
                    motor.dispose()

    # -- CENARIO D: ida e volta --------------------------------------------

    def test_D_descer_ate_a_bifurcacao_e_voltar_reconstroi_o_schema(self):
        """Ida e volta de verdade: desce os dois ramos inteiros ate o ponto em
        que eles se separam, e sobe de novo.

        `downgrade(MERGE-1)` seria ambiguo - a revisao tem DOIS pais, e o
        Alembic nao tem como saber por qual descer. Descer ate a BIFURCACAO
        e inequivoco, e testa o downgrade de todas as 16 migrations.
        """
        url = _url(self.bancos["A_vazio"])
        cfg = _cfg(url)
        antes = self.retratos["A_vazio"]
        try:
            command.downgrade(cfg, BIFURCACAO)
            self.assertEqual(_revisao(url), BIFURCACAO)
            depois_de_descer = set(_retrato(url))
            for t in TABELAS_PLATAFORMA + TABELAS_CEREBRO:
                self.assertNotIn(t, depois_de_descer,
                                 f"{t} sobreviveu ao downgrade")
        finally:
            command.upgrade(cfg, "heads")
        self.assertEqual(_revisao(url), MERGE)
        self.assertEqual(_retrato(url), antes,
                         "o schema nao voltou ao mesmo depois de descer e subir")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
