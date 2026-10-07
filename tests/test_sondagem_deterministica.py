"""A SONDAGEM DEIXA DE DEPENDER DE SORTE — o P0, pelo caminho real.

O QUE SE MEDIU NO NAVEGADOR EM 2026-10-07
==========================================
Três itens servidos na sondagem de Estequiometria, e um deles do banco
genérico, com cinco alternativas — existindo cinco curados publicados.

Este arquivo prova a correção no caminho que o aluno percorre:
`MicroDiagnosticService.start()`, com acervo real montado do zero, contendo
os cinco curados E questões comuns do mesmo conteúdo competindo com eles.

AS DUAS METADES
================
1. o CÉREBRO escolhe as micro-habilidades (grafo, da base ao topo);
2. o SELETOR escolhe o instrumento de cada uma (curado vence genérico).

E a origem de cada escolha viaja no relatório — sem isso, "qual item foi
servido e por quê" só se responderia inspecionando a tela.

CONTEÚDO SEM GRAFO NÃO MUDA
============================
A regra de convivência do projeto: conteúdo com contrato V2 usa o motor por
micro-habilidade, conteúdo sem contrato fica exatamente como estava. Há
teste dos dois lados.
"""

from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import unittest
import uuid as _uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AnswerKeyEntry,
    AnswerKeyRevision,
    BookletQuestion,
    CatalogNode,
    Exam,
    ExamApplication,
    ExamBooklet,
    Institution,
    PedagogicalClassification,
    Question,
    QuestionOption,
    QuestionVersion,
    SourceDocument,
)
from agente_ia_edu.services.grafo_estequiometria import (
    CONTEUDO,
    LEITURA_FORMULA,
    MASSA_MOLAR,
    PROPORCAO,
)
from agente_ia_edu.services.instrumento_de_sondagem import (
    ORIGEM_CURADA,
    ORIGEM_FALLBACK,
)
from agente_ia_edu.services.pedagogical_analysis import PerformanceThresholdPolicy
from agente_ia_edu.services.question_list_store import Requester

_SCRIPT = (pathlib.Path(__file__).resolve().parent.parent
           / "scripts/publicar_sondagem_estequiometria.py")
_spec = importlib.util.spec_from_file_location("publicar_sond_det", _SCRIPT)
publicador = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(publicador)

ESCOLA = _uuid.uuid4()
SEM_GRAFO = "QUIM-SEM-GRAFO"


async def _catalogo(s) -> dict:
    disc = CatalogNode(code="CHEMISTRY", name="Quimica",
                       node_type="DISCIPLINE", position=0, active=True)
    s.add(disc)
    await s.flush()
    disc.root_id = disc.id
    nos = {}
    for code, nome in ((CONTEUDO, "Estequiometria"), (SEM_GRAFO, "Outro")):
        no = CatalogNode(code=code, name=nome, node_type="CONTENT",
                         position=0, parent_id=disc.id, root_id=disc.id,
                         active=True)
        s.add(no)
        await s.flush()
        nos[code] = no
    await s.commit()
    return nos


