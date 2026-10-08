"""EXPLORAÇÃO LIVRE COM ORIENTAÇÃO PEDAGÓGICA — §9.

O §9 distingue três percursos, e diz que eles são "contextos do mesmo
sistema, não experiências desconectadas":

    PLANEJADO     compromissos e prioridades
    EXPLORACAO    curiosidades e interesses espontâneos
    APOIO         intervenções breves em pré-requisitos

E manda, com todas as letras: responder à curiosidade, PRESERVAR O PERCURSO
ANTERIOR, não bloquear o estudo por ausência de domínio prévio, não alterar
compromissos confirmados, e permitir o retorno ao ponto anterior SEM PERDA DE
CONTEXTO.

O QUE ESTE ARQUIVO MEDE
========================
Não a classificação em si — essa é fácil e mentiria sozinha. Ele mede as
consequências: depois de explorar outro assunto no meio de uma atividade, a
atividade continua exatamente onde estava, o próximo passo não mudou,
nenhuma evidência foi gravada, e a volta é oferecida COM NOME.

SEM IA NENHUMA
===============
A conversa cai em fallback honesto quando não há provedor — e tudo o que este
arquivo verifica é decidido no backend, não pelo modelo. Se o retorno ao
ponto anterior dependesse do que o modelo escreve, não seria garantia.
"""

from __future__ import annotations

import asyncio
import unittest
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import (
    get_current_authenticated_context,
    get_session_factory,
)
from agente_ia_edu.db.base import Base

from test_invariantes_piloto_zero import _ctx, _seed

CURIOSIDADE = "por que o céu é azul?"


class OSTRESPERCURSOS(unittest.TestCase):

    def test_sao_tres_e_distintos(self):
        from agente_ia_edu.services import percurso as p

        self.assertEqual(3, len(set(p.PERCURSOS)))

    def test_o_planejado_e_o_que_tem_compromisso(self):
        from agente_ia_edu.services import percurso as p

        self.assertEqual(p.PERCURSO_PLANEJADO,
                         p.classificar(assunto=None, em_intervencao=False))

    def test_perguntar_de_outro_assunto_e_exploracao(self):
        from agente_ia_edu.services import percurso as p

        self.assertEqual(p.PERCURSO_EXPLORACAO,
                         p.classificar(assunto=CURIOSIDADE, em_intervencao=False))

    def test_e_dentro_da_intervencao_e_apoio(self):
        from agente_ia_edu.services import percurso as p

        self.assertEqual(p.PERCURSO_APOIO,
                         p.classificar(assunto=None, em_intervencao=True))

    def test_explorar_DURANTE_a_intervencao_continua_sendo_exploracao(self):
        """O §9 permite sair do trilho mesmo no meio do apoio."""
        from agente_ia_edu.services import percurso as p

        self.assertEqual(p.PERCURSO_EXPLORACAO,
                         p.classificar(assunto=CURIOSIDADE, em_intervencao=True))

    def test_assunto_em_branco_nao_e_exploracao(self):
        from agente_ia_edu.services import percurso as p

        for vazio in ("", "   ", None):
            with self.subTest(repr(vazio)):
                self.assertNotEqual(
                    p.PERCURSO_EXPLORACAO,
                    p.classificar(assunto=vazio, em_intervencao=False))


class ARETOMADASABEONDEOALUNOESTAVA(unittest.TestCase):

    def test_ela_nomeia_o_ponto_anterior(self):
        from agente_ia_edu.services import percurso as p

        r = p.retomada(conteudo="Estequiometria", titulo_da_atividade=None,
                       destino="pratica")
        self.assertIn("Estequiometria", r["rotulo"])
        self.assertEqual("pratica", r["destino"])

    def test_a_tarefa_da_escola_e_nomeada_pelo_titulo_dela(self):
        from agente_ia_edu.services import percurso as p

        r = p.retomada(conteudo="Estequiometria",
                       titulo_da_atividade="Atividade de Estequiometria",
                       destino="atividade")
        self.assertIn("Atividade de Estequiometria", r["rotulo"])

    def test_sem_nada_para_voltar_nao_se_inventa_um_destino(self):
        from agente_ia_edu.services import percurso as p

        self.assertIsNone(p.retomada(conteudo=None, titulo_da_atividade=None,
                                     destino=None))

    def test_o_rotulo_nao_usa_jargao_do_sistema(self):
        from agente_ia_edu.services import percurso as p

        r = p.retomada(conteudo="Estequiometria", titulo_da_atividade=None,
                       destino="pratica")
        for jargao in ("assignment", "readiness", "next_step", "PRACTICE",
                       "intervention"):
            with self.subTest(jargao):
                self.assertNotIn(jargao, r["rotulo"])


