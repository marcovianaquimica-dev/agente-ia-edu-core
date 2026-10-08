"""As dez invariantes do Piloto Zero, como regressão.

Cada uma já foi demonstrada em algum bloco — por medição, no navegador ou num
teste pontual. Aqui elas viram regressão explícita, num lugar só, para que
nenhuma volte a ser falsa em silêncio.

A mais importante é a primeira, e todas as outras existem para protegê-la:

    CONCLUIR NÃO É DOMINAR.

Um sistema que confunde as duas devolve ao aluno um elogio que ele não ganhou,
e à escola um dado que não é verdade.
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
from agente_ia_edu.db.models.catalog import (
    CatalogNodePrerequisite, ContentQuestionLink,
)
from agente_ia_edu.identity import AuthenticatedUserContext
from agente_ia_edu.services.activity_assignment_store import ActivityAssignmentStore
from agente_ia_edu.services.curriculum_domain_map import ORIGIN_OFFICIAL_ACTIVITY
from agente_ia_edu.services.list_generator import ListConfiguration
from agente_ia_edu.services.question_list_store import QuestionListStore, Requester

KEYS = ["A", "B", "C", "D", "E"]
ESTEQ = "QUIM-ESTEQUIOMETRIA"
BALANC = "QUIM-BALANCEAMENTO"
ANA = "aluna_ana"
BRUNO = "aluno_bruno"
PROF = "professor_abc"
TURMA = "TURMA_DAS_INVARIANTES"
ESCOLA = str(_uuid.uuid5(_uuid.NAMESPACE_DNS, "invariantes-escola"))


def _ctx(user=ANA, role="STUDENT"):
    return AuthenticatedUserContext(user_id=user, external_identity_id=user,
                                    role=role, school_id=ESCOLA, scope_type="SCHOOL")


async def _seed(factory) -> dict:
    async with factory() as s:
        inst = Institution(code="INEP", name="INEP"); s.add(inst); await s.flush()
        exam = Exam(institution_id=inst.id, code="ENEM", name="ENEM"); s.add(exam); await s.flush()
        disc = CatalogNode(code="QUIM", name="Quimica", node_type="DISCIPLINE",
                           position=0, active=True)
        s.add(disc); await s.flush(); disc.root_id = disc.id; await s.flush()
        area = CatalogNode(code="QUIM-A", name="Area", node_type="AREA", position=0,
                           parent_id=disc.id, root_id=disc.id, active=True)
        s.add(area); await s.flush()
        nos = {}
        for pos, (code, nome) in enumerate(
                ((BALANC, "Balanceamento"), (ESTEQ, "Estequiometria")), 1):
            n = CatalogNode(code=code, name=nome, node_type="CONTENT", position=pos,
                            parent_id=area.id, root_id=disc.id, active=True)
            s.add(n); await s.flush(); nos[code] = n
        s.add(CatalogNodePrerequisite(content_node_id=nos[ESTEQ].id,
                                      prerequisite_node_id=nos[BALANC].id))
        await s.flush()

        app_ = ExamApplication(exam_id=exam.id, year=2024, application_type="regular", day=1)
        s.add(app_); await s.flush()
        bk = ExamBooklet(exam_application_id=app_.id, code="C24", color="AZUL")
        s.add(bk); await s.flush()
        sd = SourceDocument(exam_application_id=app_.id, exam_booklet_id=bk.id,
                            document_type="ANSWER_KEY", source_url="https://x/g.pdf",
                            acquired_at=datetime.now(timezone.utc), content_hash="g24")
        s.add(sd); await s.flush()
        rev = AnswerKeyRevision(source_document_id=sd.id, revision_number=1,
                                is_official=True)
        s.add(rev); await s.flush()

        gabarito: dict[str, str] = {}
        por_conteudo: dict[str, list[str]] = {}
        numero = 1

        async def questao(conteudo: str, skill: str):
            nonlocal numero
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
                content=conteudo, subcontent=skill, difficulty="UNKNOWN",
                reasoning_type="U", prerequisites=[], keywords=[], competencies=[],
                skills=[skill], status="CLASSIFIED", source="rule", lifecycle="ACTIVE",
                model_version="fx", prompt_version="v1",
                metadata_={"taxonomy_version": "curriculum-v2",
                           "primary_content_code": conteudo,
                           "visual_dependency": False}))
            s.add(ContentQuestionLink(content_node_id=nos[conteudo].id,
                                      question_version_id=v.id))
            gabarito[str(v.id)] = correta
            por_conteudo.setdefault(conteudo, []).append(str(v.id))
            numero += 1

        for _ in range(8):
            await questao(ESTEQ, "SKILL_A")
        for _ in range(8):
            await questao(BALANC, "SKILL_B")

        for quem in (ANA, BRUNO):
            s.add(UserSchoolLink(external_user_id=quem, school_id=_uuid.UUID(ESCOLA),
                                 role="STUDENT", scope_type="CLASSROOM",
                                 scope_external_id=TURMA, active=True))
        s.add(UserSchoolLink(external_user_id=PROF, school_id=_uuid.UUID(ESCOLA),
                             role="TEACHER", scope_type="SCHOOL", active=True))
        await s.commit()

    async with factory() as s:
        prof = Requester(external_user_id=PROF, school_id=ESCOLA, role="TEACHER",
                         is_platform_admin=False)
        listas = QuestionListStore(s)
        resumo = await listas.create(
            configuration=ListConfiguration(title="Atividade de Estequiometria",
                                            answer_key_presentation="KEY_AT_END"),
            question_version_ids=[_uuid.UUID(v) for v in por_conteudo[ESTEQ][:5]],
            requester=prof)
        lid = _uuid.UUID(str(resumo.id))
        await listas.finalize(lid, requester=prof)
        vista, _ = await ActivityAssignmentStore(s).create(
            lid, requester=prof, target_type="CLASS", target_id=TURMA,
            origin=ORIGIN_OFFICIAL_ACTIVITY)
        atividade = str(vista.id)
        await s.commit()

    return {"gabarito": gabarito, "atividade": atividade,
            "por_conteudo": por_conteudo}


class InvariantesTests(unittest.TestCase):

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

    def _como(self, quem):
        self.app.dependency_overrides[get_current_authenticated_context] = \
            lambda: _ctx(user=quem)

    def _fazer_atividade(self, *, acertos: int) -> dict:
        estado = self.client.post(
            f"/api/v1/student/activities/{self.atividade}/attempt").json()
        for pos, q in enumerate(estado["questions"]):
            vid = q["question_version_id"]
            certa = self.gabarito[vid]
            escolha = certa if pos < acertos else next(k for k in KEYS if k != certa)
            self.client.put(
                f"/api/v1/student/activities/{self.atividade}/attempt/answers/{vid}",
                json={"selected_option": escolha})
        self.client.post(
            f"/api/v1/student/activities/{self.atividade}/attempt/complete")
        r = self.client.post(
            f"/api/v1/student/activities/{self.atividade}/attempt/correct")
        return r.json().get("result") or {}

    def _praticar(self, content_code: str, *, quantas=3, acertos=3):
        r = self.client.post("/api/v1/student/practice",
                             json={"content_code": content_code,
                                   "question_count": quantas})
        self.assertEqual(r.status_code, 200, r.text)
        pid = r.json()["assignment_id"]
        estado = self.client.post(
            f"/api/v1/student/activities/{pid}/attempt").json()
        for pos, q in enumerate(estado["questions"]):
            vid = q["question_version_id"]
            certa = self.gabarito[vid]
            escolha = certa if pos < acertos else next(k for k in KEYS if k != certa)
            self.client.put(
                f"/api/v1/student/activities/{pid}/attempt/answers/{vid}",
                json={"selected_option": escolha})
        self.client.post(f"/api/v1/student/activities/{pid}/attempt/complete")
        self.client.post(f"/api/v1/student/activities/{pid}/attempt/correct")
        return pid

    def _dominio(self, code=ESTEQ) -> dict:
        self.client.post("/api/v1/student/domain/rebuild")
        return (self.client.get(
            f"/api/v1/student/domain/content/{code}").json().get("content") or {})

    def _readiness(self) -> dict:
        return self.client.get(
            f"/api/v1/student/activities/{self.atividade}/readiness").json()

    # ===================================================== 1 a 3 ==========

    def test_I1_concluir_atividade_nao_e_dominar_conteudo(self):
        res = self._fazer_atividade(acertos=0)
        self.assertEqual(res["completion_status"], "COMPLETED")
        self.assertEqual(res["correct_count"], 0)
        p = self._readiness()
        self.assertNotEqual(p["readiness_route"], "DIRECT",
                            f"entregou tudo errado e foi dado como pronto: {p}")

    def test_I2_existir_evidencia_nao_e_evidencia_positiva(self):
        self._fazer_atividade(acertos=0)
        c = self._dominio()
        self.assertEqual(c["evidence_count"], 5, "a evidência não foi registrada")
        self.assertAlmostEqual(float(c["accuracy"]), 0.0, places=3)
        self.assertNotEqual(self._readiness()["readiness_route"], "DIRECT")

    def test_I3_official_activity_nao_e_micro_diagnostic(self):
        self._fazer_atividade(acertos=5)
        origens = self._dominio().get("origin_breakdown") or {}
        self.assertEqual(origens.get("MICRO_DIAGNOSTIC", 0), 0, origens)
        self.assertGreaterEqual(origens.get("OFFICIAL_ACTIVITY", 0), 5, origens)

    # ===================================================== 4 a 6 ==========

    def test_I4_visualizar_resultado_nao_cria_evidencia(self):
        self._fazer_atividade(acertos=3)
        antes = self._dominio()
        for _ in range(5):
            self.client.get(
                f"/api/v1/student/activities/{self.atividade}/attempt/result")
        self.assertEqual(self._dominio()["evidence_count"], antes["evidence_count"])

    def test_I5_reler_o_estado_nao_cria_evidencia(self):
        """O frontend relê a cada refresh. Se isso contasse, o aluno que
        atualiza a página pareceria ter estudado mais."""
        self._fazer_atividade(acertos=3)
        antes = self._dominio()
        for _ in range(5):
            self.client.get(f"/api/v1/student/activities/{self.atividade}/attempt")
            self.client.get("/api/v1/student/progress")
            self.client.get(
                f"/api/v1/student/activities/{self.atividade}/readiness")
        self.assertEqual(self._dominio()["evidence_count"], antes["evidence_count"])

    def test_I6_corrigir_duas_vezes_nao_duplica_evidencia(self):
        self._fazer_atividade(acertos=4)
        antes = self._dominio()
        for _ in range(3):
            self.client.post(
                f"/api/v1/student/activities/{self.atividade}/attempt/correct")
        depois = self._dominio()
        self.assertEqual(antes["evidence_count"], depois["evidence_count"])
        self.assertEqual(antes["questions_answered"], depois["questions_answered"])
        self.assertEqual(antes["origin_breakdown"], depois["origin_breakdown"])

    # ===================================================== 7 a 9 ==========

    def test_I7_zero_de_cinco_nao_produz_falso_DIRECT(self):
        """A invariante é "não vira DIRECT", não uma rota específica.

        Escrevi este teste esperando `PREREQUISITE_PREPARATION` e veio
        `DIAGNOSTIC`: neste cenário a base (Balanceamento) ainda não tem
        evidência nenhuma, então o passo certo é medi-la antes. Minha
        expectativa é que estava errada — o sistema estava mais certo que eu.
        """
        self._fazer_atividade(acertos=0)
        p = self._readiness()
        self.assertNotEqual(p["readiness_route"], "DIRECT", p)
        self.assertIn(p["next_step"]["kind"], ("PRACTICE", "DIAGNOSTIC"))
        self.assertTrue(p["next_step"].get("content_code"),
                        "sem rota DIRECT e sem passo: o aluno ficaria parado")

    def test_I8_cinco_de_cinco_permite_DIRECT(self):
        self._fazer_atividade(acertos=5)
        self.assertEqual(self._readiness()["readiness_route"], "DIRECT")

    def test_I9_RECOMMENDED_sozinho_nao_significa_pronto(self):
        """O falso-pronto de 2026-10-04, reproduzido de propósito.

        O planejador marca o conteúdo como RECOMMENDED assim que há QUALQUER
        evidência. Para chegar nesse estado é preciso que a base já esteja
        dominada — senão o conteúdo sai como BLOCKED_BY_PREREQUISITE e o caso
        não se reproduz. Por isso o teste pratica Balanceamento antes.

        Com RECOMMENDED e acerto 0.0, a rota NÃO pode ser DIRECT.
        """
        self._praticar(BALANC, quantas=3, acertos=3)
        self._fazer_atividade(acertos=0)
        p = self._readiness()
        estados = {c["content_code"]: c["content_state"]
                   for c in p["required_contents"]}
        self.assertEqual(estados.get(ESTEQ), "RECOMMENDED",
                         f"o cenário deixou de reproduzir o caso: {estados}")
        self.assertNotEqual(p["readiness_route"], "DIRECT",
                            "RECOMMENDED com acerto 0.0 liberou a atividade")
        self.assertEqual(p["next_step"]["kind"], "PRACTICE")

    # ===================================================== 10 =============

    def test_I10_micro_habilidade_sem_amostra_nao_narra(self):
        from agente_ia_edu.services.diagnostico_por_habilidade import (
            diagnostico_por_habilidade,
        )

        d = diagnostico_por_habilidade([
            {"diagnostic_skill": "SKILL_A", "is_correct": True},
            {"diagnostic_skill": "SKILL_B", "is_correct": False},
        ])
        self.assertIsNone(d["texto"],
                          "narrou sobre habilidades com uma resposta cada")

    # ===================================== idempotência (fase 6) ==========

    def test_start_duas_vezes_nao_cria_duas_tentativas(self):
        a = self.client.post(
            f"/api/v1/student/activities/{self.atividade}/attempt").json()
        b = self.client.post(
            f"/api/v1/student/activities/{self.atividade}/attempt").json()
        self.assertEqual([q["question_version_id"] for q in a["questions"]],
                         [q["question_version_id"] for q in b["questions"]])
        self.assertEqual(a["status"], b["status"])

    def test_salvar_a_mesma_resposta_duas_vezes_e_estavel(self):
        estado = self.client.post(
            f"/api/v1/student/activities/{self.atividade}/attempt").json()
        vid = estado["questions"][0]["question_version_id"]
        for _ in range(3):
            r = self.client.put(
                f"/api/v1/student/activities/{self.atividade}/attempt/answers/{vid}",
                json={"selected_option": "B"})
            self.assertEqual(r.status_code, 200, r.text)
        depois = self.client.get(
            f"/api/v1/student/activities/{self.atividade}/attempt").json()
        marcadas = [q for q in depois["questions"] if q.get("selected_option")]
        self.assertEqual(len(marcadas), 1, "salvar de novo criou outra resposta")
        self.assertEqual(marcadas[0]["selected_option"], "B")

    def test_a_ultima_resposta_e_a_que_vale(self):
        estado = self.client.post(
            f"/api/v1/student/activities/{self.atividade}/attempt").json()
        vid = estado["questions"][0]["question_version_id"]
        for escolha in ("A", "C", "E"):
            self.client.put(
                f"/api/v1/student/activities/{self.atividade}/attempt/answers/{vid}",
                json={"selected_option": escolha})
        depois = self.client.get(
            f"/api/v1/student/activities/{self.atividade}/attempt").json()
        q = next(x for x in depois["questions"] if x["question_version_id"] == vid)
        self.assertEqual(q["selected_option"], "E")

    def test_rebuild_repetido_e_estavel(self):
        self._fazer_atividade(acertos=3)
        primeiro = self._dominio()
        for _ in range(3):
            self.client.post("/api/v1/student/domain/rebuild")
        ultimo = self._dominio()
        for campo in ("questions_answered", "questions_correct", "evidence_count"):
            with self.subTest(campo=campo):
                self.assertEqual(primeiro[campo], ultimo[campo])
        self.assertEqual(primeiro["origin_breakdown"], ultimo["origin_breakdown"])

    # ===================================== origens (fase 9) ===============

    def test_as_duas_origens_convivem_no_mesmo_aluno(self):
        """Prática e atividade oficial, no mesmo conteúdo, sem se somarem numa
        origem só: a auditoria precisa saber de onde veio cada evidência."""
        self._praticar(ESTEQ, quantas=3, acertos=3)
        self._fazer_atividade(acertos=5)
        origens = self._dominio().get("origin_breakdown") or {}
        self.assertGreaterEqual(origens.get("OFFICIAL_ACTIVITY", 0), 5, origens)
        self.assertGreaterEqual(origens.get("PRACTICE", 0), 3, origens)
        self.assertEqual(origens.get("UNKNOWN_ORIGIN", 0), 0, origens)

    # ===================================== isolamento (fase 8) ============

    def test_a_evidencia_de_um_nao_mexe_no_dominio_do_outro(self):
        """Ana e Bruno estão na MESMA turma — se o isolamento dependesse só do
        escopo da turma, este teste passaria por acidente."""
        self._fazer_atividade(acertos=5)
        da_ana = self._dominio()
        self.assertEqual(da_ana["questions_answered"], 5)

        self._como(BRUNO)
        do_bruno = self._dominio()
        self.assertEqual(do_bruno.get("questions_answered") or 0, 0,
                         f"o domínio do Bruno herdou evidência da Ana: {do_bruno}")

    def test_o_progresso_de_um_nao_aparece_para_o_outro(self):
        self._fazer_atividade(acertos=5)
        self.client.post("/api/v1/student/domain/rebuild")
        self._como(BRUNO)
        self.client.post("/api/v1/student/domain/rebuild")
        faixas = self.client.get("/api/v1/student/progress").json()["faixas"]
        itens = [i for f in faixas for i in (f.get("itens") or [])]
        self.assertEqual(itens, [], f"o Bruno viu o progresso da Ana: {faixas}")

    def test_a_tentativa_de_um_nao_aparece_para_o_outro(self):
        """Mesma turma: o Bruno PODE abrir a atividade, mas tem de começar do
        zero — a tentativa é por aluno, não por atividade."""
        self.client.post(f"/api/v1/student/activities/{self.atividade}/attempt")
        estado = self.client.get(
            f"/api/v1/student/activities/{self.atividade}/attempt").json()
        vid = estado["questions"][0]["question_version_id"]
        self.client.put(
            f"/api/v1/student/activities/{self.atividade}/attempt/answers/{vid}",
            json={"selected_option": "A"})
        self.client.put(
            f"/api/v1/student/activities/{self.atividade}/attempt/position?position=4")

        self._como(BRUNO)
        dele = self.client.post(
            f"/api/v1/student/activities/{self.atividade}/attempt").json()
        respondidas = [q for q in dele["questions"] if q.get("selected_option")]
        self.assertEqual(respondidas, [],
                         "o Bruno recebeu as respostas da Ana")
        self.assertEqual(dele["current_position"], 1,
                         "o Bruno herdou o cursor da Ana")

    def test_o_resultado_de_um_nao_e_legivel_pelo_outro(self):
        self._fazer_atividade(acertos=5)
        self._como(BRUNO)
        r = self.client.get(
            f"/api/v1/student/activities/{self.atividade}/attempt/result")
        self.assertIn(r.status_code, (404, 409), r.text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
