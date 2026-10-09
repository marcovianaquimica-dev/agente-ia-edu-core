"""MICRODIAGNOSTICO POR CONTEUDO - a decisao de 3 perguntas.

Existe para responder UMA pergunta: o aluno pode ir direto para a tarefa da
escola, ou precisa preparar um pre-requisito antes?

NAO e o InitialDiagnostic. Aquele e a sessao unica de onboarding que estima o
mapa de dominio inteiro. Este olha UM conteudo e decide UMA coisa.

NAO e um motor novo. Reutiliza AdaptivePracticeService inteiro - selecao
deterministica por content_code, exclusao de dependencia visual e de questoes
protegidas, despriorizacao das praticadas recentemente - mudando apenas a
ORIGEM da evidencia e a quantidade.

NAO e prova. Nao produz nota, e a lista que ele monta diz isso ao aluno.

O NUMERO 3 NAO E DESTE ARQUIVO
==============================
A politica de desempenho (PerformanceThresholdPolicy.min_sample_size) e que
define quantas respostas sao necessarias para a evidencia deixar de ser
insuficiente. O microdiagnostico PERGUNTA a ela. Se a politica mudar para 4,
o microdiagnostico passa a pedir 4 sem que ninguem edite este codigo - e ha
teste abaixo que falha se alguem escrever 3 na mao.
"""

from __future__ import annotations

import asyncio
import unittest
import uuid as _uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AnswerKeyEntry, AnswerKeyRevision, BookletQuestion, CatalogNode, Exam,
    ExamApplication, ExamBooklet, Institution, PedagogicalClassification,
    Question, QuestionOption, QuestionVersion, SourceDocument,
)
from agente_ia_edu.services.curriculum_domain_map import ORIGIN_MICRO_DIAGNOSTIC
from agente_ia_edu.services.pedagogical_analysis import PerformanceThresholdPolicy
from agente_ia_edu.services.question_list_store import Requester

KEYS = ["A", "B", "C", "D", "E"]

ESTEQ = "QUIM-ESTEQUIOMETRIA"
BALANC = "QUIM-BALANCEAMENTO"
VAZIO = "QUIM-SEM-QUESTOES"

ESCOLA = _uuid.uuid4()


async def _seed(factory) -> dict:
    """Banco minimo: dois conteudos com questoes, um sem nenhuma."""
    async with factory() as s:
        inst = Institution(code="INEP", name="INEP"); s.add(inst); await s.flush()
        exam = Exam(institution_id=inst.id, code="ENEM", name="ENEM"); s.add(exam); await s.flush()

        disc = CatalogNode(code="QUIM", name="Quimica", node_type="DISCIPLINE", position=0, active=True)
        s.add(disc); await s.flush(); disc.root_id = disc.id; await s.flush()
        area = CatalogNode(code="QUIM-A", name="Area", node_type="AREA", position=0,
                           parent_id=disc.id, root_id=disc.id, active=True)
        s.add(area); await s.flush()
        for pos, (code, nome) in enumerate(
                ((ESTEQ, "Estequiometria"), (BALANC, "Balanceamento"), (VAZIO, "Sem questoes")), 1):
            s.add(CatalogNode(code=code, name=nome, node_type="CONTENT", position=pos,
                              parent_id=area.id, root_id=disc.id, active=True))
        await s.flush()

        app = ExamApplication(exam_id=exam.id, year=2024, application_type="regular", day=1)
        s.add(app); await s.flush()
        bk = ExamBooklet(exam_application_id=app.id, code="C24", color="AZUL")
        s.add(bk); await s.flush()
        sd = SourceDocument(exam_application_id=app.id, exam_booklet_id=bk.id,
                            document_type="ANSWER_KEY", source_url="https://x/g.pdf",
                            acquired_at=datetime.now(timezone.utc), content_hash="g24")
        s.add(sd); await s.flush()
        rev = AnswerKeyRevision(source_document_id=sd.id, revision_number=1, is_official=True)
        s.add(rev); await s.flush()

        gabarito: dict[str, str] = {}
        numero = 1

        async def questao(content: str):
            nonlocal numero
            correta = KEYS[numero % 5]
            q = Question(validation_status="validated", origin_type="IMPORTED",
                         status="PUBLISHED", visibility_scope="PUBLIC")
            s.add(q); await s.flush()
            v = QuestionVersion(question_id=q.id, version_kind="official_original",
                                canonical_text=f"e{numero}", statement=f"e{numero}",
                                content_hash=f"h{numero}", is_immutable=True)
            s.add(v); await s.flush()
            opcoes = {}
            for pos, key in enumerate(KEYS, start=1):
                o = QuestionOption(question_version_id=v.id, option_key=key, position=pos,
                                   text=f"Alt {key}", is_valid_option=(key == correta))
                s.add(o); await s.flush(); opcoes[key] = o
            bq = BookletQuestion(exam_booklet_id=bk.id, question_version_id=v.id,
                                 position=numero, official_number=numero, page_number=1)
            s.add(bq); await s.flush()
            s.add(AnswerKeyEntry(answer_key_revision_id=rev.id, booklet_question_id=bq.id,
                                 official_answer_label=correta, resolved_option_id=opcoes[correta].id,
                                 page_number=1))
            s.add(PedagogicalClassification(
                question_version_id=v.id, discipline="CURRICULUM_PROPOSAL",
                content=content, subcontent=content, difficulty="UNKNOWN", reasoning_type="U",
                prerequisites=[], keywords=[], competencies=[], skills=[],
                status="CLASSIFIED", source="rule", lifecycle="ACTIVE",
                model_version="fx", prompt_version="v1",
                metadata_={"taxonomy_version": "curriculum-v2",
                           "primary_content_code": content, "visual_dependency": False}))
            gabarito[str(v.id)] = correta
            numero += 1

        for _ in range(8):
            await questao(ESTEQ)
        for _ in range(8):
            await questao(BALANC)
        await s.commit()
        return {"gabarito": gabarito}


