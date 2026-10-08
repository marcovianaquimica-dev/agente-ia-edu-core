"""A SELEÇÃO DO ITEM L0 — com os dois acervos publicados no mesmo conteúdo.

POR QUE ESTE ARQUIVO PRECISA DE BANCO
======================================
`test_instrumento_de_sondagem` prova o contrato sobre candidatos
construídos à mão. Ele passaria com o produto quebrado em dois pontos que
só aparecem com o acervo real carregado:

1. se o carregador não trouxesse a finalidade de cada item, todo candidato
   nasceria com `finalidade=None` e NENHUM seria elegível — nem probe, nem
   verificação;
2. se a seleção lesse o conteúdo inteiro sem separar as finalidades, a
   verificação devolveria o item da sondagem, que é o que o aluno já errou.

Então aqui os DOIS publicadores rodam no mesmo banco, no mesmo nó
curricular, e as perguntas são feitas de verdade.
"""

from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import unittest
import uuid as _uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import CatalogNode, Question, QuestionVersion
from agente_ia_edu.services.grafo_estequiometria import (
    CONTEUDO,
    LEITURA_FORMULA,
    MASSA_MOLAR,
)
from agente_ia_edu.services.instrumento_de_sondagem import ORIGEM_CURADA
from agente_ia_edu.services.seletor_de_sondagem import SeletorDeSondagem
from agente_ia_edu.services.verificacao_da_habilidade import (
    ids_de,
    itens_para_verificar,
    respondidas,
)
from agente_ia_edu.services.verificacao_estequiometria import (
    ITENS as ITENS_VER,
)

ALUNO = "aluno_qa_verificacao"


_RAIZ = pathlib.Path(__file__).resolve().parent.parent


def _carregar(nome: str, caminho: str):
    spec = importlib.util.spec_from_file_location(nome, _RAIZ / caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pub_sondagem = _carregar("pub_sond",
                         "scripts/publicar_sondagem_estequiometria.py")
pub_verificacao = _carregar("pub_ver",
                            "scripts/publicar_verificacao_estequiometria.py")


class _Base(unittest.TestCase):

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                          poolclass=StaticPool)
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession,
                                          expire_on_commit=False)

        async def prep():
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with self.factory() as s:
                disc = CatalogNode(id=_uuid.uuid4(), code="CHEMISTRY",
                                   name="Quimica", node_type="DISCIPLINE",
                                   position=0, active=True)
                s.add(disc)
                await s.flush()
                disc.root_id = disc.id
                s.add(CatalogNode(id=_uuid.uuid4(), code=CONTEUDO,
                                  name="Estequiometria", node_type="CONTENT",
                                  position=0, parent_id=disc.id,
                                  root_id=disc.id, active=True))
                await s.commit()
            # OS DOIS ACERVOS, no mesmo conteudo. E assim no banco real.
            async with self.factory() as s:
                await pub_sondagem.publicar(s)
            async with self.factory() as s:
                await pub_verificacao.publicar(s)

        self.loop.run_until_complete(prep())

    def tearDown(self):
        self.loop.run_until_complete(self.engine.dispose())
        self.loop.close()

    def _rodar(self, corrotina_de):
        async def run():
            async with self.factory() as s:
                return await corrotina_de(SeletorDeSondagem(s))
        return self.loop.run_until_complete(run())

    def _versoes_por_chave(self) -> dict[str, str]:
        """{sondagem_key|verificacao_key: question_version_id}"""
        async def run():
            async with self.factory() as s:
                saida = {}
                for q in (await s.execute(select(Question))).scalars().all():
                    md = q.metadata_ or {}
                    chave = md.get("verificacao_key") or md.get("sondagem_key")
                    v = (await s.execute(select(QuestionVersion).where(
                        QuestionVersion.question_id == q.id))).scalars().first()
                    saida[chave] = str(v.id)
                return saida
        return self.loop.run_until_complete(run())


class OCARREGADORTRAZASFINALIDADES(_Base):
    """Sem isto, nenhum item seria elegível e o teste de contrato não veria."""

    def test_os_dois_acervos_estao_no_mesmo_conteudo(self):
        candidatos = self._rodar(lambda sv: sv.candidatos(CONTEUDO))
        self.assertGreaterEqual(len(candidatos), 5 + len(ITENS_VER))

    def test_as_duas_finalidades_aparecem_entre_os_candidatos(self):
        candidatos = self._rodar(lambda sv: sv.candidatos(CONTEUDO))
        self.assertEqual({"PROBE", "VERIFICATION"},
                         {c.finalidade for c in candidatos if c.finalidade})


