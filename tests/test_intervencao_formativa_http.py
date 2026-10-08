"""A INTERVENÇÃO ATRAVESSA A API — e é aí que ela tinha falhado.

POR QUE ESTE ARQUIVO EXISTE, ALÉM DO DE SERVIÇO
================================================
`test_intervencao_formativa` prova a decisão no serviço, e passava verde
enquanto a plataforma, no navegador, continuava servindo a próxima questão.
Dois defeitos moravam exatamente entre o serviço e o cliente, e nenhum teste
de serviço podia vê-los:

1. **MissingGreenlet engolido.** `save_answer` lia `row.metadata_` DEPOIS do
   commit; com `expire_on_commit=True` — como a aplicação — isso tenta
   recarregar fora do contexto async. O `except` da decisão engolia, e a
   resposta errada voltava com `may_advance: true`. O teste de serviço usava
   `expire_on_commit=False` e por isso não via.

2. **O `response_model` descartando o campo.** O serviço calculava
   `pending_intervention` corretamente e `ActivityPlayerState` não o
   declarava, então o Pydantic o removia em silêncio. O F5 devolvia a
   próxima questão.

Os dois só aparecem com a resposta HTTP de verdade. É o que este arquivo faz.
"""

from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import unittest

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import (
    get_current_authenticated_context,
    get_session_factory,
)
from agente_ia_edu.db.base import Base

from test_invariantes_piloto_zero import _ctx, _seed

_RAIZ = pathlib.Path(__file__).resolve().parent.parent

# O CONTEUDO REAL, e nao o do seed.
#
# `_seed` usa "QUIM-ESTEQUIOMETRIA", um codigo de fixture: nele nao ha
# micro-habilidade que o Nucleo saiba investigar, e a decisao devolve {}
# corretamente. Para exercitar a intervencao e preciso o conteudo de
# verdade, com os itens curados publicados nele.
CONTEUDO = "CHEMISTRY-PHYSICAL-STOICHIOMETRY"


def _publicador():
    """O publicador da sondagem, para o seed ter micro-habilidades REAIS.

    `_seed` cria questoes com `SKILL_A`/`SKILL_B`, que nao tem cadeia de
    microperguntas escrita - e sem alvo a decisao devolve {} corretamente.
    Para exercitar a intervencao, o conteudo precisa ter pelo menos um item
    de uma micro-habilidade que o Nucleo saiba investigar.
    """
    caminho = _RAIZ / "scripts/publicar_sondagem_estequiometria.py"
    spec = importlib.util.spec_from_file_location("pub_sond_http", caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Base(unittest.TestCase):

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                          poolclass=StaticPool)
        # COMO A APLICAÇÃO. Com False, o MissingGreenlet de `save_answer` não
        # aparece — foi assim que ele chegou ao navegador.
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession,
                                          expire_on_commit=True)

        async def prep():
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            return await _seed(self.factory)

        self.fx = self.loop.run_until_complete(prep())

        async def curados():
            """O no curricular real + os cinco itens curados."""
            import uuid as _uuid

            from agente_ia_edu.db.models import CatalogNode

            async with self.factory() as s:
                disc = CatalogNode(id=_uuid.uuid4(), code="CHEMISTRY",
                                   name="Quimica", node_type="DISCIPLINE",
                                   position=0, active=True)
                s.add(disc)
                await s.flush()
                disc.root_id = disc.id
                s.add(CatalogNode(id=_uuid.uuid4(), code=CONTEUDO,
                                  name="Estequiometria", node_type="CONTENT",
                                  position=9, parent_id=disc.id,
                                  root_id=disc.id, active=True))
                await s.commit()
            async with self.factory() as s:
                await _publicador().publicar(s)

        self.loop.run_until_complete(curados())
        self.app = create_app()
        self.app.dependency_overrides[get_session_factory] = lambda: self.factory
        self.app.dependency_overrides[get_current_authenticated_context] = _ctx
        self.client = TestClient(self.app)

    def tearDown(self):
        self.app.dependency_overrides.clear()
        self.client.close()
        self.loop.run_until_complete(self.engine.dispose())
        self.loop.close()

    # -- o percurso, por HTTP ---------------------------------------------

    def _pratica(self, purpose="PRACTICE") -> str:
        r = self.client.post("/api/v1/student/practice",
                             json={"content_code": CONTEUDO,
                                   "question_count": 5,
                                   "purpose": purpose})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["assignment_id"]

    def _abrir(self, pid: str) -> dict:
        r = self.client.post(f"/api/v1/student/activities/{pid}/attempt")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _estado(self, pid: str) -> dict:
        r = self.client.get(f"/api/v1/student/activities/{pid}/attempt")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _responder(self, pid: str, vid: str, opcao: str) -> dict:
        r = self.client.put(
            f"/api/v1/student/activities/{pid}/attempt/answers/{vid}",
            json={"selected_option": opcao})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _opcoes(self, questao: dict) -> list[str]:
        return [o["key"] for o in (questao.get("options") or [])]

    def _investigavel(self, estado: dict) -> dict:
        """A questão do lote cuja micro-habilidade o Núcleo sabe investigar.

        Procurada pelo ENUNCIADO curado, e não por índice: a ordem do lote é
        do banco, e fixá-la aqui tornaria o teste refém dela.
        """
        for q in estado["questions"]:
            if "NH" in (q.get("statement") or ""):
                return q
        self.fail("nenhuma questão investigável no lote")

    def _uma_errada(self, pid: str, questao: dict) -> str:
        """Descobre uma errada SEM ler gabarito: tenta e usa a resposta.

        O gabarito não viaja para o aluno — e é assim que tem de ser. Então
        o teste marca uma alternativa e pergunta ao backend se houve
        intervenção; a que produz intervenção é, por definição, a errada.
        """
        for chave in self._opcoes(questao):
            r = self._responder(pid, questao["question_version_id"], chave)
            if r.get("intervention"):
                return chave
        return ""