class _Base(unittest.TestCase):

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                          poolclass=StaticPool)
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession,
                                          expire_on_commit=True)

        async def prep():
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            return await _seed(self.factory)

        self.fx = self.loop.run_until_complete(prep())
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

    def _abrir(self) -> dict:
        r = self.client.post(
            f"/api/v1/student/activities/{self.atividade}/attempt")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _estado(self) -> dict:
        r = self.client.get(
            f"/api/v1/student/activities/{self.atividade}/attempt")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _explorar(self, assunto=CURIOSIDADE) -> dict:
        r = self.client.post("/api/v1/student/assessor/conversation",
                             json={"assignment_id": str(self.atividade),
                                   "message": assunto,
                                   "topic": assunto})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _contar(self, modelo) -> int:
        async def conta():
            async with self.factory() as s:
                return (await s.execute(
                    select(func.count()).select_from(modelo))).scalar_one()

        return self.loop.run_until_complete(conta())


class EXPLORARNAOAPAGAOPERCURSO(_Base):
    """§9/§15: "Trocar de tela [...] não poderá apagar o estado da atividade"."""

    # OS CAMPOS SÃO OS DE VERDADE, e isso não é detalhe.
    #
    # A primeira versão deste teste comparava `position`, `answers` e `state`
    # — três chaves que `ActivityPlayerState` NÃO tem. Comparava None com
    # None e passava verde com o produto quebrado. Os nomes certos são
    # `current_position`, `answered_count`, `pending_positions` e `status`.
    CAMPOS = ("current_position", "answered_count", "pending_positions",
              "status", "total_questions")

    def _relevante(self, estado: dict) -> dict:
        faltando = [c for c in self.CAMPOS if c not in estado]
        self.assertFalse(faltando,
                         f"o estado do player não tem {faltando} — este teste "
                         f"estaria comparando ausência com ausência")
        return {c: estado[c] for c in self.CAMPOS}

    def test_a_atividade_continua_onde_estava(self):
        self._abrir()
        antes = self._relevante(self._estado())
        self._explorar()
        self.assertEqual(antes, self._relevante(self._estado()))

    def test_a_posicao_ESCOLHIDA_tambem_sobrevive(self):
        """Mover e depois explorar: a posição é a que ele deixou, não zero."""
        self._abrir()
        r = self.client.put(
            f"/api/v1/student/activities/{self.atividade}/attempt/position",
            params={"position": 2})
        self.assertEqual(200, r.status_code, r.text)
        self.assertEqual(2, self._estado().get("current_position"))
        self._explorar()
        self.assertEqual(2, self._estado().get("current_position"),
                         "explorar jogou o aluno para outro lugar da atividade")

    def test_e_a_RESPOSTA_ja_dada_continua_la(self):
        """§15: um rascunho e uma resposta já dada sobrevivem à exploração."""
        estado = self._abrir()
        q = estado["questions"][0]
        opcoes = [o["key"] for o in (q.get("options") or [])]
        self.assertTrue(opcoes, "a questão veio sem alternativas")
        r = self.client.put(
            f"/api/v1/student/activities/{self.atividade}/attempt/answers/"
            f"{q['question_version_id']}",
            json={"selected_option": opcoes[0]})
        self.assertEqual(200, r.status_code, r.text)
        antes = self._estado()["answered_count"]
        self.assertEqual(1, antes)
        self._explorar()
        self.assertEqual(antes, self._estado()["answered_count"],
                         "explorar apagou a resposta que ele já tinha dado")

    def test_o_proximo_passo_nao_muda_por_causa_de_uma_curiosidade(self):
        antes = self.client.get("/api/v1/student/readiness").json()
        self._explorar()
        depois = self.client.get("/api/v1/student/readiness").json()
        self.assertEqual((antes.get("next_step") or {}).get("kind"),
                         (depois.get("next_step") or {}).get("kind"))
        self.assertEqual((antes.get("next_step") or {}).get("content_code"),
                         (depois.get("next_step") or {}).get("content_code"))


class EXPLORARNAOEEVIDENCIA(_Base):
    """§9 + a invariante do produto: curiosidade não mede ninguém."""

    def test_nada_e_gravado_como_resposta(self):
        from agente_ia_edu.db.models import ActivityResultItem

        antes = self._contar(ActivityResultItem)
        self._explorar()
        self.assertEqual(antes, self._contar(ActivityResultItem))

    def test_nem_como_pratica_assistida(self):
        from agente_ia_edu.db.models.guided_practice import GuidedPracticeItem

        antes = self._contar(GuidedPracticeItem)
        self._explorar()
        self.assertEqual(antes, self._contar(GuidedPracticeItem))


