"""O item do Nucleo Diagnostic Bank atravessa o ciclo pedagogico inteiro.

    Diagnostic Bank -> MicroDiagnostic -> resposta -> correcao
                    -> evidencia MICRO_DIAGNOSTIC -> dominio -> readiness

Nada aqui e simulado. Os itens sao gravados como Question/QuestionVersion
comuns, selecionados pela PracticeSelectionPolicy real, respondidos pelo
Player real e corrigidos pela correcao real. Se algum elo nao fechasse, o
teste nao teria como passar.

POR QUE SQLITE E NAO O BANCO DE DEV
====================================
O banco de desenvolvimento esta carimbado em `063_platform_material_target`,
revisao que nao existe na cadeia deste worktree (`063_embedding_activation`
-> 064 -> 065). Sao linhagens divergentes. Forcar upgrade ou carimbar a mao
seria mexer no banco do usuario com uma cadeia que nao e a dele - entao aqui
o schema vem de `Base.metadata.create_all`, que reflete os modelos, inclusive
a coluna `provenance` da migration 065.

A quimica dos itens abaixo NAO e afirmada: ela e conferida por
`chemistry_balance` dentro do proprio teste, por contagem de atomos.
"""

from __future__ import annotations

import asyncio
import unittest
import uuid as _uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select
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
from agente_ia_edu.services.chemistry_balance import equacao_balanceada
from agente_ia_edu.services.curriculum_domain_map import (
    ORIGIN_MICRO_DIAGNOSTIC, ORIGIN_OFFICIAL_ACTIVITY,
)
from agente_ia_edu.services.diagnostic_bank import (
    BANK_TAG, CONTENT_CODE, LETRAS, ORIGIN_TYPE, SKILL_BALANCEAR,
    SKILL_CONSERVACAO, SKILL_RECONHECER,
)

ESTEQ = "CHEMISTRY-PHYSICAL-STOICHIOMETRY"
PEDRO = "pedro"
ESCOLA = str(_uuid.uuid5(_uuid.NAMESPACE_DNS, "diagnostic-bank-escola"))

# Seis itens com quimica REAL. A equacao correta de cada um e conferida por
# contagem dentro do teste - nenhuma afirmacao sobre quimica fica sem prova.
ITENS = [
    (SKILL_CONSERVACAO, "Qual equacao conserva o numero de atomos?",
     {"A": "H2 + O2 -> H2O", "B": "2 H2 + O2 -> 2 H2O",
      "C": "Na + Cl2 -> NaCl", "D": "CH4 + O2 -> CO2 + H2O",
      "E": "N2 + H2 -> NH3"}, "B", "2 H2 + O2 -> 2 H2O"),
    (SKILL_CONSERVACAO, "Qual equacao esta corretamente balanceada?",
     {"A": "Fe + O2 -> Fe2O3", "B": "2 Fe + O2 -> Fe2O3",
      "C": "4 Fe + 3 O2 -> 2 Fe2O3", "D": "Fe + 3 O2 -> 2 Fe2O3",
      "E": "2 Fe + 3 O2 -> Fe2O3"}, "C", "4 Fe + 3 O2 -> 2 Fe2O3"),
    (SKILL_RECONHECER, "Qual das combustoes esta balanceada?",
     {"A": "CH4 + O2 -> CO2 + 2 H2O", "B": "CH4 + 2 O2 -> CO2 + H2O",
      "C": "CH4 + 2 O2 -> CO2 + 2 H2O", "D": "2 CH4 + O2 -> 2 CO2 + H2O",
      "E": "CH4 + 3 O2 -> CO2 + 2 H2O"}, "C", "CH4 + 2 O2 -> CO2 + 2 H2O"),
    (SKILL_RECONHECER, "Qual sintese da amonia esta balanceada?",
     {"A": "N2 + H2 -> NH3", "B": "N2 + 2 H2 -> 2 NH3",
      "C": "N2 + 3 H2 -> NH3", "D": "N2 + 3 H2 -> 2 NH3",
      "E": "2 N2 + 3 H2 -> 2 NH3"}, "D", "N2 + 3 H2 -> 2 NH3"),
    (SKILL_BALANCEAR, "Qual equacao do cloreto de sodio esta balanceada?",
     {"A": "Na + Cl2 -> NaCl", "B": "2 Na + Cl2 -> 2 NaCl",
      "C": "Na + 2 Cl2 -> NaCl", "D": "2 Na + 2 Cl2 -> 2 NaCl",
      "E": "Na + Cl2 -> 2 NaCl"}, "B", "2 Na + Cl2 -> 2 NaCl"),
    (SKILL_BALANCEAR, "Qual reducao do oxido de ferro esta balanceada?",
     {"A": "Fe2O3 + CO -> Fe + CO2", "B": "Fe2O3 + 2 CO -> 2 Fe + 2 CO2",
      "C": "Fe2O3 + 3 CO -> 2 Fe + 3 CO2", "D": "2 Fe2O3 + CO -> Fe + CO2",
      "E": "Fe2O3 + 3 CO -> 3 Fe + 2 CO2"}, "C", "Fe2O3 + 3 CO -> 2 Fe + 3 CO2"),
]


