"""A reconciliacao das duas linhagens pos-064, provada em PostgreSQL real.

Mesmo padrao de tests/test_alembic_reconciliacao_063.py (que reconciliou a
bifurcacao anterior, em 055_essay_prompt_soft_delete) - este arquivo prova
a bifurcacao SEGUINTE, em 064_essay_batch_extracted_text:

    063_platform_material_target
     |
     +-- 064_essay_batch_extracted_text   -- motor de redacao (Quality
     |   065_essay_quality_gate              Gate/Zero Gate)
     |   066_essay_zero_gate
     |
     +-- [066_merge_063_lineages - reconciliacao ANTERIOR, ver aquele
     |    arquivo/aquele teste para o detalhe]
         067_guided_practice                 -- fase6/vetorial
         068_dialogue_continuity
     |
    069_merge_essay_vetorial   (esta revisao)

fase6/vetorial reconciliou sua propria bifurcacao interna ancorando o
merge em 063_platform_material_target - um passo ANTES de
064_essay_batch_extracted_text existir. Por isso nunca incorporou essa
revisao, e o motor de redacao (que encadeou normalmente a partir dela)
diverge dali.

O QUE ESTE ARQUIVO EXIGE
========================
Que um banco vindo de QUALQUER das duas linhagens - ou de lugar nenhum -
chegue ao mesmo schema. Nao basta "o upgrade nao explodiu": os tres
caminhos sao comparados tabela a tabela e coluna a coluna.

SEM SKIP SILENCIOSO
====================
Se o PostgreSQL nao estiver disponivel, estes testes FALHAM em vez de
pular - mesma convencao de test_alembic_reconciliacao_063.py.
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

RAMO_REDACAO = "066_essay_zero_gate"
RAMO_VETORIAL = "068_dialogue_continuity"
MERGE = "069_merge_essay_vetorial"
BIFURCACAO = "063_platform_material_target"

# Antes de 024 e preciso semear o catalogo: ela e migration de DADOS e insere
# um no sob um pai que so o seed cria (mesma razao de
# test_alembic_reconciliacao_063.py).
ANTES_DO_SEED = "023_curriculum_taxonomy"

# Colunas ADICIONADAS por cada ramo a tabelas que JA EXISTIAM antes da
# bifurcacao (essay_corrections e study_sessions/pedagogical_classifications
# sao todas anteriores a 063) - a distincao que importa, nao tabelas novas.
COLUNAS_REDACAO = {
    "essay_corrections": ("quality_gate_status", "quality_gate_version",
                          "zero_gate_decision", "zero_gate_version"),
}
# guided_practice_items E nova (criada em 067, do lado vetorial) - essa sim
# serve como tabela inteira para provar o ramo.
TABELA_VETORIAL = "guided_practice_items"


def _head_atual() -> str:
    heads = list(ScriptDirectory.from_config(_cfg("postgresql://x/y")).get_heads())
    assert len(heads) == 1, f"o grafo tem {len(heads)} heads: {heads}"
    return heads[0]


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
        self.assertEqual(len(heads), 1, f"esperava um head so, achei {heads}")

    def test_o_head_descende_do_merge(self):
        head = list(self.script.get_heads())[0]
        linhagem = {r.revision for r in self.script.walk_revisions("base", head)}
        self.assertIn(MERGE, linhagem,
                      "o head nao passa pela reconciliacao das duas linhagens")

    def test_o_merge_declara_os_dois_ramos_como_pais(self):
        rev = self.script.get_revision(MERGE)
        self.assertEqual(set(rev.down_revision), {RAMO_REDACAO, RAMO_VETORIAL})

    def test_o_merge_nao_tem_DDL(self):
        """Merge que mexe no schema esconde uma migration de verdade."""
        import ast
        import pathlib

        fonte = pathlib.Path(
            "migrations/versions/069_merge_essay_vetorial.py").read_text(
            encoding="utf-8")
        arvore = ast.parse(fonte)
        chamadas = [n for n in ast.walk(arvore)
                    if isinstance(n, ast.Attribute)
                    and isinstance(n.value, ast.Name) and n.value.id == "op"]
        self.assertEqual(chamadas, [],
                         "o merge revision tem DDL - deveria ser so historico")

    def test_064_essay_e_064_vetorial_bifurcam_no_mesmo_ponto(self):
        self.assertEqual(
            self.script.get_revision("064_essay_batch_extracted_text").down_revision,
            BIFURCACAO)
        # O lado vetorial nao passa por 064_essay_batch_extracted_text - ele
        # reconcilia sua PROPRIA bifurcacao interna (066_merge_063_lineages)
        # ainda ancorado em BIFURCACAO, nunca avancando para la.
        self.assertIn(
            BIFURCACAO,
            self.script.get_revision("066_merge_063_lineages").down_revision)

    def test_essay_quality_gate_e_zero_gate_continuam_onde_sempre_estiveram(self):
        """Nao renumerei nem reescrevi ancestry destas duas."""
        self.assertEqual(
            self.script.get_revision("065_essay_quality_gate").down_revision,
            "064_essay_batch_extracted_text")
        self.assertEqual(
            self.script.get_revision("066_essay_zero_gate").down_revision,
            "065_essay_quality_gate")


class ConvergenciaDeSchemaTests(unittest.TestCase):
    """Os tres caminhos tem de chegar ao mesmo schema. PostgreSQL de verdade."""

    bancos = {
        "A_vazio": "agente_reconc069_a",
        "B_redacao": "agente_reconc069_b",
        "C_vetorial": "agente_reconc069_c",
    }

    @classmethod
    def setUpClass(cls):
        try:
            _admin("SELECT 1")
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(
                "PostgreSQL indisponivel na 5433 - a reconciliacao NAO foi "
                "verificada. Suba o banco antes de confiar neste arquivo."
            ) from exc

        cls.retratos: dict[str, dict[str, set[str]]] = {}
        for _, banco in cls.bancos.items():
            _admin(f'DROP DATABASE IF EXISTS "{banco}"')
            _admin(f'CREATE DATABASE "{banco}"')

        # A: vazio -> head final, direto
        _subir(_url(cls.bancos["A_vazio"]), "heads")

        # B: percorre SO o ramo do motor de redacao, depois reconcilia
        url_b = _url(cls.bancos["B_redacao"])
        _subir(url_b, RAMO_REDACAO)
        cls.revisao_b_antes = _revisao(url_b)
        cls.retrato_b_antes = _retrato(url_b)
        command.upgrade(_cfg(url_b), "heads")

        # C: percorre SO o ramo vetorial (ja incluindo sua propria
        # reconciliacao interna em 066_merge_063_lineages), depois reconcilia
        url_c = _url(cls.bancos["C_vetorial"])
        _subir(url_c, RAMO_VETORIAL)
        cls.revisao_c_antes = _revisao(url_c)
        command.upgrade(_cfg(url_c), "heads")

        for rotulo, banco in cls.bancos.items():
            cls.retratos[rotulo] = _retrato(_url(banco))

    @classmethod
    def tearDownClass(cls):
        for banco in cls.bancos.values():
            _admin(f'DROP DATABASE IF EXISTS "{banco}"')

    def test_A_banco_vazio_chega_ao_head(self):
        self.assertEqual(_revisao(_url(self.bancos["A_vazio"])), _head_atual())

    def test_A_tem_colunas_da_redacao_e_tabela_vetorial(self):
        retrato = self.retratos["A_vazio"]
        for tabela, colunas in COLUNAS_REDACAO.items():
            for c in colunas:
                self.assertIn(c, retrato.get(tabela, set()))
        self.assertIn(TABELA_VETORIAL, retrato)

    def test_B_partiu_mesmo_do_ramo_redacao(self):
        self.assertEqual(self.revisao_b_antes, RAMO_REDACAO)
        antes = self.retrato_b_antes
        for c in COLUNAS_REDACAO["essay_corrections"]:
            self.assertIn(c, antes.get("essay_corrections", set()))
        self.assertNotIn(TABELA_VETORIAL, antes,
                         f"{TABELA_VETORIAL} nao deveria existir ainda")

    def test_B_chega_ao_head_depois_da_reconciliacao(self):
        self.assertEqual(_revisao(_url(self.bancos["B_redacao"])), _head_atual())

    def test_B_ganhou_a_tabela_do_outro_ramo(self):
        self.assertIn(TABELA_VETORIAL, self.retratos["B_redacao"])

    def test_C_partiu_mesmo_do_ramo_vetorial(self):
        self.assertEqual(self.revisao_c_antes, RAMO_VETORIAL)

    def test_C_chega_ao_head_depois_da_reconciliacao(self):
        self.assertEqual(_revisao(_url(self.bancos["C_vetorial"])), _head_atual())

    def test_C_ganhou_as_colunas_do_outro_ramo(self):
        retrato = self.retratos["C_vetorial"]
        for c in COLUNAS_REDACAO["essay_corrections"]:
            self.assertIn(c, retrato.get("essay_corrections", set()))

    def test_os_tres_caminhos_chegam_ao_MESMO_conjunto_de_tabelas(self):
        a = set(self.retratos["A_vazio"])
        b = set(self.retratos["B_redacao"])
        c = set(self.retratos["C_vetorial"])
        self.assertEqual(a, b, f"A x B diferem em {a ^ b}")
        self.assertEqual(a, c, f"A x C diferem em {a ^ c}")

    def test_os_tres_caminhos_chegam_as_MESMAS_colunas(self):
        a, b, c = (self.retratos[k] for k in
                   ("A_vazio", "B_redacao", "C_vetorial"))
        divergencias = []
        for tabela in sorted(set(a) | set(b) | set(c)):
            cols = {k: r.get(tabela, set()) for k, r in
                    (("A", a), ("B", b), ("C", c))}
            if cols["A"] != cols["B"] or cols["A"] != cols["C"]:
                divergencias.append(
                    f"{tabela}: A-B={cols['A'] ^ cols['B']} "
                    f"A-C={cols['A'] ^ cols['C']}")
        self.assertEqual(divergencias, [], "\n".join(divergencias))

    def test_o_check_constraint_do_quality_gate_sobreviveu(self):
        """A CHECK da 065 nao pode ter se perdido na reconciliacao."""
        for rotulo, banco in self.bancos.items():
            with self.subTest(caminho=rotulo):
                motor = sa.create_engine(_url(banco))
                try:
                    with motor.connect() as c:
                        inspector = sa.inspect(c)
                        checks = {chk["name"] for chk in
                                  inspector.get_check_constraints("essay_corrections")}
                        self.assertIn("ck_essay_corrections_quality_gate_status", checks)
                finally:
                    motor.dispose()

    def test_D_descer_ate_a_bifurcacao_e_voltar_reconstroi_o_schema(self):
        """Ida e volta: desce os dois ramos inteiros ate o ponto em que eles
        se separam (BIFURCACAO), e sobe de novo."""
        url = _url(self.bancos["A_vazio"])
        cfg = _cfg(url)
        antes = self.retratos["A_vazio"]
        try:
            command.downgrade(cfg, BIFURCACAO)
            self.assertEqual(_revisao(url), BIFURCACAO)
            depois_de_descer = _retrato(url)
            for c in COLUNAS_REDACAO["essay_corrections"]:
                self.assertNotIn(c, depois_de_descer.get("essay_corrections", set()),
                                 f"{c} sobreviveu ao downgrade")
            self.assertNotIn(TABELA_VETORIAL, depois_de_descer,
                             f"{TABELA_VETORIAL} sobreviveu ao downgrade")
        finally:
            command.upgrade(cfg, "heads")
        self.assertEqual(_revisao(url), _head_atual())
        self.assertEqual(_retrato(url), antes,
                         "o schema nao voltou ao mesmo depois de descer e subir")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
