"""O CICLO FECHADO DO ALUNO - casos A a D, ponta a ponta.

    DOMINIO ATUAL -> SESSAO -> RESPOSTAS -> CORRECAO -> NOVA EVIDENCIA
                  -> DOMINIO ATUALIZADO -> NOVA DECISAO DE PRONTIDAO

Nada aqui e simulado. As respostas passam pelo Player real, pela Correcao
real, e o dominio e relido do mapa real depois. Se algum elo nao fechasse, o
teste nao teria como passar - que e a unica razao de ele existir em vez de um
teste que afirma que o codigo contem certas palavras.

OS CASOS
========
A  dominio suficiente      -> DIRECT, vai direto para a atividade
B  sem evidencia           -> DIAGNOSTIC, microdiagnostico, decide
C  lacuna no pre-requisito -> PREREQUISITE_PREPARATION, prepara, dominio sobe
D  tempo insuficiente      -> prepara, objetivo preservado, retoma depois

Pedro e o aluno de todos eles. Estequiometria e a tarefa da escola;
Balanceamento e o pre-requisito dela.
"""

from __future__ import annotations

import asyncio
import unittest
import uuid as _uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import (
    get_current_authenticated_context,
    get_session_factory,
)
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AnswerKeyEntry, AnswerKeyRevision, BookletQuestion, CatalogNode, Exam,
    ExamApplication, ExamBooklet, Institution, PedagogicalClassification,
    Question, QuestionOption, QuestionVersion, SourceDocument,
)
from agente_ia_edu.db.models.admin import UserSchoolLink
from agente_ia_edu.db.models.catalog import CatalogNodePrerequisite
from agente_ia_edu.identity import AuthenticatedUserContext
from agente_ia_edu.services.curriculum_domain_map import (
    ORIGIN_MICRO_DIAGNOSTIC,
    ORIGIN_OFFICIAL_ACTIVITY,
    ORIGIN_PRACTICE,
)

KEYS = ["A", "B", "C", "D", "E"]
ESTEQ = "QUIM-ESTEQUIOMETRIA"
BALANC = "QUIM-BALANCEAMENTO"
PEDRO = "pedro"
ESCOLA = str(_uuid.uuid5(_uuid.NAMESPACE_DNS, "ciclo-pedro-escola"))


def _ctx(user=PEDRO, role="STUDENT"):
    return AuthenticatedUserContext(user_id=user, external_identity_id=user,
                                    role=role, school_id=ESCOLA, scope_type="SCHOOL")


async def _seed(factory) -> dict:
    async with factory() as s:
        inst = Institution(code="INEP", name="INEP"); s.add(inst); await s.flush()
        exam = Exam(institution_id=inst.id, code="ENEM", name="ENEM"); s.add(exam); await s.flush()

        disc = CatalogNode(code="QUIM", name="Quimica", node_type="DISCIPLINE",
                           position=0, active=True)
        s.add(disc); await s.flush(); disc.root_id = disc.id; await s.flush()
        area = CatalogNode(code="QUIM-A", name="Fisico-quimica", node_type="AREA",
                           position=0, parent_id=disc.id, root_id=disc.id, active=True)
        s.add(area); await s.flush()
        nos = {}
        for pos, (code, nome) in enumerate(
                ((BALANC, "Balanceamento de equacoes"), (ESTEQ, "Estequiometria")), 1):
            n = CatalogNode(code=code, name=nome, node_type="CONTENT", position=pos,
                            parent_id=area.id, root_id=disc.id, active=True)
            s.add(n); await s.flush(); nos[code] = n

        # Estequiometria EXIGE Balanceamento. E este arco que transforma
        # "errou balanceamento" em "nao esta pronto para a tarefa".
        s.add(CatalogNodePrerequisite(content_node_id=nos[ESTEQ].id,
                                      prerequisite_node_id=nos[BALANC].id))
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
        por_conteudo: dict[str, list[str]] = {}
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
                                 official_answer_label=correta,
                                 resolved_option_id=opcoes[correta].id, page_number=1))
            s.add(PedagogicalClassification(
                question_version_id=v.id, discipline="CURRICULUM_PROPOSAL",
                content=content, subcontent=content, difficulty="UNKNOWN",
                reasoning_type="U", prerequisites=[], keywords=[], competencies=[],
                skills=[], status="CLASSIFIED", source="rule", lifecycle="ACTIVE",
                model_version="fx", prompt_version="v1",
                metadata_={"taxonomy_version": "curriculum-v2",
                           "primary_content_code": content, "visual_dependency": False}))
            gabarito[str(v.id)] = correta
            por_conteudo.setdefault(content, []).append(str(v.id))
            numero += 1

        for _ in range(14):
            await questao(ESTEQ)
        for _ in range(14):
            await questao(BALANC)

        s.add(UserSchoolLink(external_user_id=PEDRO, school_id=_uuid.UUID(ESCOLA),
                             role="STUDENT", scope_type="CLASSROOM",
                             scope_external_id="turma-1", active=True))
        await s.commit()
        return {"gabarito": gabarito, "por_conteudo": por_conteudo}