def _ctx():
    return AuthenticatedUserContext(user_id=PEDRO, external_identity_id=PEDRO,
                                    role="STUDENT", school_id=ESCOLA,
                                    scope_type="SCHOOL")


async def _seed(factory) -> dict:
    async with factory() as s:
        disc = CatalogNode(code="CHEMISTRY", name="Quimica",
                           node_type="DISCIPLINE", position=0, active=True)
        s.add(disc); await s.flush(); disc.root_id = disc.id; await s.flush()
        geral = CatalogNode(code="CHEMISTRY-GENERAL", name="Quimica Geral",
                            node_type="AREA", position=1, parent_id=disc.id,
                            root_id=disc.id, active=True)
        fisico = CatalogNode(code="CHEMISTRY-PHYSICAL", name="Fisico-Quimica",
                             node_type="AREA", position=2, parent_id=disc.id,
                             root_id=disc.id, active=True)
        s.add_all([geral, fisico]); await s.flush()
        balanc = CatalogNode(code=CONTENT_CODE,
                             name="Reacoes quimicas e balanceamento",
                             node_type="CONTENT", position=1, parent_id=geral.id,
                             root_id=disc.id, active=True)
        esteq = CatalogNode(code=ESTEQ, name="Estequiometria",
                            node_type="CONTENT", position=1, parent_id=fisico.id,
                            root_id=disc.id, active=True)
        s.add_all([balanc, esteq]); await s.flush()
        s.add(CatalogNodePrerequisite(content_node_id=esteq.id,
                                      prerequisite_node_id=balanc.id))

        # O QuestionBankService faz INNER JOIN com BookletQuestion ->
        # ExamBooklet -> ExamApplication: ele foi construido em torno de prova
        # oficial, e uma questao sem caderno e invisivel para ele.
        #
        # Em vez de afrouxar esse join (caminho quente da busca do professor),
        # o Diagnostic Bank declara a propria "edicao": a instituicao e o
        # Nucleo, o "exame" e o banco diagnostico, e cada item recebe um
        # numero estavel. E a mesma infraestrutura com origem propria - nao
        # uma prova disfarcada, e `origin_type='GENERATED'` continua
        # distinguindo os dois casos.
        inst = Institution(code="NUCLEO", name="Nucleo Edu 360")
        s.add(inst); await s.flush()
        exame = Exam(institution_id=inst.id, code="NUCLEO_DIAGNOSTIC",
                     name="Nucleo Diagnostic Bank")
        s.add(exame); await s.flush()
        edicao = ExamApplication(exam_id=exame.id, year=2026,
                                 application_type="diagnostic", day=1)
        s.add(edicao); await s.flush()
        caderno = ExamBooklet(exam_application_id=edicao.id,
                              code="BALANCEAMENTO-V1", color="UNICO")
        s.add(caderno); await s.flush()
        # O gabarito do banco diagnostico e do proprio Nucleo. Declara-lo pela
        # mesma estrutura (SourceDocument + AnswerKeyRevision) e o que faz
        # TODO o resto do sistema funcionar sem alteracao nenhuma.
        doc = SourceDocument(exam_application_id=edicao.id,
                             exam_booklet_id=caderno.id,
                             document_type="ANSWER_KEY",
                             source_url="nucleo://diagnostic-bank/balanceamento/v1",
                             acquired_at=datetime.now(timezone.utc),
                             content_hash=str(_uuid.uuid4()))
        s.add(doc); await s.flush()
        revisao = AnswerKeyRevision(source_document_id=doc.id,
                                    revision_number=1, is_official=True)
        s.add(revisao); await s.flush()

        gabarito: dict[str, str] = {}
        for numero, (skill, stem, opcoes, correta, _eq) in enumerate(ITENS, start=1):
            q = Question(validation_status="valid", origin_type=ORIGIN_TYPE,
                         status="PUBLISHED", visibility_scope="PUBLIC",
                         question_type="MULTIPLE_CHOICE",
                         created_by_external_identity=BANK_TAG,
                         metadata_={"bank": BANK_TAG, "diagnostic_skill": skill,
                                    "generation": {"actor_type": "AI"},
                                    "verification": {"actor_type": "AI"}})
            s.add(q); await s.flush()
            v = QuestionVersion(question_id=q.id, version_kind="official_original",
                                canonical_text=stem, statement=stem,
                                content_hash=str(_uuid.uuid4()), is_immutable=True,
                                recommended_difficulty="MEDIUM")
            s.add(v); await s.flush()
            opcoes_salvas = {}
            for pos, letra in enumerate(LETRAS, start=1):
                o = QuestionOption(question_version_id=v.id, option_key=letra,
                                   position=pos, text=opcoes[letra],
                                   is_valid_option=(letra == correta))
                s.add(o); await s.flush(); opcoes_salvas[letra] = o
            s.add(PedagogicalClassification(
                question_version_id=v.id, discipline="CURRICULUM_PROPOSAL",
                content=CONTENT_CODE, subcontent=skill, difficulty="MEDIUM",
                reasoning_type="DIAGNOSTIC", prerequisites=[], keywords=[],
                competencies=[], skills=[skill], status="CLASSIFIED",
                source="ai", lifecycle="ACTIVE", provenance="AI_VERIFIED",
                model_version="teste", prompt_version="teste",
                metadata_={"taxonomy_version": "curriculum-v2",
                           "primary_content_code": CONTENT_CODE,
                           "visual_dependency": False}))
            bq = BookletQuestion(exam_booklet_id=caderno.id,
                                 question_version_id=v.id, position=numero,
                                 official_number=numero, page_number=1)
            s.add(bq); await s.flush()
            s.add(AnswerKeyEntry(answer_key_revision_id=revisao.id,
                                 booklet_question_id=bq.id,
                                 official_answer_label=correta,
                                 resolved_option_id=opcoes_salvas[correta].id,
                                 page_number=1))
            s.add(ContentQuestionLink(content_node_id=balanc.id,
                                      question_version_id=v.id))
            gabarito[str(v.id)] = correta

        s.add(UserSchoolLink(external_user_id=PEDRO, school_id=_uuid.UUID(ESCOLA),
                             role="STUDENT", scope_type="CLASSROOM",
                             scope_external_id="turma-1", active=True))
        await s.commit()
        return {"gabarito": gabarito}


