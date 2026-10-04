"""PILOTO ZERO ponta a ponta, pelos ENDPOINTS HTTP.

    tarefa da escola -> GET /readiness -> POST /micro-diagnostic
    -> Player real -> Correcao real -> evidencia MICRO_DIAGNOSTIC
    -> dominio recalculado -> GET /decision -> readiness MUDA

O teste do ciclo do Pedro (`test_aluno_ciclo_pedro.py`) ja provava esta
cadeia no nivel dos SERVICOS. Este prova pelo caminho que o navegador
percorre, que e onde o Piloto Zero vive - e foi escrito porque ate agora a
decisao de prontidao era calculada no JavaScript, a partir de um MOCK.

O QUE ESTE TESTE NAO FAZ
=========================
Nao procura string em codigo. Toda afirmacao abaixo e medida: as respostas
entram pelo Player, a correcao e deterministica, a evidencia e lida do mapa
de dominio depois, e a rota e recalculada a partir desse estado novo.
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
from agente_ia_edu.services.readiness_route import (
    ROTA_DIAGNOSTICO, ROTA_DIRETA, ROTA_PREPARACAO,
)

KEYS = ["A", "B", "C", "D", "E"]
ESTEQ = "QUIM-ESTEQUIOMETRIA"
BALANC = "QUIM-BALANCEAMENTO"
ALUNO = "aluno_teste_a"
PROF = "professor_abc"
TURMA = "PILOTO_3A"
ESCOLA = str(_uuid.uuid5(_uuid.NAMESPACE_DNS, "piloto-zero-escola"))


def _ctx(user=ALUNO, role="STUDENT"):
    return AuthenticatedUserContext(user_id=user, external_identity_id=user,
                                    role=role, school_id=ESCOLA, scope_type="SCHOOL")


async def _seed(factory) -> dict:
    """A Escola ABC em miniatura: o arco curricular, as questoes dos dois
    conteudos, o aluno na turma do piloto e a atividade da escola distribuida
    pelo caminho real de ActivityAssignment."""
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
            # O elo que faz a ATIVIDADE declarar o conteudo que exige. Sem
            # ele `content_codes` vem vazio e o fail-closed manda diagnosticar
            # sem ter o que perguntar - estado real de atividade cujas questoes
            # ninguem ligou ao catalogo.
            s.add(ContentQuestionLink(content_node_id=nos[content].id,
                                      question_version_id=v.id))
            gabarito[str(v.id)] = correta
            por_conteudo.setdefault(content, []).append(str(v.id))
            numero += 1

        for _ in range(8):
            await questao(ESTEQ)
        for _ in range(14):
            await questao(BALANC)

        for quem, papel in ((ALUNO, "STUDENT"), (PROF, "TEACHER")):
            s.add(UserSchoolLink(
                external_user_id=quem, school_id=_uuid.UUID(ESCOLA), role=papel,
                scope_type="CLASSROOM" if papel == "STUDENT" else "SCHOOL",
                scope_external_id=TURMA if papel == "STUDENT" else None, active=True))
        await s.commit()

    # A atividade da escola, criada pelo professor e distribuida a turma -
    # o mesmo caminho do portal do professor.
    async with factory() as s:
        prof = Requester(external_user_id=PROF, school_id=ESCOLA, role="TEACHER",
                         is_platform_admin=False)
        listas = QuestionListStore(s)
        resumo = await listas.create(
            configuration=ListConfiguration(title="Atividade de Estequiometria",
                                            answer_key_presentation="KEY_AT_END"),
            question_version_ids=[_uuid.UUID(v) for v in por_conteudo[ESTEQ][:5]],
            requester=prof)
        lista_id = _uuid.UUID(str(resumo.id))
        await listas.finalize(lista_id, requester=prof)
        vista, _ = await ActivityAssignmentStore(s).create(
            lista_id, requester=prof, target_type="CLASS", target_id=TURMA,
            origin=ORIGIN_OFFICIAL_ACTIVITY)
        await s.commit()
        atividade = str(vista.id)

    return {"gabarito": gabarito, "por_conteudo": por_conteudo, "atividade": atividade}


class PilotoZeroTests(unittest.TestCase):

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

    # -- utilidades que exercitam o caminho real ----------------------------

    def _readiness(self) -> dict:
        r = self.client.get(f"/api/v1/student/activities/{self.atividade}/readiness")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _abrir_diagnostico(self, content_code) -> dict:
        r = self.client.post("/api/v1/student/micro-diagnostic",
                             json={"content_code": content_code,
                                   "objective_assignment_id": self.atividade})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _responder(self, assignment_id, *, acertos=None):
        st = self.client.post(
            f"/api/v1/student/activities/{assignment_id}/attempt").json()
        vids = [q["question_version_id"] for q in st["questions"]]
        n = len(vids) if acertos is None else acertos
        for pos, vid in enumerate(vids):
            certa = self.gabarito[vid]
            escolha = certa if pos < n else next(k for k in KEYS if k != certa)
            self.client.put(
                f"/api/v1/student/activities/{assignment_id}/attempt/answers/{vid}",
                json={"selected_option": escolha})
        self.client.post(f"/api/v1/student/activities/{assignment_id}/attempt/complete")
        r = self.client.post(f"/api/v1/student/activities/{assignment_id}/attempt/correct")
        self.assertIn(r.status_code, (200, 201), r.text)
        return vids

    def _decisao(self, assignment_id, content_code) -> dict:
        r = self.client.get(
            f"/api/v1/student/micro-diagnostic/{assignment_id}/decision",
            params={"content_code": content_code})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _origens(self, content_code) -> dict:
        """De onde veio a evidencia deste conteudo, segundo o mapa de dominio.

        `origin_breakdown` e o campo que o proprio mapa mantem; ler dali, e
        nao do que o endpoint do diagnostico DISSE que ia gravar, e a
        diferenca entre medir e acreditar."""
        self.client.post("/api/v1/student/domain/rebuild")
        r = self.client.get(f"/api/v1/student/domain/content/{content_code}")
        self.assertEqual(r.status_code, 200, r.text)
        return (r.json().get("content") or {}).get("origin_breakdown") or {}

    # -- o cenario principal ------------------------------------------------

    def test_a_atividade_da_escola_chega_pelo_caminho_real(self):
        """Sem isto, tudo o mais seria encenacao: a tarefa tem de existir como
        ActivityAssignment visivel ao aluno, nao como objeto de JavaScript."""
        r = self.client.get("/api/v1/student/activities")
        self.assertEqual(r.status_code, 200, r.text)
        itens = r.json()["items"]
        meu = [i for i in itens if i["assignment_id"] == self.atividade]
        self.assertEqual(len(meu), 1, f"a atividade nao chegou ao aluno: {itens}")
        self.assertEqual(meu[0]["title"], "Atividade de Estequiometria")
        self.assertIn(ESTEQ, meu[0]["content_codes"],
                      "a atividade nao declara o conteudo que exige")

    def test_b_aluno_sem_historico_NAO_vai_direto_para_a_atividade(self):
        p = self._readiness()
        self.assertNotEqual(p["readiness_route"], ROTA_DIRETA,
                            "aluno sem nenhuma evidencia foi liberado para a tarefa")
        self.assertIn(p["readiness_route"], (ROTA_DIAGNOSTICO, ROTA_PREPARACAO))
        self.assertIsNotNone(p["target_content_code"],
                             "a tela ficaria sem proximo passo para oferecer")

    def test_c_a_atividade_continua_sendo_o_objetivo(self):
        """Preparar-se para a tarefa NAO e concluir a tarefa."""
        p = self._readiness()
        self.assertEqual(p["objective_assignment_id"], self.atividade)
        self.assertFalse(p["objective_completed"])

        d = self._abrir_diagnostico(p["target_content_code"])
        self.assertEqual(d["objective_assignment_id"], self.atividade)
        self.assertFalse(d["objective_completed"])

    def test_d_o_microdiagnostico_usa_o_banco_real_e_a_politica_real(self):
        from agente_ia_edu.services.pedagogical_analysis import PerformanceThresholdPolicy

        alvo = self._readiness()["target_content_code"]
        d = self._abrir_diagnostico(alvo)
        self.assertTrue(d["sufficient"], d)
        self.assertEqual(d["origin"], "MICRO_DIAGNOSTIC")
        self.assertEqual(d["question_count"],
                         PerformanceThresholdPolicy.default().min_sample_size,
                         "o tamanho do diagnostico deixou de vir da politica")

    def test_e_ciclo_completo_respondendo_bem_muda_a_decisao(self):
        """O cenario principal do Piloto Zero, do inicio ao fim.

        resposta -> evidencia -> dominio -> NOVA decisao. A ultima asercao e a
        que importa: a rota depois NAO pode ser igual a de antes, senao
        responder o diagnostico nao serviu para nada.
        """
        antes = self._readiness()
        self.assertNotEqual(antes["readiness_route"], ROTA_DIRETA)
        alvo = antes["target_content_code"]
        self.assertEqual(alvo, BALANC,
                         "sem evidencia de nada, o diagnostico tem de comecar "
                         "pelo pre-requisito, nao pelo conteudo final")

        d = self._abrir_diagnostico(alvo)
        self._responder(d["assignment_id"])          # acerta tudo

        # a evidencia existe, e e do tipo certo
        origens = self._origens(alvo)
        self.assertGreaterEqual(origens.get("MICRO_DIAGNOSTIC", 0), 1,
                                f"a evidencia nao foi arquivada como microdiagnostico: {origens}")
        self.assertEqual(origens.get("OFFICIAL_ACTIVITY", 0), 0,
                         f"o diagnostico virou atividade oficial concluida: {origens}")
        self.assertEqual(origens.get("UNKNOWN_ORIGIN", 0), 0,
                         f"a evidencia caiu na quarentena de origem desconhecida: {origens}")

        decisao = self._decisao(d["assignment_id"], alvo)
        self.assertEqual(decisao["decision"], "PROCEED_TO_ACTIVITY", decisao)
        self.assertFalse(decisao["objective_completed"])

        # A rota NAO vira DIRECT, e isso esta certo: dominar Balanceamento
        # nao produz evidencia nenhuma sobre Estequiometria. O que muda - e o
        # que prova que a decisao foi refeita sobre o estado novo - e o ALVO:
        # o pre-requisito saiu da frente e agora a pergunta e sobre o
        # conteudo da propria atividade.
        depois = self._readiness()
        self.assertEqual(depois["target_content_code"], ESTEQ,
                         f"dominou o pre-requisito e o alvo nao avancou: {depois}")
        pre = [p for d in depois["required_contents"]
               for p in d["prerequisites"] if p["code"] == BALANC]
        self.assertTrue(pre and pre[0]["mastered"],
                        f"o pre-requisito nao foi reconhecido como dominado: {depois}")

    def test_f_respondendo_mal_NAO_libera_a_atividade(self):
        """O contrario do caso E, e o que o impede de passar por acaso."""
        antes = self._readiness()
        alvo = antes["target_content_code"]
        d = self._abrir_diagnostico(alvo)
        self._responder(d["assignment_id"], acertos=0)

        decisao = self._decisao(d["assignment_id"], alvo)
        self.assertNotEqual(decisao["decision"], "PROCEED_TO_ACTIVITY", decisao)
        depois = self._readiness()
        self.assertNotEqual(depois["readiness_route"], ROTA_DIRETA,
                            f"errou tudo no pre-requisito e foi liberado: {depois}")
        self.assertEqual(depois["target_content_code"], BALANC,
                         "errou o pre-requisito e o sistema seguiu em frente")

    def test_g_o_diagnostico_nao_aparece_como_atividade_da_escola(self):
        """Ele produz evidencia pedagogica, nao tarefa cumprida. Se vazasse
        para a lista de atividades, o aluno veria uma tarefa que a escola
        nunca mandou."""
        alvo = self._readiness()["target_content_code"]
        d = self._abrir_diagnostico(alvo)

        itens = self.client.get("/api/v1/student/activities").json()["items"]
        ids = {i["assignment_id"] for i in itens}
        self.assertNotIn(d["assignment_id"], ids,
                         "o microdiagnostico apareceu como atividade da escola")

    def test_h_progresso_reflete_o_novo_estado(self):
        antes = self._readiness()
        alvo = antes["target_content_code"]
        # o nome tem de ser capturado AGORA: depois do diagnostico a rota vira
        # DIRECT e deixa de apontar alvo nenhum.
        nome = antes["target_content_name"] or alvo
        d = self._abrir_diagnostico(alvo)
        self._responder(d["assignment_id"])

        r = self.client.get("/api/v1/student/progress")
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        # Meu Progresso fala com o aluno: as faixas trazem NOME de conteudo,
        # nao codigo de taxonomia. Medir pelo codigo aqui mediria o formato
        # errado, nao o produto.
        mostrados = {i for f in (j.get("faixas") or []) for i in (f.get("itens") or [])}
        self.assertTrue(mostrados,
                        f"Meu Progresso ficou vazio depois do diagnostico: {j}")
        self.assertIn(nome, mostrados,
                      f"o conteudo diagnosticado nao apareceu em Meu Progresso: {j}")


    # -- isolamento ---------------------------------------------------------

    def test_i_outro_aluno_NAO_ve_o_piloto(self):
        """O sandbox nao pode vazar para quem nao e dele.

        Nao basta a lista vir vazia: perguntar diretamente pelo id tambem tem
        de ser recusado, e com 404 - um 403 ja confirmaria que a atividade
        existe."""
        outro = "aluno_de_outra_turma"
        self.app.dependency_overrides[get_current_authenticated_context] = \
            lambda: _ctx(user=outro)
        try:
            itens = self.client.get("/api/v1/student/activities").json()["items"]
            self.assertEqual(itens, [], f"o piloto vazou para {outro}: {itens}")

            r = self.client.get(
                f"/api/v1/student/activities/{self.atividade}/readiness")
            self.assertEqual(r.status_code, 404, r.text)
        finally:
            self.app.dependency_overrides[get_current_authenticated_context] = _ctx

    def test_j_o_professor_nao_vira_aluno_do_piloto(self):
        """O professor tem vinculo SCHOOL, nao CLASSROOM: a atividade que ele
        mesmo distribuiu nao aparece como tarefa DELE."""
        self.app.dependency_overrides[get_current_authenticated_context] = \
            lambda: _ctx(user=PROF, role="TEACHER")
        try:
            itens = self.client.get("/api/v1/student/activities").json()["items"]
            ids = {i["assignment_id"] for i in itens}
            self.assertNotIn(self.atividade, ids,
                             "a atividade apareceu como tarefa do professor")
        finally:
            self.app.dependency_overrides[get_current_authenticated_context] = _ctx


    # -- o ciclo nao se repete (teste humano, 2026-10-04) -------------------

    def test_k_errar_a_base_leva_a_PRATICAR_nao_a_rediagnosticar(self):
        """O bug principal que o teste humano encontrou.

        O aluno errou as tres perguntas de Balanceamento. O backend decidia
        PREPARE_PREREQUISITE - certo - e a prontidao da atividade continuava
        DIAGNOSTIC apontando para o MESMO conteudo. "Continuar" abria outro
        microdiagnostico de Balanceamento. E outro.

        O microdiagnostico COLETA EVIDENCIA PARA DECIDIR. Depois que decidiu,
        repeti-lo nao acrescenta nada.
        """
        antes = self._readiness()
        self.assertEqual(antes["next_step"]["kind"], "DIAGNOSTIC")
        alvo = antes["target_content_code"]
        self.assertEqual(alvo, BALANC)

        d = self._abrir_diagnostico(alvo)
        self._responder(d["assignment_id"], acertos=0)

        depois = self._readiness()
        self.assertEqual(depois["readiness_route"], ROTA_PREPARACAO, depois)
        self.assertEqual(depois["next_step"]["kind"], "PRACTICE",
                         f"o aluno foi mandado a rediagnosticar o que ja foi "
                         f"medido: {depois['next_step']}")
        self.assertEqual(depois["next_step"]["content_code"], BALANC,
                         "a pratica deve ser do conteudo que ficou fraco")

    def test_l_acertar_a_base_faz_o_alvo_AVANCAR(self):
        """O outro lado: a decisao muda de verdade, nao so de texto."""
        alvo = self._readiness()["target_content_code"]
        d = self._abrir_diagnostico(alvo)
        self._responder(d["assignment_id"])

        depois = self._readiness()
        self.assertEqual(depois["next_step"]["content_code"], ESTEQ,
                         f"dominou a base e o alvo nao avancou: {depois['next_step']}")

    def test_m_o_proximo_passo_SEMPRE_existe_e_e_acionavel(self):
        """Nunca um estado sem saida: ou ha passo com conteudo, ou ha motivo."""
        for rodada in range(3):
            p = self._readiness()
            passo = p["next_step"]
            with self.subTest(rodada=rodada, kind=passo["kind"]):
                self.assertIn(passo["kind"],
                              ("DIAGNOSTIC", "PRACTICE", "ACTIVITY", "NONE"))
                if passo["kind"] == "NONE":
                    self.assertTrue(passo.get("reason"),
                                    "sem passo E sem motivo: a tela fica muda")
                else:
                    self.assertTrue(passo.get("content_code"),
                                    "passo acionavel sem conteudo alvo")
            if passo["kind"] == "DIAGNOSTIC":
                d = self._abrir_diagnostico(passo["content_code"])
                if not d.get("sufficient"):
                    break
                self._responder(d["assignment_id"], acertos=0)
            else:
                break

    def test_n_o_feedback_vem_do_backend_e_depende_do_resultado(self):
        """Ate 2026-10-04 o texto era montado no JavaScript, e dizia 'agora
        falta Balanceamento' a quem acabara de demonstrar Balanceamento."""
        alvo = self._readiness()["target_content_code"]
        d = self._abrir_diagnostico(alvo)
        self._responder(d["assignment_id"])

        r = self.client.get(
            f"/api/v1/student/micro-diagnostic/{d['assignment_id']}/decision",
            params={"content_code": alvo,
                    "objective_assignment_id": self.atividade})
        self.assertEqual(r.status_code, 200, r.text)
        fb = r.json().get("feedback")
        self.assertIsNotNone(fb, "o backend nao devolveu feedback")
        self.assertEqual(fb["tom"], "BOM")
        texto = (fb["titulo"] + " " + fb["detalhe"]).lower()
        self.assertNotIn("falta", texto,
                         "chamou de falta o que o aluno acabou de demonstrar")

    def test_o_quem_erra_NAO_recebe_texto_de_dominio(self):
        alvo = self._readiness()["target_content_code"]
        d = self._abrir_diagnostico(alvo)
        self._responder(d["assignment_id"], acertos=0)

        fb = self.client.get(
            f"/api/v1/student/micro-diagnostic/{d['assignment_id']}/decision",
            params={"content_code": alvo,
                    "objective_assignment_id": self.atividade}).json()["feedback"]
        self.assertEqual(fb["tom"], "REVISAR")
        texto = (fb["titulo"] + " " + fb["detalhe"]).lower()
        for palavra in ("bom domínio", "muito bem", "você domina"):
            self.assertNotIn(palavra, texto, f"afirmou dominio: {palavra!r}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