class MicrodiagnosticoTests(unittest.TestCase):

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                          connect_args={"check_same_thread": False},
                                          poolclass=StaticPool)
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

        async def montar():
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            return await _seed(self.factory)

        self.fixture = self.loop.run_until_complete(montar())

    def tearDown(self):
        self.loop.run_until_complete(self.engine.dispose())
        self.loop.close()

    def _requester(self, aluno="pedro"):
        return Requester(external_user_id=aluno, school_id=ESCOLA, role="STUDENT")

    async def _servico(self, s):
        from agente_ia_edu.services.micro_diagnostic import MicroDiagnosticService
        return MicroDiagnosticService(s)

    def _iniciar(self, content_code, aluno="pedro"):
        async def run():
            async with self.factory() as s:
                svc = await self._servico(s)
                return await svc.start(aluno, requester=self._requester(aluno),
                                       content_code=content_code)
        return self.loop.run_until_complete(run())

    # -- o tamanho vem da politica, nao de um literal -----------------------

    def test_o_numero_de_questoes_vem_da_politica_de_desempenho(self):
        esperado = PerformanceThresholdPolicy.default().min_sample_size
        d = self._iniciar(ESTEQ)
        self.assertEqual(d["question_count"], esperado)

    def test_ninguem_escreveu_3_na_mao_no_servico(self):
        """Se a politica virar 4, o microdiagnostico tem de acompanhar sozinho."""
        import inspect

        from agente_ia_edu.services import micro_diagnostic

        fonte = inspect.getsource(micro_diagnostic)
        self.assertIn("min_sample_size", fonte,
                      "o servico precisa PERGUNTAR o tamanho a politica")

    def test_com_politica_diferente_o_tamanho_acompanha(self):
        async def run():
            from agente_ia_edu.services.micro_diagnostic import MicroDiagnosticService
            async with self.factory() as s:
                svc = MicroDiagnosticService(
                    s, thresholds=PerformanceThresholdPolicy(min_sample_size=5))
                return await svc.start("pedro", requester=self._requester(),
                                       content_code=ESTEQ)

        d = self.loop.run_until_complete(run())
        self.assertEqual(d["question_count"], 5)

    # -- a origem da evidencia ----------------------------------------------

    def test_a_evidencia_nasce_com_origem_propria(self):
        d = self._iniciar(ESTEQ)
        self.assertEqual(d["origin"], ORIGIN_MICRO_DIAGNOSTIC)
        self.assertNotEqual(d["origin"], "OFFICIAL_ACTIVITY")
        self.assertNotEqual(d["origin"], "PRACTICE")

    def test_a_atividade_criada_carrega_a_origem_no_metadata(self):
        """E o metadata do assignment que o mapa de dominio le depois."""
        from agente_ia_edu.db.models.assessments import ActivityAssignment

        d = self._iniciar(ESTEQ)

        async def ler():
            async with self.factory() as s:
                row = await s.get(ActivityAssignment, _uuid.UUID(str(d["assignment_id"])))
                return (row.metadata_ or {})

        md = self.loop.run_until_complete(ler())
        self.assertEqual(md.get("origin"), ORIGIN_MICRO_DIAGNOSTIC)
        self.assertTrue(md.get("micro_diagnostic"))

    def test_nao_se_apresenta_como_prova_nem_promete_nota(self):
        d = self._iniciar(ESTEQ)
        texto = f"{d.get('title', '')} {d.get('instructions', '')}".lower()
        for proibido in ("prova", "nota", "avalia"):
            self.assertNotIn(proibido, texto,
                             f"o microdiagnostico se apresentou como {proibido!r}")

    # -- banco insuficiente: nao inventa conclusao --------------------------

    def test_sem_questoes_suficientes_nao_conclui_nada(self):
        d = self._iniciar(VAZIO)
        self.assertFalse(d["sufficient"])
        self.assertEqual(d["decision"], "INSUFFICIENT_EVIDENCE")
        self.assertIsNone(d.get("assignment_id"))

    def test_sem_questoes_suficientes_explica_o_que_faltou(self):
        d = self._iniciar(VAZIO)
        self.assertEqual(d["selection"]["available_questions"], 0)
        self.assertGreater(d["selection"]["requested_questions"], 0)

    # -- a regra de parada pergunta a politica, nao conta ate 3 -------------

    def test_a_parada_antecipada_consulta_a_politica_vigente(self):
        async def run():
            from agente_ia_edu.services.micro_diagnostic import MicroDiagnosticService
            async with self.factory() as s:
                return MicroDiagnosticService(s)

        svc = self.loop.run_until_complete(run())
        # com a politica padrao (min_sample_size=3) nao da para decidir com 2
        self.assertFalse(svc.evidencia_suficiente(answered=2, accuracy=1.0))
        self.assertTrue(svc.evidencia_suficiente(answered=3, accuracy=1.0))

    def test_uma_politica_mais_frouxa_permitiria_parar_antes(self):
        """A parada antecipada e estrutural, mesmo que hoje nunca dispare."""
        async def run():
            from agente_ia_edu.services.micro_diagnostic import MicroDiagnosticService
            async with self.factory() as s:
                return MicroDiagnosticService(
                    s, thresholds=PerformanceThresholdPolicy(min_sample_size=2))

        svc = self.loop.run_until_complete(run())
        self.assertTrue(svc.evidencia_suficiente(answered=2, accuracy=1.0))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