class ASONDAGEMNAOVEOSITENSDEVERIFICACAO(_Base):
    """O diagnóstico continua medindo com o item escrito para ele."""

    def test_o_instrumento_de_massa_molar_e_o_da_sondagem(self):
        escolhas = self._rodar(lambda sv: sv.instrumentos(
            conteudo=CONTEUDO, habilidades=[MASSA_MOLAR]))
        chaves = self._versoes_por_chave()
        self.assertEqual(1, len(escolhas))
        self.assertEqual(chaves["SOND-EST-MASSA-MOLAR-1"],
                         escolhas[0].question_version_id)

    def test_e_nenhum_item_de_verificacao_e_servido_como_sondagem(self):
        escolhas = self._rodar(lambda sv: sv.instrumentos(
            conteudo=CONTEUDO,
            habilidades=[LEITURA_FORMULA, MASSA_MOLAR]))
        chaves = self._versoes_por_chave()
        proibidos = {chaves[i.key] for i in ITENS_VER}
        for e in escolhas:
            with self.subTest(e.habilidade):
                self.assertNotIn(e.question_version_id, proibidos)


class AVERIFICACAONAOVEOITEMDASONDAGEM(_Base):
    """O ponto do bloco: verificar não é reservir o que ele já errou."""

    def test_devolve_itens_de_verificacao_para_massa_molar(self):
        escolhas = self._rodar(lambda sv: sv.verificacao(
            conteudo=CONTEUDO, habilidade=MASSA_MOLAR, quantas=3))
        self.assertEqual(3, len(escolhas))

    def test_e_NENHUM_deles_e_o_item_da_sondagem(self):
        escolhas = self._rodar(lambda sv: sv.verificacao(
            conteudo=CONTEUDO, habilidade=MASSA_MOLAR, quantas=3))
        chaves = self._versoes_por_chave()
        da_sondagem = chaves["SOND-EST-MASSA-MOLAR-1"]
        for e in escolhas:
            with self.subTest(e.question_version_id):
                self.assertNotEqual(da_sondagem, e.question_version_id)

    def test_todos_sao_dos_itens_curados_de_verificacao(self):
        escolhas = self._rodar(lambda sv: sv.verificacao(
            conteudo=CONTEUDO, habilidade=MASSA_MOLAR, quantas=3))
        chaves = self._versoes_por_chave()
        esperados = {chaves[i.key] for i in ITENS_VER}
        self.assertEqual(esperados,
                         {e.question_version_id for e in escolhas})

    def test_a_origem_declarada_e_curada(self):
        """§16: a origem viaja como DADO, não como suposição de quem lê."""
        escolhas = self._rodar(lambda sv: sv.verificacao(
            conteudo=CONTEUDO, habilidade=MASSA_MOLAR, quantas=3))
        for e in escolhas:
            with self.subTest(e.question_version_id):
                self.assertEqual(ORIGEM_CURADA, e.origem)
                self.assertIn("verifica", e.motivo.lower())

    def test_pedir_menos_devolve_menos_e_sempre_os_mesmos_primeiros(self):
        """Determinístico: o aluno não vê o item mudar entre duas aberturas."""
        uma = self._rodar(lambda sv: sv.verificacao(
            conteudo=CONTEUDO, habilidade=MASSA_MOLAR, quantas=1))
        outra = self._rodar(lambda sv: sv.verificacao(
            conteudo=CONTEUDO, habilidade=MASSA_MOLAR, quantas=1))
        self.assertEqual(1, len(uma))
        self.assertEqual(uma[0].question_version_id,
                         outra[0].question_version_id)

    def test_habilidade_sem_item_de_verificacao_devolve_vazio(self):
        """Fail-open para o status quo: quem chamou cai na seleção por conteúdo.

        É o caso de 36 dos 37 conteúdos do catálogo hoje. Devolver um item de
        outra habilidade seria pior que devolver nada.
        """
        self.assertEqual([], self._rodar(lambda sv: sv.verificacao(
            conteudo=CONTEUDO, habilidade=LEITURA_FORMULA, quantas=3)))

    def test_conteudo_desconhecido_devolve_vazio(self):
        self.assertEqual([], self._rodar(lambda sv: sv.verificacao(
            conteudo="CHEMISTRY-PHYSICAL-KINETICS",
            habilidade=MASSA_MOLAR, quantas=3)))


class OJAVISTONAOVOLTA(_Base):
    """Verificar com o item que ele já respondeu mede memória."""

    def test_o_item_ja_respondido_sai_da_lista(self):
        chaves = self._versoes_por_chave()
        visto = chaves["VER-EST-MASSA-MOLAR-1"]
        escolhas = self._rodar(lambda sv: sv.verificacao(
            conteudo=CONTEUDO, habilidade=MASSA_MOLAR, quantas=3,
            ja_vistos={visto}))
        self.assertEqual(2, len(escolhas))
        self.assertNotIn(visto, {e.question_version_id for e in escolhas})

    def test_com_todos_vistos_devolve_vazio_em_vez_de_repetir(self):
        """Fail-closed: não há verificação nova a fazer, e dizer isso é honesto."""
        chaves = self._versoes_por_chave()
        todos = {chaves[i.key] for i in ITENS_VER}
        self.assertEqual([], self._rodar(lambda sv: sv.verificacao(
            conteudo=CONTEUDO, habilidade=MASSA_MOLAR, quantas=3,
            ja_vistos=todos)))


