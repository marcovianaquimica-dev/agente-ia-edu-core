"""A ATIVIDADE OFICIAL, ponta a ponta, pelos endpoints HTTP.

    atribuída → readiness DIRECT → abre → responde → finaliza
    → correção determinística → resultado → evidência OFFICIAL_ACTIVITY
    → domínio → readiness recalculado → histórico

O backend já existia inteiro desde a PHASE 17/18 — iniciar, salvar, retomar,
finalizar e corrigir são os MESMOS endpoints que o microdiagnóstico usa. O que
faltava era a tela, e o texto "a resolução será disponibilizada em breve" era
uma string fixa em `entry_screen.note`, não um bloqueio de servidor.

Este arquivo existe para que isso continue verdade.

A ASSERÇÃO CENTRAL
==================
**Concluir a atividade NÃO é demonstrar domínio.** Há dois caminhos aqui — um
aluno que vai bem e um que vai mal — e o segundo tem de continuar sendo
tratado como alguém que precisa estudar, por mais que tenha entregado a
tarefa.
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
from agente_ia_edu.db.models.catalog import ContentQuestionLink
from agente_ia_edu.identity import AuthenticatedUserContext
from agente_ia_edu.services.activity_assignment_store import ActivityAssignmentStore
from agente_ia_edu.services.curriculum_domain_map import ORIGIN_OFFICIAL_ACTIVITY
from agente_ia_edu.services.list_generator import ListConfiguration
from agente_ia_edu.services.question_list_store import QuestionListStore, Requester

KEYS = ["A", "B", "C", "D", "E"]
ESTEQ = "QUIM-ESTEQUIOMETRIA"
ALUNO = "aluno_teste_a"
OUTRO = "aluno_de_outra_turma"
PROF = "professor_abc"
TURMA = "PILOTO_3A"
ESCOLA = str(_uuid.uuid5(_uuid.NAMESPACE_DNS, "atividade-oficial-escola"))


def _ctx(user=ALUNO, role="STUDENT"):
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
        no = CatalogNode(code=ESTEQ, name="Estequiometria", node_type="CONTENT",
                         position=1, parent_id=area.id, root_id=disc.id, active=True)
        s.add(no); await s.flush()

        app_ = ExamApplication(exam_id=exam.id, year=2024, application_type="regular", day=1)
        s.add(app_); await s.flush()
        bk = ExamBooklet(exam_application_id=app_.id, code="C24", color="AZUL")
        s.add(bk); await s.flush()
        sd = SourceDocument(exam_application_id=app_.id, exam_booklet_id=bk.id,
                            document_type="ANSWER_KEY", source_url="https://x/g.pdf",
                            acquired_at=datetime.now(timezone.utc), content_hash="g24")
        s.add(sd); await s.flush()
        rev = AnswerKeyRevision(source_document_id=sd.id, revision_number=1, is_official=True)
        s.add(rev); await s.flush()

        gabarito: dict[str, str] = {}
        versoes: list[str] = []
        for numero in range(1, 9):
            correta = KEYS[numero % 5]
            q = Question(validation_status="validated", origin_type="IMPORTED",
                         status="PUBLISHED", visibility_scope="PUBLIC")
            s.add(q); await s.flush()
            v = QuestionVersion(question_id=q.id, version_kind="official_original",
                                canonical_text=f"e{numero}", statement=f"enunciado {numero}",
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
                content=ESTEQ, subcontent=ESTEQ, difficulty="UNKNOWN",
                reasoning_type="U", prerequisites=[], keywords=[], competencies=[],
                skills=[], status="CLASSIFIED", source="rule", lifecycle="ACTIVE",
                model_version="fx", prompt_version="v1",
                metadata_={"taxonomy_version": "curriculum-v2",
                           "primary_content_code": ESTEQ, "visual_dependency": False}))
            s.add(ContentQuestionLink(content_node_id=no.id, question_version_id=v.id))
            gabarito[str(v.id)] = correta
            versoes.append(str(v.id))

        for quem, papel, escopo, alvo in (
                (ALUNO, "STUDENT", "CLASSROOM", TURMA),
                (OUTRO, "STUDENT", "CLASSROOM", "OUTRA_TURMA"),
                (PROF, "TEACHER", "SCHOOL", None)):
            s.add(UserSchoolLink(external_user_id=quem, school_id=_uuid.UUID(ESCOLA),
                                 role=papel, scope_type=escopo,
                                 scope_external_id=alvo, active=True))
        await s.commit()

    async def _distribuir(titulo: str, vids: list[str], alvo_tipo: str,
                          alvo: str) -> str:
        """Uma atividade por SESSAO.

        `ActivityAssignmentStore.create` comita, o que expira todos os objetos
        da sessao; encadear duas distribuicoes na mesma sessao levanta
        MissingGreenlet na segunda. Sessao propria e mais barato que gerenciar
        o ciclo de vida a mao.
        """
        async with factory() as s:
            prof = Requester(external_user_id=PROF, school_id=ESCOLA,
                             role="TEACHER", is_platform_admin=False)
            listas = QuestionListStore(s)
            resumo = await listas.create(
                configuration=ListConfiguration(
                    title=titulo, answer_key_presentation="KEY_AT_END"),
                question_version_ids=[_uuid.UUID(v) for v in vids],
                requester=prof)
            lista_id = _uuid.UUID(str(resumo.id))
            await listas.finalize(lista_id, requester=prof)
            vista, _ = await ActivityAssignmentStore(s).create(
                lista_id, requester=prof, target_type=alvo_tipo, target_id=alvo,
                origin=ORIGIN_OFFICIAL_ACTIVITY)
            qual = str(vista.id)
            await s.commit()
            return qual

    atividade = await _distribuir("Atividade de Estequiometria",
                                  versoes[:5], "CLASS", TURMA)
    # Uma segunda, so do OUTRO aluno, para o teste de isolamento.
    do_outro = await _distribuir("Atividade do outro",
                                 versoes[5:8], "STUDENT", OUTRO)

    return {"gabarito": gabarito, "atividade": atividade,
            "atividade_do_outro": do_outro}


class AtividadeOficialTests(unittest.TestCase):

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

        self.fx = self.loop.run_until_complete(prep())
        self.gabarito = self.fx["gabarito"]
        self.atividade = self.fx["atividade"]
        self.app = create_app()
        self.app.dependency_overrides[get_session_factory] = lambda: self.factory
        self.app.dependency_overrides[get_current_authenticated_context] = _ctx
        self.client = TestClient(self.app)

    def tearDown(self):
        self.app.dependency_overrides.clear()
        self.client.close()
        self.loop.run_until_complete(self.engine.dispose())
        self.loop.close()

    # -- utilidades ---------------------------------------------------------

    def _abrir(self) -> dict:
        r = self.client.post(f"/api/v1/student/activities/{self.atividade}/attempt")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _responder_todas(self, *, acertos: int) -> list[str]:
        estado = self._abrir()
        vids = [q["question_version_id"] for q in estado["questions"]]
        for pos, vid in enumerate(vids):
            certa = self.gabarito[vid]
            escolha = certa if pos < acertos else next(k for k in KEYS if k != certa)
            r = self.client.put(
                f"/api/v1/student/activities/{self.atividade}/attempt/answers/{vid}",
                json={"selected_option": escolha})
            self.assertEqual(r.status_code, 200, r.text)
        return vids

    def _finalizar_e_corrigir(self) -> dict:
        r = self.client.post(
            f"/api/v1/student/activities/{self.atividade}/attempt/complete")
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.post(
            f"/api/v1/student/activities/{self.atividade}/attempt/correct")
        self.assertIn(r.status_code, (200, 201), r.text)
        return r.json().get("result") or {}

    def _dominio(self) -> dict:
        self.client.post("/api/v1/student/domain/rebuild")
        r = self.client.get(f"/api/v1/student/domain/content/{ESTEQ}")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json().get("content") or {}

    def _readiness(self) -> dict:
        r = self.client.get(f"/api/v1/student/activities/{self.atividade}/readiness")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    # -- o ciclo ------------------------------------------------------------

    def test_a_a_atividade_pode_ser_iniciada_pelo_caminho_real(self):
        """O `entry_screen.note` dizia que a resolução não estava disponível.
        Era texto, não bloqueio: `can_start` já vinha true."""
        detalhe = self.client.get(
            f"/api/v1/student/activities/{self.atividade}").json()
        self.assertTrue(detalhe["can_start"])

        estado = self._abrir()
        self.assertEqual(estado["status"], "IN_PROGRESS")
        self.assertEqual(len(estado["questions"]), 5)

    def test_b_iniciar_e_idempotente(self):
        """Dois cliques em Começar não criam duas tentativas."""
        a = self._abrir()
        b = self._abrir()
        self.assertEqual(a["status"], b["status"])
        self.assertEqual([q["question_version_id"] for q in a["questions"]],
                         [q["question_version_id"] for q in b["questions"]])

    def test_c_responder_alterar_e_retomar(self):
        estado = self._abrir()
        vid = estado["questions"][0]["question_version_id"]
        certa = self.gabarito[vid]
        outra = next(k for k in KEYS if k != certa)

        self.client.put(
            f"/api/v1/student/activities/{self.atividade}/attempt/answers/{vid}",
            json={"selected_option": outra})
        self.client.put(
            f"/api/v1/student/activities/{self.atividade}/attempt/answers/{vid}",
            json={"selected_option": certa})

        # sair e voltar: o estado vem do servidor, não da memória da tela
        voltou = self.client.get(
            f"/api/v1/student/activities/{self.atividade}/attempt").json()
        q = next(x for x in voltou["questions"] if x["question_version_id"] == vid)
        self.assertTrue(q["answered"])
        self.assertEqual(q["selected_option"], certa,
                         "a alteração não sobreviveu ao recarregamento")

    def test_d_finalizar_incompleta_e_recusado(self):
        """O backend valida a completude - a tela não é a autoridade."""
        self._abrir()
        r = self.client.post(
            f"/api/v1/student/activities/{self.atividade}/attempt/complete")
        self.assertEqual(r.status_code, 409, r.text)

    def test_e_dupla_finalizacao(self):
        self._responder_todas(acertos=5)
        p = self.client.post(
            f"/api/v1/student/activities/{self.atividade}/attempt/complete")
        s = self.client.post(
            f"/api/v1/student/activities/{self.atividade}/attempt/complete")
        self.assertEqual(p.status_code, 200, p.text)
        self.assertEqual(s.status_code, 200,
                         "a segunda finalização deveria ser idempotente")
        self.assertEqual(s.json()["status"], "COMPLETED")

    def test_f_resposta_depois_de_finalizar_e_recusada(self):
        vids = self._responder_todas(acertos=5)
        self.client.post(
            f"/api/v1/student/activities/{self.atividade}/attempt/complete")
        r = self.client.put(
            f"/api/v1/student/activities/{self.atividade}/attempt/answers/{vids[0]}",
            json={"selected_option": "A"})
        self.assertEqual(r.status_code, 409, r.text)

    def test_g_correcao_e_deterministica(self):
        self._responder_todas(acertos=3)
        res = self._finalizar_e_corrigir()
        self.assertEqual(res["question_count"], 5)
        self.assertEqual(res["correct_count"], 3)
        self.assertEqual(res["incorrect_count"], 2)

    def test_h_a_correcao_e_idempotente(self):
        self._responder_todas(acertos=4)
        a = self._finalizar_e_corrigir()
        r = self.client.post(
            f"/api/v1/student/activities/{self.atividade}/attempt/correct")
        self.assertIn(r.status_code, (200, 201), r.text)
        self.assertEqual((r.json().get("result") or {})["correct_count"],
                         a["correct_count"])

    # -- a proveniência -----------------------------------------------------

    def test_i_a_evidencia_e_OFFICIAL_ACTIVITY_e_nao_MICRO_DIAGNOSTIC(self):
        self._responder_todas(acertos=4)
        self._finalizar_e_corrigir()
        origens = self._dominio().get("origin_breakdown") or {}
        self.assertGreaterEqual(origens.get("OFFICIAL_ACTIVITY", 0), 5, origens)
        self.assertEqual(origens.get("MICRO_DIAGNOSTIC", 0), 0,
                         f"a tarefa da escola virou microdiagnóstico: {origens}")
        self.assertEqual(origens.get("UNKNOWN_ORIGIN", 0), 0, origens)

    # -- CONCLUIR NÃO É DOMINAR --------------------------------------------

    def test_j_bom_desempenho_o_dominio_sobe(self):
        self._responder_todas(acertos=5)
        self._finalizar_e_corrigir()
        c = self._dominio()
        self.assertEqual(c["questions_answered"], 5)
        self.assertAlmostEqual(float(c["accuracy"]), 1.0, places=3)
        self.assertEqual(self._readiness()["readiness_route"], "DIRECT")

    def test_k_desempenho_RUIM_NAO_promove_por_ter_concluido(self):
        """A asserção central deste arquivo.

        O aluno entregou a tarefa inteira e errou tudo. Entregar não é saber,
        e a política continua sendo a autoridade sobre suficiência.
        """
        self._responder_todas(acertos=0)
        res = self._finalizar_e_corrigir()
        self.assertEqual(res["correct_count"], 0)

        c = self._dominio()
        self.assertEqual(c["questions_answered"], 5)
        self.assertAlmostEqual(float(c["accuracy"]), 0.0, places=3)

        p = self._readiness()
        self.assertNotEqual(p["readiness_route"], "DIRECT",
                            f"concluiu errando tudo e foi dado como pronto: {p}")
        self.assertEqual(p["next_step"]["kind"], "PRACTICE")

    def test_l_desempenho_ruim_aparece_como_atencao_no_progresso(self):
        self._responder_todas(acertos=0)
        self._finalizar_e_corrigir()
        self.client.post("/api/v1/student/domain/rebuild")
        faixas = self.client.get("/api/v1/student/progress").json()["faixas"]
        por_faixa = {f["faixa"]: f["itens"] for f in faixas}
        self.assertNotIn("Estequiometria", por_faixa.get("Consolidado", []),
                         f"errou tudo e apareceu como consolidado: {faixas}")

    def test_m_o_historico_reflete_a_conclusao(self):
        self._responder_todas(acertos=3)
        self._finalizar_e_corrigir()
        r = self.client.get(
            f"/api/v1/student/activities/{self.atividade}/attempt/result")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json().get("result") or {})["completion_status"],
                         "COMPLETED")

    # -- retomada: respostas E cursor ---------------------------------------

    def test_c2_o_cursor_sobrevive_a_sair_e_voltar(self):
        """Eu havia registrado como dívida que o cursor não era restaurado.

        Estava errado: `current_position` é coluna de `ActivityAttempt`,
        `set_current_position` a grava e `get_state` a devolve. O caminho
        inteiro já existia; o que faltava era eu medir antes de escrever a
        dívida.
        """
        self._abrir()
        self.client.put(
            f"/api/v1/student/activities/{self.atividade}/attempt/position"
            f"?position=3")
        voltou = self.client.get(
            f"/api/v1/student/activities/{self.atividade}/attempt").json()
        self.assertEqual(voltou["current_position"], 3,
                         "o aluno voltaria para a primeira questão")

    def test_c3_reabrir_nao_zera_o_cursor(self):
        """`start` é idempotente, e isso inclui não perder onde ele estava."""
        self._abrir()
        self.client.put(
            f"/api/v1/student/activities/{self.atividade}/attempt/position"
            f"?position=4")
        de_novo = self._abrir()
        self.assertEqual(de_novo["current_position"], 4)

    def test_c4_posicao_invalida_e_recusada_sem_corromper(self):
        """Fail-safe: o player não pode quebrar por cursor fora da faixa, e o
        cursor bom não pode ser perdido por uma tentativa ruim."""
        self._abrir()
        self.client.put(
            f"/api/v1/student/activities/{self.atividade}/attempt/position?position=2")
        for ruim in (0, -1, 99):
            with self.subTest(position=ruim):
                r = self.client.put(
                    f"/api/v1/student/activities/{self.atividade}"
                    f"/attempt/position?position={ruim}")
                self.assertEqual(r.status_code, 422, r.text)
        estado = self.client.get(
            f"/api/v1/student/activities/{self.atividade}/attempt").json()
        self.assertEqual(estado["current_position"], 2,
                         "uma posição inválida apagou a posição boa")

    def test_c5_cursor_nas_bordas(self):
        total = len(self._abrir()["questions"])
        for p in (1, total):
            with self.subTest(position=p):
                r = self.client.put(
                    f"/api/v1/student/activities/{self.atividade}"
                    f"/attempt/position?position={p}")
                self.assertEqual(r.status_code, 200, r.text)
                self.assertEqual(
                    self.client.get(
                        f"/api/v1/student/activities/{self.atividade}/attempt"
                    ).json()["current_position"], p)

    # -- autorização e isolamento (§3) -------------------------------------

    def test_n_atividade_inexistente_e_404(self):
        r = self.client.post(
            f"/api/v1/student/activities/{_uuid.uuid4()}/attempt")
        self.assertEqual(r.status_code, 404, r.text)

    def test_o_atividade_de_outro_aluno_nao_abre(self):
        """O que importa: NÃO abre.

        O projeto responde 403 aqui (`AssignmentAuthError`, compartilhado por
        prática, diagnóstico e atividade). Eu escrevi este teste esperando 404,
        porque um 403 com "this activity is not assigned to you" confirma que
        a atividade existe a quem não tem acesso.

        Não mudei o código: trocar o status de `AssignmentAuthError` mexeria no
        contrato de autorização de TODOS os consumidores, e isso é decisão de
        produto, não ajuste de teste. Fica como dívida registrada; o teste
        mede a propriedade que de fato importa.
        """
        outra = self.fx["atividade_do_outro"]
        r = self.client.post(f"/api/v1/student/activities/{outra}/attempt")
        self.assertIn(r.status_code, (403, 404), r.text)
        self.assertNotEqual(r.status_code, 200,
                            "abriu a atividade de outro aluno")

    def test_p_aluno_de_outra_turma_nao_ve_nem_abre(self):
        self.app.dependency_overrides[get_current_authenticated_context] = \
            lambda: _ctx(user=OUTRO)
        try:
            itens = self.client.get("/api/v1/student/activities").json()["items"]
            ids = {i["assignment_id"] for i in itens}
            self.assertNotIn(self.atividade, ids,
                             "a atividade da turma vazou para quem não é dela")
            r = self.client.post(
                f"/api/v1/student/activities/{self.atividade}/attempt")
            self.assertIn(r.status_code, (403, 404), r.text)
        finally:
            self.app.dependency_overrides[get_current_authenticated_context] = _ctx

    def test_p2_a_prontidao_de_atividade_alheia_responde_404(self):
        """O endpoint que eu criei neste piloto escolheu 404 de propósito.

        Fica a inconsistência: `/readiness` diz 404, o player diz 403. As duas
        recusam; só uma esconde que a atividade existe.
        """
        self.app.dependency_overrides[get_current_authenticated_context] = \
            lambda: _ctx(user=OUTRO)
        try:
            r = self.client.get(
                f"/api/v1/student/activities/{self.atividade}/readiness")
            self.assertEqual(r.status_code, 404, r.text)
        finally:
            self.app.dependency_overrides[get_current_authenticated_context] = _ctx

    def test_q_resultado_de_outro_aluno_nao_e_legivel(self):
        self._responder_todas(acertos=5)
        self._finalizar_e_corrigir()
        self.app.dependency_overrides[get_current_authenticated_context] = \
            lambda: _ctx(user=OUTRO)
        try:
            r = self.client.get(
                f"/api/v1/student/activities/{self.atividade}/attempt/result")
            self.assertIn(r.status_code, (403, 404), r.text)
        finally:
            self.app.dependency_overrides[get_current_authenticated_context] = _ctx

    def test_r_a_tentativa_de_outro_aluno_nao_e_visivel(self):
        self._responder_todas(acertos=2)
        self.app.dependency_overrides[get_current_authenticated_context] = \
            lambda: _ctx(user=OUTRO)
        try:
            r = self.client.get(
                f"/api/v1/student/activities/{self.atividade}/attempt")
            self.assertIn(r.status_code, (403, 404), r.text)
        finally:
            self.app.dependency_overrides[get_current_authenticated_context] = _ctx


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