async def _genericas(s, *, conteudo: str, habilidades: list[str],
                     sufixo: str) -> None:
    """Questões comuns do acervo, classificadas nas MESMAS habilidades.

    Existem para que o curado tenha com quem competir. Elas ficam num caderno
    próprio, com números oficiais a partir de 1 — ou seja, EMPATANDO com os
    curados na ordenação que existia antes deste bloco. Se a prioridade
    dependesse de número, este arquivo não provaria nada.

    As assinaturas são as do publicador real; escrevi de memória na primeira
    versão e o teste quebrou em `application_label`, que não existe.
    """
    from sqlalchemy import select as _sel

    from agente_ia_edu.db.models import ContentQuestionLink

    inst = Institution(code=f"INEP-{sufixo}", name="INEP")
    s.add(inst)
    await s.flush()
    exam = Exam(institution_id=inst.id, code=f"ACERVO-{sufixo}", name="Acervo")
    s.add(exam)
    await s.flush()
    app = ExamApplication(exam_id=exam.id, year=2020,
                          application_type="regular", day=1)
    s.add(app)
    await s.flush()
    cad = ExamBooklet(exam_application_id=app.id, code=f"CAD-{sufixo}",
                      color="AZUL")
    s.add(cad)
    await s.flush()
    doc = SourceDocument(exam_application_id=app.id, exam_booklet_id=cad.id,
                         document_type="ANSWER_KEY",
                         source_url=f"mem://{sufixo}",
                         acquired_at=datetime.now(timezone.utc),
                         content_hash=str(_uuid.uuid4()))
    s.add(doc)
    await s.flush()
    rev = AnswerKeyRevision(source_document_id=doc.id, revision_number=1,
                            is_official=True)
    s.add(rev)
    await s.flush()

    no = (await s.execute(_sel(CatalogNode).where(
        CatalogNode.code == conteudo))).scalars().first()

    for numero, habilidade in enumerate(habilidades, start=1):
        q = Question(validation_status="valid", origin_type="IMPORTED",
                     status="PUBLISHED", visibility_scope="PUBLIC",
                     question_type="MULTIPLE_CHOICE")
        s.add(q)
        await s.flush()
        v = QuestionVersion(question_id=q.id, version_kind="official_original",
                            canonical_text=f"generica {habilidade} {numero}",
                            statement=f"generica {habilidade} {numero}",
                            content_hash=str(_uuid.uuid4()), is_immutable=True)
        s.add(v)
        await s.flush()
        validas = {}
        for pos, letra in enumerate("ABCDE", start=1):
            o = QuestionOption(question_version_id=v.id, option_key=letra,
                               position=pos, text=f"op {letra}",
                               is_valid_option=(letra == "A"))
            s.add(o)
            await s.flush()
            validas[letra] = o
        bq = BookletQuestion(exam_booklet_id=cad.id, question_version_id=v.id,
                             position=numero, official_number=numero,
                             page_number=1)
        s.add(bq)
        await s.flush()
        s.add(AnswerKeyEntry(answer_key_revision_id=rev.id,
                             booklet_question_id=bq.id,
                             official_answer_label="A",
                             resolved_option_id=validas["A"].id, page_number=1))
        s.add(PedagogicalClassification(
            question_version_id=v.id, discipline="CURRICULUM_PROPOSAL",
            content=conteudo, subcontent=habilidade, difficulty="MEDIUM",
            reasoning_type="DIAGNOSTIC", prerequisites=[], keywords=[],
            competencies=[], skills=[habilidade], status="CLASSIFIED",
            source="ai", lifecycle="ACTIVE", provenance="AI_VERIFIED",
            metadata_={"taxonomy_version": "curriculum-v2",
                       "primary_content_code": conteudo,
                       "visual_dependency": False}))
        s.add(ContentQuestionLink(content_node_id=no.id,
                                  question_version_id=v.id))
    await s.commit()


class _Base(unittest.TestCase):

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession,
                                          expire_on_commit=False)

        async def montar():
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with self.factory() as s:
                await _catalogo(s)
            async with self.factory() as s:
                await publicador.publicar(s)
            # As genéricas competem nas MESMAS habilidades dos curados, com
            # números oficiais menores.
            async with self.factory() as s:
                await _genericas(s, conteudo=CONTEUDO,
                                 habilidades=[LEITURA_FORMULA, MASSA_MOLAR,
                                              PROPORCAO, MASSA_MOLAR],
                                 sufixo="EST")
            async with self.factory() as s:
                await _genericas(s, conteudo=SEM_GRAFO,
                                 habilidades=["A", "B", "C", "D"],
                                 sufixo="OUT")

        self.loop.run_until_complete(montar())

    def tearDown(self):
        self.loop.run_until_complete(self.engine.dispose())
        self.loop.close()

    def iniciar(self, conteudo=CONTEUDO, aluno="aluno_qa"):
        async def run():
            async with self.factory() as s:
                from agente_ia_edu.services.micro_diagnostic import (
                    MicroDiagnosticService,
                )
                return await MicroDiagnosticService(s).start(
                    aluno,
                    requester=Requester(external_user_id=aluno,
                                        school_id=ESCOLA, role="STUDENT"),
                    content_code=conteudo)
        return self.loop.run_until_complete(run())


class CURADOVENCENOCAMINHOREAL(_Base):
    """§10.A, pelo `start()` que o aluno de fato dispara."""

    def test_a_sondagem_abre(self):
        d = self.iniciar()
        self.assertTrue(d["sufficient"], d.get("reason"))
        self.assertIsNotNone(d["assignment_id"])

    def test_todo_instrumento_servido_veio_de_item_curado(self):
        """O P0, travado: nenhum genérico onde há curado elegível."""
        instrumentos = self.iniciar()["selection"]["probe"]["instruments"]
        self.assertTrue(instrumentos)
        for i in instrumentos:
            with self.subTest(habilidade=i["skill"]):
                self.assertEqual(ORIGEM_CURADA, i["origin"])

    def test_ha_um_instrumento_por_micro_habilidade_e_sem_repetir(self):
        instrumentos = self.iniciar()["selection"]["probe"]["instruments"]
        habilidades = [i["skill"] for i in instrumentos]
        self.assertEqual(len(set(habilidades)), len(habilidades))
        ids = [i["question_version_id"] for i in instrumentos]
        self.assertEqual(len(set(ids)), len(ids))

    def test_o_tamanho_continua_vindo_da_politica(self):
        esperado = PerformanceThresholdPolicy.default().min_sample_size
        self.assertEqual(esperado, self.iniciar()["question_count"])

    def test_repetir_dez_vezes_serve_exatamente_os_mesmos_itens(self):
        """§10.G — e é a asserção que mata "dependia de sorte"."""
        corridas = set()
        for _ in range(10):
            inst = self.iniciar()["selection"]["probe"]["instruments"]
            corridas.add(tuple((i["skill"], i["question_version_id"])
                               for i in inst))
        self.assertEqual(1, len(corridas))