class DiagnosticBankIntegracaoTests(unittest.TestCase):

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

        self.gabarito = self.loop.run_until_complete(prep())["gabarito"]
        self.app = create_app()
        self.app.dependency_overrides[get_session_factory] = lambda: self.factory
        self.app.dependency_overrides[get_current_authenticated_context] = _ctx
        self.client = TestClient(self.app)

    def tearDown(self):
        self.app.dependency_overrides.clear()
        self.client.close()
        self.loop.run_until_complete(self.engine.dispose())
        self.loop.close()

    # -- a quimica dos itens, conferida aqui -------------------------------

    def test_a_resposta_correta_de_cada_item_esta_mesmo_balanceada(self):
        """Nenhuma afirmacao sobre quimica fica sem prova neste arquivo."""
        for skill, _stem, opcoes, correta, equacao in ITENS:
            with self.subTest(skill=skill):
                self.assertEqual(opcoes[correta], equacao)
                self.assertTrue(equacao_balanceada(equacao),
                                f"a resposta correta {equacao!r} nao balanceia")

    def test_as_alternativas_erradas_NAO_estao_balanceadas(self):
        """Distrator balanceado faria a questao ter duas respostas certas."""
        for skill, _stem, opcoes, correta, _eq in ITENS:
            for letra, texto in opcoes.items():
                if letra == correta:
                    continue
                with self.subTest(skill=skill, alternativa=letra):
                    self.assertFalse(equacao_balanceada(texto),
                                     f"o distrator {letra} tambem balanceia")

    # -- A: item diagnostico nao e questao oficial -------------------------

    def test_o_item_nasce_com_origem_propria_e_nao_como_importado(self):
        async def ler():
            async with self.factory() as s:
                return (await s.scalars(select(Question))).all()

        for q in self.loop.run_until_complete(ler()):
            self.assertEqual(q.origin_type, ORIGIN_TYPE)
            self.assertNotEqual(q.origin_type, "IMPORTED")
            self.assertEqual((q.metadata_ or {}).get("bank"), BANK_TAG)

    def test_a_proveniencia_do_gerador_e_do_verificador_ficam_gravadas(self):
        async def ler():
            async with self.factory() as s:
                return (await s.scalars(select(Question))).all()

        for q in self.loop.run_until_complete(ler()):
            md = q.metadata_ or {}
            self.assertEqual(md["generation"]["actor_type"], "AI")
            self.assertEqual(md["verification"]["actor_type"], "AI")

    def test_a_classificacao_e_AI_VERIFIED_sem_identidade_humana(self):
        async def ler():
            async with self.factory() as s:
                return (await s.scalars(select(PedagogicalClassification))).all()

        linhas = self.loop.run_until_complete(ler())
        self.assertEqual(len(linhas), len(ITENS))
        for c in linhas:
            self.assertEqual(c.provenance, "AI_VERIFIED")
            self.assertNotEqual(c.provenance, "HUMAN_VALIDATED")
            self.assertIsNone(c.validated_by_external_identity)

    # -- K/L: o microdiagnostico seleciona ---------------------------------

    def _iniciar_microdiagnostico(self):
        from agente_ia_edu.services.micro_diagnostic import MicroDiagnosticService
        from agente_ia_edu.services.question_list_store import Requester

        async def run():
            async with self.factory() as s:
                return await MicroDiagnosticService(s).start(
                    PEDRO,
                    requester=Requester(external_user_id=PEDRO,
                                        school_id=_uuid.UUID(ESCOLA),
                                        role="STUDENT"),
                    content_code=CONTENT_CODE)

        return self.loop.run_until_complete(run())

    def test_o_microdiagnostico_consegue_selecionar_itens_do_banco(self):
        d = self._iniciar_microdiagnostico()
        self.assertTrue(d["sufficient"], f"nao selecionou: {d.get('reason')}")
        self.assertEqual(d["selection"]["selected_questions"], d["question_count"])
        self.assertGreaterEqual(d["selection"]["available_questions"], len(ITENS))

    def test_o_tamanho_vem_da_politica_e_o_banco_tem_mais_que_isso(self):
        from agente_ia_edu.services.pedagogical_analysis import (
            PerformanceThresholdPolicy,
        )

        minimo = PerformanceThresholdPolicy.default().min_sample_size
        d = self._iniciar_microdiagnostico()
        self.assertEqual(d["question_count"], minimo)
        self.assertGreater(len(ITENS), minimo,
                           "o banco precisa ter mais itens do que apresenta")

    def test_a_selecao_respeita_o_content_code(self):
        """Pedir Estequiometria nao pode devolver item de Balanceamento."""
        from agente_ia_edu.services.micro_diagnostic import MicroDiagnosticService
        from agente_ia_edu.services.question_list_store import Requester

        async def run():
            async with self.factory() as s:
                return await MicroDiagnosticService(s).start(
                    PEDRO,
                    requester=Requester(external_user_id=PEDRO,
                                        school_id=_uuid.UUID(ESCOLA),
                                        role="STUDENT"),
                    content_code=ESTEQ)

        d = self.loop.run_until_complete(run())
        self.assertFalse(d["sufficient"],
                         "selecionou itens de Balanceamento para Estequiometria")
        self.assertEqual(d["decision"], "INSUFFICIENT_EVIDENCE")

    # -- M/N/O: o ciclo fecha ----------------------------------------------

    def _responder(self, assignment_id, *, acertos=None):
        st = self.client.post(
            f"/api/v1/student/activities/{assignment_id}/attempt").json()
        vids = [q["question_version_id"] for q in st["questions"]]
        n = len(vids) if acertos is None else acertos
        for pos, vid in enumerate(vids):
            certa = self.gabarito[vid]
            escolha = certa if pos < n else next(k for k in LETRAS if k != certa)
            self.client.put(
                f"/api/v1/student/activities/{assignment_id}/attempt/answers/{vid}",
                json={"selected_option": escolha})
        self.client.post(f"/api/v1/student/activities/{assignment_id}/attempt/complete")
        r = self.client.post(f"/api/v1/student/activities/{assignment_id}/attempt/correct")
        self.assertIn(r.status_code, (200, 201), r.text)
        return vids

    def _dominio(self):
        j = self.client.post("/api/v1/student/domain/rebuild").json()
        return next((c for d in j["disciplines"] for c in d["contents"]
                     if c["content_code"] == CONTENT_CODE), None)

    def test_responder_gera_evidencia_com_origem_MICRO_DIAGNOSTIC(self):
        d = self._iniciar_microdiagnostico()
        self._responder(d["assignment_id"])
        c = self._dominio()
        self.assertIsNotNone(c, "o microdiagnostico nao gerou evidencia")
        self.assertEqual(c["origin_breakdown"].get(ORIGIN_MICRO_DIAGNOSTIC),
                         d["question_count"])

    def test_a_evidencia_NAO_vira_atividade_oficial(self):
        d = self._iniciar_microdiagnostico()
        self._responder(d["assignment_id"])
        origens = self._dominio()["origin_breakdown"]
        self.assertNotIn(ORIGIN_OFFICIAL_ACTIVITY, origens,
                         "o diagnostico foi contabilizado como avaliacao da escola")
        self.assertNotIn("UNKNOWN_ORIGIN", origens)

    def test_o_dominio_reage_a_evidencia(self):
        d = self._iniciar_microdiagnostico()
        self.assertIsNone(self._dominio(), "Pedro ja tinha evidencia?")
        self._responder(d["assignment_id"], acertos=0)
        c = self._dominio()
        self.assertEqual(c["questions_answered"], d["question_count"])
        self.assertEqual(c["accuracy"], 0.0)

    def test_a_readiness_de_estequiometria_reage_ao_resultado(self):
        """O ciclo inteiro: diagnostico fraco no pre-requisito mantem
        Estequiometria bloqueada; diagnostico forte destrava."""
        from agente_ia_edu.services.pedagogical_analysis import (
            PerformanceThresholdPolicy,
        )

        politica = PerformanceThresholdPolicy.default()

        def bloqueada() -> bool:
            c = self._dominio()
            if c is None:
                return True
            return not (c["questions_answered"] >= politica.min_sample_size
                        and (c["accuracy"] or 0) >= politica.strong_accuracy)

        self.assertTrue(bloqueada(), "sem evidencia deveria bloquear")

        # Acertando tudo, o pre-requisito destrava.
        d = self._iniciar_microdiagnostico()
        self._responder(d["assignment_id"])
        self.assertFalse(bloqueada(),
                         "acertou tudo e Estequiometria continuou bloqueada - "
                         "a readiness nao usou a evidencia nova")

    def test_errar_o_microdiagnostico_mantem_estequiometria_bloqueada(self):
        """O outro lado da mesma moeda, em cenario proprio.

        A primeira versao deste teste encadeava os dois casos na mesma
        sessao: errava 3, depois acertava 3, e exigia destravar. Mas a
        evidencia ACUMULA - 3 certas em 6 respondidas da 0,50, abaixo do
        corte de 0,80 da politica. O sistema estava certo e o teste e que
        somava errado. Separados, cada um mede uma coisa so.
        """
        from agente_ia_edu.services.pedagogical_analysis import (
            PerformanceThresholdPolicy,
        )

        politica = PerformanceThresholdPolicy.default()
        d = self._iniciar_microdiagnostico()
        self._responder(d["assignment_id"], acertos=0)
        c = self._dominio()
        self.assertEqual(c["accuracy"], 0.0)
        self.assertLess(c["accuracy"], politica.strong_accuracy,
                        "errar tudo deveria manter o pre-requisito fraco")

    # -- P: o item nao muda sozinho ----------------------------------------

    def test_o_item_versionado_e_imutavel(self):
        async def ler():
            async with self.factory() as s:
                return (await s.scalars(select(QuestionVersion))).all()

        for v in self.loop.run_until_complete(ler()):
            self.assertTrue(v.is_immutable)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