class ERRARPELAAPINAOAVANCA(_Base):
    """O defeito do §3, medido na resposta HTTP."""

    def test_a_resposta_do_PUT_traz_os_campos_novos(self):
        pid = self._pratica()
        estado = self._abrir(pid)
        q = estado["questions"][0]
        r = self._responder(pid, q["question_version_id"],
                            self._opcoes(q)[0])
        self.assertIn("may_advance", r)
        self.assertIn("intervention", r)

    def test_alguma_alternativa_produz_intervencao(self):
        """Se NENHUMA produz, a decisão não está atravessando a API."""
        pid = self._pratica()
        estado = self._abrir(pid)
        q = self._investigavel(estado)
        self.assertTrue(self._uma_errada(pid, q),
                        "nenhuma alternativa produziu intervenção — a decisão "
                        "não chegou ao cliente")

    def test_e_quando_produz_o_avanco_e_negado(self):
        pid = self._pratica()
        estado = self._abrir(pid)
        q = self._investigavel(estado)
        errada = self._uma_errada(pid, q)
        r = self._responder(pid, q["question_version_id"], errada)
        self.assertFalse(r["may_advance"])
        self.assertTrue(r["intervention"]["action"])
        self.assertTrue(r["intervention"]["skill"])

    def test_a_intervencao_nao_entrega_o_gabarito(self):
        """Ela diz o que trabalhar, nunca qual era a resposta."""
        import json

        pid = self._pratica()
        estado = self._abrir(pid)
        q = self._investigavel(estado)
        errada = self._uma_errada(pid, q)
        r = self._responder(pid, q["question_version_id"], errada)
        bruto = json.dumps(r["intervention"], ensure_ascii=False)
        self.assertNotIn("correct_option", bruto)
        self.assertNotIn("is_valid_option", bruto)


class AINTERVENCAOSOBREVIVEAORECARREGAR(_Base):
    """§13 — e o defeito do `response_model` que a descartava."""

    def test_o_estado_da_tentativa_declara_pending_intervention(self):
        pid = self._pratica()
        estado = self._abrir(pid)
        self.assertIn("pending_intervention", estado,
                      "o response_model descartou o campo")

    def test_depois_de_errar_ele_vem_preenchido(self):
        pid = self._pratica()
        estado = self._abrir(pid)
        q = self._investigavel(estado)
        self._uma_errada(pid, q)
        # GET novo, como a página recarregada faz.
        depois = self._estado(pid)
        self.assertIsNotNone(depois["pending_intervention"],
                             "a intervenção sumiu no recarregar")
        self.assertEqual(q["question_version_id"],
                         depois["pending_intervention"]["question_version_id"])

    def test_e_a_posicao_corrente_e_a_da_questao_errada(self):
        pid = self._pratica()
        estado = self._abrir(pid)
        q = self._investigavel(estado)
        self._uma_errada(pid, q)
        self.assertEqual(q["position"], self._estado(pid)["current_position"])


class OSOUTROSMODOSATRAVESSAMILESOS(_Base):
    """A proteção do §4, medida por HTTP."""

    def test_a_verificacao_L0_nunca_e_interrompida(self):
        pid = self._pratica(purpose="VERIFY")
        estado = self._abrir(pid)
        q = self._investigavel(estado)
        for chave in self._opcoes(q):
            r = self._responder(pid, q["question_version_id"], chave)
            with self.subTest(chave):
                self.assertTrue(r["may_advance"])
                self.assertIsNone(r["intervention"])

    def test_e_nem_mesmo_no_estado_da_tentativa(self):
        pid = self._pratica(purpose="VERIFY")
        estado = self._abrir(pid)
        q = self._investigavel(estado)
        self._responder(pid, q["question_version_id"], self._opcoes(q)[0])
        self.assertIsNone(self._estado(pid)["pending_intervention"])


if __name__ == "__main__":
    unittest.main()