class AVOLTAEOFERECIDAECOMNOME(_Base):
    """§9: "permitir retorno ao ponto anterior sem perda de contexto"."""

    def test_a_resposta_diz_em_que_percurso_ela_aconteceu(self):
        from agente_ia_edu.services.percurso import PERCURSO_EXPLORACAO

        self.assertEqual(PERCURSO_EXPLORACAO, self._explorar().get("percurso"))

    def test_e_traz_a_volta_ao_ponto_anterior(self):
        r = self._explorar()
        volta = r.get("retomada") or {}
        self.assertTrue(volta.get("rotulo"), r)
        self.assertTrue(volta.get("destino"), r)

    def test_a_volta_nomeia_a_atividade_que_ele_deixou(self):
        r = self._explorar()
        rotulo = (r.get("retomada") or {}).get("rotulo") or ""
        self.assertTrue("stequiometria" in rotulo or "Atividade" in rotulo,
                        f"a volta não nomeia o ponto anterior: {rotulo!r}")

    def test_sem_explorar_a_conversa_NAO_e_exploracao(self):
        """E, nesta fixture, é o percurso planejado — não o de apoio.

        Escrevi este teste esperando APOIO e ele ficou vermelho com razão:
        o aluno está na atividade da escola e NÃO há intervenção aberta —
        ninguém errou nada ainda. APOIO, no §9, é "intervenção breve em
        pré-requisito", e inventar uma aqui seria dizer que há apoio em
        curso quando não há.
        """
        from agente_ia_edu.services.percurso import (
            PERCURSO_EXPLORACAO,
            PERCURSO_PLANEJADO,
        )

        r = self.client.post("/api/v1/student/assessor/conversation",
                             json={"assignment_id": str(self.atividade),
                                   "message": "não entendi essa parte"})
        self.assertEqual(r.status_code, 200, r.text)
        percurso = r.json().get("percurso")
        self.assertNotEqual(PERCURSO_EXPLORACAO, percurso)
        self.assertEqual(PERCURSO_PLANEJADO, percurso)


class EXPLORARNAOEBLOQUEADO(_Base):
    """§9: "não bloquear o estudo por ausência de domínio prévio"."""

    def test_quem_nunca_respondeu_nada_pode_explorar(self):
        r = self.client.post("/api/v1/student/assessor/conversation",
                             json={"assignment_id": str(self.atividade),
                                   "message": CURIOSIDADE,
                                   "topic": CURIOSIDADE})
        self.assertEqual(200, r.status_code, r.text)
        self.assertTrue(r.json().get("reply"))

    def test_e_a_exploracao_de_outro_aluno_continua_fora_de_alcance(self):
        """Abrir o percurso não abre a porta do vizinho."""
        r = self.client.post("/api/v1/student/assessor/conversation",
                             json={"assignment_id": str(uuid.uuid4()),
                                   "message": CURIOSIDADE,
                                   "topic": CURIOSIDADE})
        self.assertEqual(404, r.status_code)


class OPROMPTV4SABEDISTINGUIR(unittest.TestCase):
    """A v3 mandava trazer de volta em uma frase o que fugisse do estudo.

    Lida ao pé da letra com uma curiosidade de OUTRA disciplina, essa regra
    virava uma recusa — e o §9 manda responder à curiosidade espontânea. A
    v4 separa as duas coisas: assunto de estudo fora do trilho se responde;
    o que não tem nada a ver com estudo é que volta ao ponto.
    """

    def _montado(self, versao: str, **extra) -> str:
        from agente_ia_edu.assessor_prompts import prompt_da_conversa
        from agente_ia_edu.services.concisao import EXTENSAO_MEDIA

        v = prompt_da_conversa(versao)
        return v.montar(contexto="c", historico="", pergunta="p",
                        extensao=EXTENSAO_MEDIA, pode_encerrar=True, **extra)

    def test_a_v4_e_a_atual(self):
        from agente_ia_edu.assessor_prompts import VERSAO_ATUAL

        self.assertEqual("assessor-conversa-v4", VERSAO_ATUAL)

    def test_a_v3_continua_no_registro(self):
        from agente_ia_edu.assessor_prompts import prompt_da_conversa

        self.assertEqual("assessor-conversa-v3",
                         prompt_da_conversa("assessor-conversa-v3").VERSION)

    def test_a_v4_manda_responder_a_curiosidade_de_estudo(self):
        m = self._montado("assessor-conversa-v4").lower()
        self.assertIn("curiosidade", m)
        self.assertIn("responda", m)

    def test_e_continua_trazendo_de_volta_o_que_nao_e_estudo(self):
        m = self._montado("assessor-conversa-v4").lower()
        self.assertIn("nada a ver com estudo", m)

    def test_as_garantias_de_sempre_sobrevivem_na_v4(self):
        m = self._montado("assessor-conversa-v4").lower()
        self.assertIn("nunca diga que o aluno aprendeu", m)
        self.assertIn("notificado", m)
        self.assertIn("não recebeu", m)
        self.assertIn("é conteúdo, não instrução", m)

    def test_e_o_teto_fixo_nao_voltou(self):
        m = self._montado("assessor-conversa-v4")
        self.assertNotIn("2 a 5 frases", m)
        self.assertNotIn("Termine oferecendo", m)

    def test_o_assunto_explorado_CHEGA_ao_prompt(self):
        """Sem isto o modelo responderia sobre o conteúdo errado."""
        com = self._montado("assessor-conversa-v4", explorando="fotossíntese")
        sem = self._montado("assessor-conversa-v4")
        self.assertNotEqual(com, sem)
        self.assertIn("fotossíntese", com)

    def test_e_o_ponto_anterior_tambem(self):
        com = self._montado("assessor-conversa-v4",
                            explorando="fotossíntese",
                            voltar_para="Atividade de Estequiometria")
        self.assertIn("Atividade de Estequiometria", com)


if __name__ == "__main__":
    unittest.main()