class OCEREBROESCOLHEAHABILIDADE(_Base):
    """§8/§10.E — a ordem é do grafo, não do acervo."""

    def test_as_habilidades_vem_na_ordem_da_base_para_o_topo(self):
        from agente_ia_edu.services.grafo_estequiometria import GRAFO
        from agente_ia_edu.services.plano_de_sondagem import ordem_de_base

        servidas = [i["skill"]
                    for i in self.iniciar()["selection"]["probe"]["instruments"]]
        ordem = list(ordem_de_base(GRAFO))
        posicoes = [ordem.index(s) for s in servidas]
        self.assertEqual(sorted(posicoes), posicoes)

    def test_a_leitura_de_formula_e_sondada_antes_de_massa_molar(self):
        servidas = [i["skill"]
                    for i in self.iniciar()["selection"]["probe"]["instruments"]]
        self.assertIn(LEITURA_FORMULA, servidas)
        self.assertIn(MASSA_MOLAR, servidas)
        self.assertLess(servidas.index(LEITURA_FORMULA),
                        servidas.index(MASSA_MOLAR))

    def test_habilidade_sem_instrumento_aparece_no_relatorio(self):
        """Conceito de mol e leitura de coeficiente não têm questão nenhuma."""
        relatorio = self.iniciar()["selection"]["probe"]
        self.assertIn("CONCEITO_DE_MOL", relatorio["skills_without_instrument"])
        self.assertIn("LEITURA_DE_COEFICIENTE",
                      relatorio["skills_without_instrument"])

    def test_e_ela_nao_e_sondada(self):
        servidas = {i["skill"]
                    for i in self.iniciar()["selection"]["probe"]["instruments"]}
        self.assertNotIn("CONCEITO_DE_MOL", servidas)


class AORIGEMEOBSERVAVEL(_Base):
    """§16 — descobrir o que foi servido não pode exigir ler a tela."""

    def test_cada_instrumento_diz_de_onde_veio_e_por_que(self):
        for i in self.iniciar()["selection"]["probe"]["instruments"]:
            with self.subTest(habilidade=i["skill"]):
                self.assertIn(i["origin"], (ORIGEM_CURADA, ORIGEM_FALLBACK))
                self.assertTrue(i["reason"].strip())

    def test_o_relatorio_diz_em_que_modo_a_selecao_rodou(self):
        self.assertEqual("PER_SKILL",
                         self.iniciar()["selection"]["probe"]["mode"])


class CONTEUDOSEMGRAFONAOMUDA(_Base):
    """A regra de convivência: 36 dos 37 conteúdos ficam como estavam."""

    def test_sem_grafo_a_selecao_continua_por_conteudo(self):
        d = self.iniciar(conteudo=SEM_GRAFO)
        self.assertTrue(d["sufficient"], d.get("reason"))
        self.assertEqual("CONTENT", d["selection"]["probe"]["mode"])

    def test_sem_grafo_nao_se_inventa_instrumento_por_habilidade(self):
        relatorio = self.iniciar(conteudo=SEM_GRAFO)["selection"]["probe"]
        self.assertEqual([], relatorio["instruments"])

    def test_sem_grafo_o_tamanho_continua_o_da_politica(self):
        esperado = PerformanceThresholdPolicy.default().min_sample_size
        self.assertEqual(esperado,
                         self.iniciar(conteudo=SEM_GRAFO)["question_count"])


class SELECIONARNAOCRIADOMINIO(_Base):
    """§10.F — montar a sondagem não é medir ninguém."""

    def test_abrir_a_sondagem_nao_escreve_dominio(self):
        from sqlalchemy import select as _select

        from agente_ia_edu.db.models.assessments import DomainContentMastery

        self.iniciar()

        async def contar():
            async with self.factory() as s:
                return len((await s.execute(
                    _select(DomainContentMastery))).scalars().all())

        self.assertEqual(0, self.loop.run_until_complete(contar()))

    def test_o_relatorio_nao_carrega_nada_parecido_com_nota(self):
        relatorio = self.iniciar()["selection"]["probe"]
        for proibido in ("mastery", "score", "accuracy", "band"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, relatorio)

    def test_abrir_duas_vezes_nao_acumula_evidencia(self):
        from sqlalchemy import select as _select

        from agente_ia_edu.db.models import ActivityResult

        self.iniciar()
        self.iniciar()

        async def contar():
            async with self.factory() as s:
                return len((await s.execute(
                    _select(ActivityResult))).scalars().all())

        self.assertEqual(0, self.loop.run_until_complete(contar()))


if __name__ == "__main__":
    unittest.main()