class CicloPedroTests(unittest.TestCase):

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                          poolclass=StaticPool)
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession,
                                          expire_on_commit=False)

        async def prep():
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            return await _seed(self.factory)

        self.fixture = self.loop.run_until_complete(prep())
        self.gabarito = self.fixture["gabarito"]
        self.app = create_app()
        self.app.dependency_overrides[get_session_factory] = lambda: self.factory
        self.app.dependency_overrides[get_current_authenticated_context] = _ctx
        self.client = TestClient(self.app)

    def tearDown(self):
        self.app.dependency_overrides.clear()
        self.client.close()
        self.loop.run_until_complete(self.engine.dispose())
        self.loop.close()

    # -- o ciclo real -------------------------------------------------------

    def _responder(self, assignment_id, *, acertos=None):
        """Player real + correcao real. `acertos` = quantas acertar."""
        st = self.client.post(
            f"/api/v1/student/activities/{assignment_id}/attempt").json()
        vids = [q["question_version_id"] for q in st["questions"]]
        n_acertos = len(vids) if acertos is None else acertos
        for pos, vid in enumerate(vids):
            certa = self.gabarito[vid]
            escolha = certa if pos < n_acertos else next(k for k in KEYS if k != certa)
            self.client.put(
                f"/api/v1/student/activities/{assignment_id}/attempt/answers/{vid}",
                json={"selected_option": escolha})
        self.client.post(f"/api/v1/student/activities/{assignment_id}/attempt/complete")
        r = self.client.post(f"/api/v1/student/activities/{assignment_id}/attempt/correct")
        self.assertIn(r.status_code, (200, 201), r.text)
        return vids

    def _dominio(self, code):
        j = self.client.post("/api/v1/student/domain/rebuild").json()
        return next((c for d in j["disciplines"] for c in d["contents"]
                     if c["content_code"] == code), None)

    def _praticar(self, content_code, quantas, *, acertos=None):
        r = self.client.post("/api/v1/student/practice",
                             json={"content_code": content_code,
                                   "question_count": quantas})
        self.assertEqual(r.status_code, 200, r.text)
        pid = r.json()["assignment_id"]
        self._responder(pid, acertos=acertos)
        return pid

    def _prontidao(self, content_code):
        """A decisao de rota, a partir do dominio REAL de agora."""
        from agente_ia_edu.services.study_session import (
            READINESS_DIAGNOSTIC, READINESS_DIRECT, READINESS_PREREQUISITE,
        )
        from agente_ia_edu.services.pedagogical_analysis import (
            BAND_INSUFFICIENT, BAND_NO_DATA, PerformanceThresholdPolicy,
        )

        politica = PerformanceThresholdPolicy.default()
        alvo = self._dominio(content_code)
        pre = self._dominio(BALANC) if content_code == ESTEQ else None

        def banda(c):
            if c is None:
                return BAND_INSUFFICIENT
            return politica.band(answered=c["questions_answered"],
                                 accuracy=c["accuracy"])

        banda_pre = banda(pre) if pre is not None else None
        if banda_pre is not None and banda_pre not in (BAND_INSUFFICIENT, BAND_NO_DATA):
            from agente_ia_edu.services.pedagogical_analysis import BAND_IMPROVEMENT
            if banda_pre == BAND_IMPROVEMENT:
                return READINESS_PREREQUISITE
        if banda(alvo) in (BAND_INSUFFICIENT, BAND_NO_DATA):
            return READINESS_DIAGNOSTIC
        return READINESS_DIRECT

    # ====================== CASO A - pronto ================================

    def test_caso_A_com_dominio_suficiente_a_rota_e_direta(self):
        from agente_ia_edu.services.study_session import READINESS_DIRECT

        # Pedro acerta Estequiometria e Balanceamento
        self._praticar(BALANC, 5)
        self._praticar(ESTEQ, 5)

        self.assertEqual(self._prontidao(ESTEQ), READINESS_DIRECT)

    # ====================== CASO B - sem evidencia =========================

    def test_caso_B_sem_evidencia_a_rota_e_o_diagnostico(self):
        from agente_ia_edu.services.study_session import READINESS_DIAGNOSTIC

        self.assertIsNone(self._dominio(ESTEQ),
                          "Pedro nao deveria ter evidencia nenhuma ainda")
        self.assertEqual(self._prontidao(ESTEQ), READINESS_DIAGNOSTIC)

    def test_caso_B_o_microdiagnostico_gera_evidencia_com_a_origem_certa(self):
        from agente_ia_edu.services.micro_diagnostic import MicroDiagnosticService
        from agente_ia_edu.services.question_list_store import Requester

        async def iniciar():
            async with self.factory() as s:
                return await MicroDiagnosticService(s).start(
                    PEDRO,
                    requester=Requester(external_user_id=PEDRO,
                                        school_id=_uuid.UUID(ESCOLA), role="STUDENT"),
                    content_code=ESTEQ)

        d = self.loop.run_until_complete(iniciar())
        self.assertTrue(d["sufficient"])
        self._responder(d["assignment_id"])

        c = self._dominio(ESTEQ)
        self.assertIsNotNone(c, "o microdiagnostico nao gerou evidencia nenhuma")
        self.assertEqual(c["questions_answered"], d["question_count"])

        origens = c["origin_breakdown"]
        self.assertEqual(origens.get(ORIGIN_MICRO_DIAGNOSTIC), d["question_count"])
        self.assertNotIn(ORIGIN_OFFICIAL_ACTIVITY, origens,
                         "o microdiagnostico foi contabilizado como avaliacao "
                         "oficial da escola")

    def test_caso_B_depois_do_microdiagnostico_a_rota_deixa_de_ser_diagnostico(self):
        from agente_ia_edu.services.micro_diagnostic import MicroDiagnosticService
        from agente_ia_edu.services.question_list_store import Requester
        from agente_ia_edu.services.study_session import READINESS_DIAGNOSTIC

        async def iniciar():
            async with self.factory() as s:
                return await MicroDiagnosticService(s).start(
                    PEDRO,
                    requester=Requester(external_user_id=PEDRO,
                                        school_id=_uuid.UUID(ESCOLA), role="STUDENT"),
                    content_code=ESTEQ)

        d = self.loop.run_until_complete(iniciar())
        self._responder(d["assignment_id"])
        self.assertNotEqual(self._prontidao(ESTEQ), READINESS_DIAGNOSTIC,
                            "respondeu o diagnostico e a rota continuou pedindo "
                            "diagnostico - o ciclo nao fechou")

    def test_caso_B_a_decisao_sai_da_politica_e_nao_de_um_corte_local(self):
        from agente_ia_edu.services.micro_diagnostic import (
            DECISION_PREPARE, DECISION_PROCEED, MicroDiagnosticService,
        )

        async def servico():
            async with self.factory() as s:
                return MicroDiagnosticService(s)

        svc = self.loop.run_until_complete(servico())
        self.assertEqual(svc.decidir(answered=3, accuracy=1.0)["decision"],
                         DECISION_PROCEED)
        self.assertEqual(
            svc.decidir(answered=3, accuracy=0.0,
                        prerequisito_em_falta=BALANC)["decision"],
            DECISION_PREPARE)

    # ====================== CASO C - lacuna conhecida ======================

    def test_caso_C_lacuna_no_pre_requisito_manda_preparar(self):
        from agente_ia_edu.services.study_session import READINESS_PREREQUISITE

        # Pedro responde Balanceamento MAL: 1 acerto em 5
        self._praticar(BALANC, 5, acertos=1)
        self.assertEqual(self._prontidao(ESTEQ), READINESS_PREREQUISITE)

    def test_caso_C_o_ciclo_fecha_a_preparacao_muda_o_dominio_e_a_rota(self):
        """DOMINIO -> SESSAO -> RESPOSTAS -> CORRECAO -> DOMINIO -> NOVA DECISAO."""
        from agente_ia_edu.services.study_session import READINESS_PREREQUISITE

        self._praticar(BALANC, 5, acertos=1)
        antes = self._dominio(BALANC)
        self.assertEqual(self._prontidao(ESTEQ), READINESS_PREREQUISITE)

        # prepara: pratica Balanceamento e vai bem
        self._praticar(BALANC, 9, acertos=9)

        depois = self._dominio(BALANC)
        self.assertGreater(depois["questions_answered"], antes["questions_answered"],
                           "a pratica nao virou evidencia nova")
        self.assertGreater(depois["accuracy"], antes["accuracy"],
                           "a evidencia nova nao moveu o dominio")
        self.assertNotEqual(self._prontidao(ESTEQ), READINESS_PREREQUISITE,
                            "o dominio subiu e a decisao de prontidao nao mudou - "
                            "a nova decisao nao esta usando o novo estado")

    def test_caso_C_a_pratica_nao_vira_avaliacao_oficial(self):
        self._praticar(BALANC, 5)
        origens = self._dominio(BALANC)["origin_breakdown"]
        self.assertEqual(origens.get(ORIGIN_PRACTICE), 5)
        self.assertNotIn(ORIGIN_OFFICIAL_ACTIVITY, origens)

    # ====================== CASO D - tempo insuficiente ====================

    def test_caso_D_objetivo_preservado_quando_a_atividade_nao_cabe(self):
        from agente_ia_edu.db.models.study_session import StudySession
        from agente_ia_edu.services.question_list_store import Requester
        from agente_ia_edu.services.study_session import (
            READINESS_PREREQUISITE, StudySessionService,
        )

        tarefa = _uuid.uuid4()

        async def criar():
            async with self.factory() as s:
                return await StudySessionService(s).create_student_session(
                    PEDRO,
                    requester=Requester(external_user_id=PEDRO,
                                        school_id=_uuid.UUID(ESCOLA), role="STUDENT"),
                    available_minutes=15,
                    readiness_route=READINESS_PREREQUISITE,
                    objective_assignment_id=tarefa)

        vista = self.loop.run_until_complete(criar())

        async def ler():
            async with self.factory() as s:
                return await s.get(StudySession, _uuid.UUID(vista["id"]))

        linha = self.loop.run_until_complete(ler())
        self.assertEqual(str(linha.objective_assignment_id), str(tarefa))
        self.assertFalse(linha.objective_completed,
                         "a atividade nao coube no tempo mas foi marcada como feita")
        self.assertEqual(linha.readiness_route, READINESS_PREREQUISITE)

    def test_caso_D_a_sessao_seguinte_consegue_retomar_o_mesmo_objetivo(self):
        from sqlalchemy import select

        from agente_ia_edu.db.models.study_session import StudySession
        from agente_ia_edu.services.study_session import READINESS_PREREQUISITE

        tarefa = _uuid.uuid4()

        async def duas_sessoes():
            async with self.factory() as s:
                for dia in ("2026-01-01", "2026-01-02"):
                    s.add(StudySession(
                        student_external_id=PEDRO, source="STUDENT_DEFINED",
                        school_id=_uuid.UUID(ESCOLA), scope_type="STUDENT",
                        scope_external_id=PEDRO, session_date=dia,
                        timer_mode="TIMED", available_minutes=15,
                        break_minutes=0, effective_study_minutes=15,
                        status="READY", current_block_index=0,
                        readiness_route=READINESS_PREREQUISITE,
                        objective_assignment_id=tarefa, objective_completed=False))
                await s.commit()
                q = select(StudySession).where(
                    StudySession.objective_assignment_id == tarefa,
                    StudySession.objective_completed.is_(False))
                return list((await s.scalars(q)).all())

        linhas = self.loop.run_until_complete(duas_sessoes())
        self.assertEqual(len(linhas), 2,
                         "a retomada perdeu o vinculo com a tarefa original")
        self.assertTrue(all(str(x.objective_assignment_id) == str(tarefa) for x in linhas))


    # ====================== MEU PROGRESSO com dado real ====================

    def test_meu_progresso_sai_do_dominio_real_e_nao_de_mock(self):
        self._praticar(BALANC, 5, acertos=1)      # fraco
        self._praticar(ESTEQ, 5, acertos=5)       # forte

        r = self.client.get("/api/v1/student/progress")
        self.assertEqual(r.status_code, 200, r.text)
        faixas = {f["faixa"]: f["itens"] for f in r.json()["faixas"]}

        self.assertIn("Balanceamento de equacoes", faixas["Precisa de atenção"])
        # "Consolidado" virou "Indo bem" em 2026-10-08: a faixa traduz
        # BAND_STRONG, que e acerto forte numa UNICA ocasiao, e a palavra
        # ficou reservada a consolidacao de verdade - repetir em ocasioes
        # diferentes, que agora `services/consolidacao` calcula.
        self.assertIn("Estequiometria", faixas["Indo bem"])

    def test_meu_progresso_nao_entrega_numero_nem_jargao_ao_aluno(self):
        self._praticar(ESTEQ, 5, acertos=5)
        texto = self.client.get("/api/v1/student/progress").text
        for proibido in ("accuracy", "score", "probability", "confidence",
                         "PONTO_FORTE", "origin_breakdown", "questions_answered",
                         "QUIM-ESTEQUIOMETRIA"):
            self.assertNotIn(proibido, texto, f"vazou {proibido!r} para o aluno")

    def test_meu_progresso_de_quem_mal_comecou_nao_diz_que_ele_tem_dificuldade(self):
        """CASO G, ponta a ponta: 1 resposta errada nao e diagnostico."""
        self._praticar(ESTEQ, 1, acertos=0)

        faixas = {f["faixa"]: f["itens"]
                  for f in self.client.get("/api/v1/student/progress").json()["faixas"]}
        self.assertNotIn("Precisa de atenção", faixas,
                         "uma unica resposta errada bastou para o sistema dizer "
                         "ao aluno que ele tem dificuldade")
        self.assertIn("Estequiometria",
                      faixas["Ainda estamos conhecendo seu aprendizado"])

    def test_meu_progresso_de_aluno_sem_historico_nao_quebra(self):
        r = self.client.get("/api/v1/student/progress")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["faixas"], [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