if __name__ == "__main__":
    unittest.main()


class OQUEELEJARESPONDEUNAOVOLTA(_Base):
    """A exclusão vem do histórico real, não de um parâmetro de teste."""

    def _marcar_respondida(self, question_version_id: str) -> None:
        """Grava um ActivityResult+Item como a correção gravaria.

        Os FKs de `activity_results` apontam para tentativa, atribuição e
        versão de avaliação; em SQLite eles não são verificados, e construir
        as três não acrescentaria nada ao que este teste mede — que é se a
        exclusão lê o histórico. O que importa é `student_external_id` e o
        `question_version_id` do item.
        """
        import uuid

        from agente_ia_edu.db.models.assessments import (
            ActivityResult,
            ActivityResultItem,
        )

        async def run():
            async with self.factory() as s:
                r = ActivityResult(
                    id=uuid.uuid4(), attempt_id=uuid.uuid4(),
                    assignment_id=uuid.uuid4(), student_external_id=ALUNO,
                    assessment_version_id=uuid.uuid4(),
                    question_count=1, answered_count=1, correct_count=1,
                    incorrect_count=0, unanswered_count=0)
                s.add(r)
                await s.flush()
                s.add(ActivityResultItem(
                    id=uuid.uuid4(), result_id=r.id,
                    question_version_id=uuid.UUID(question_version_id),
                    position=1, is_correct=True, answered=True))
                await s.commit()
        self.loop.run_until_complete(run())

    def _verificar(self, quantas=3):
        async def run():
            async with self.factory() as s:
                return await itens_para_verificar(
                    s, aluno=ALUNO, conteudo=CONTEUDO,
                    habilidade=MASSA_MOLAR, quantas=quantas)
        return self.loop.run_until_complete(run())

    def test_sem_historico_os_tres_estao_disponiveis(self):
        self.assertEqual(3, len(self._verificar()))

    def test_o_item_respondido_sai_da_lista(self):
        chaves = self._versoes_por_chave()
        visto = chaves["VER-EST-MASSA-MOLAR-2"]
        self._marcar_respondida(visto)
        ids = {e.question_version_id for e in self._verificar()}
        self.assertEqual(2, len(ids))
        self.assertNotIn(visto, ids)

    def test_o_historico_de_OUTRO_aluno_nao_afeta(self):
        """Isolamento: o que o colega respondeu não tira item deste."""
        import uuid

        from agente_ia_edu.db.models.assessments import (
            ActivityResult,
            ActivityResultItem,
        )
        chaves = self._versoes_por_chave()

        async def run():
            async with self.factory() as s:
                r = ActivityResult(
                    id=uuid.uuid4(), attempt_id=uuid.uuid4(),
                    assignment_id=uuid.uuid4(),
                    student_external_id="outro_aluno_qualquer",
                    assessment_version_id=uuid.uuid4(),
                    question_count=1, answered_count=1, correct_count=1,
                    incorrect_count=0, unanswered_count=0)
                s.add(r)
                await s.flush()
                s.add(ActivityResultItem(
                    id=uuid.uuid4(), result_id=r.id,
                    question_version_id=uuid.UUID(
                        chaves["VER-EST-MASSA-MOLAR-2"]),
                    position=1, is_correct=True, answered=True))
                await s.commit()
        self.loop.run_until_complete(run())
        self.assertEqual(3, len(self._verificar()))

    def test_com_os_tres_respondidos_devolve_vazio(self):
        chaves = self._versoes_por_chave()
        for item in ITENS_VER:
            self._marcar_respondida(chaves[item.key])
        self.assertEqual([], self._verificar())

    def test_sem_habilidade_identificada_devolve_vazio(self):
        """`None` acontece quando a amostra não sustenta dizer qual falhou."""
        async def run():
            async with self.factory() as s:
                return await itens_para_verificar(
                    s, aluno=ALUNO, conteudo=CONTEUDO, habilidade=None,
                    quantas=3)
        self.assertEqual([], self.loop.run_until_complete(run()))

    def test_respondidas_le_o_historico_do_aluno(self):
        chaves = self._versoes_por_chave()
        self._marcar_respondida(chaves["VER-EST-MASSA-MOLAR-1"])

        async def run():
            async with self.factory() as s:
                return await respondidas(s, ALUNO)
        self.assertEqual({chaves["VER-EST-MASSA-MOLAR-1"]},
                         self.loop.run_until_complete(run()))

    def test_ids_de_preserva_a_ordem(self):
        escolhas = self._verificar()
        self.assertEqual([e.question_version_id for e in escolhas],
                         ids_de(escolhas))
