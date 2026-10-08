"""A VERIFICAÇÃO QUE FALHA, PELO CAMINHO REAL DA API.

Não é o mesmo que `test_assessor_verificacao_falha.py`: lá a regra é provada
na função de decisão, com os parâmetros entregues na mão. Aqui o que se prova
é que o sistema SABE que a última tentativa era uma verificação - porque, até
2026-10-06, ele não sabia.

A verificação é criada pelo mesmo `POST /student/practice` da prática comum,
com `question_count` menor. Depois de corrigida, as duas ficam indistinguíveis:
mesma origem, mesmos metadados. O assessor então tratava uma verificação
falhada como "mais uma prática fraca" e oferecia outro lote de questões.

A correção não inventa tabela: `purpose` entra nos metadados que o assignment
já carrega, e volta por `list_practices`. Quem não informa continua sendo
prática - o padrão é o comportamento antigo.
"""

from __future__ import annotations

import asyncio
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
from agente_ia_edu.services.adaptive_practice import (
    PROPOSITO_PRATICA,
    PROPOSITO_VERIFICACAO,
)
from agente_ia_edu.services.proximo_passo import PASSO_PRATICA

from test_intervencao_no_readiness import _publicar_material
from test_invariantes_piloto_zero import BALANC, KEYS, _ctx, _seed


class VerificacaoFalhaPelaAPI(unittest.TestCase):

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                          poolclass=StaticPool)
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession,
                                          expire_on_commit=False)

        async def prep():
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            fx = await _seed(self.factory)
            fx["material_id"] = await _publicar_material(self.factory)
            return fx

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

    # -- utilidades --------------------------------------------------------

    def _responder(self, *, quantas: int, acertos: int,
                   purpose: str | None = None) -> str:
        corpo = {"content_code": BALANC, "question_count": quantas}
        if purpose is not None:
            corpo["purpose"] = purpose
        r = self.client.post("/api/v1/student/practice", json=corpo)
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
        self.client.post("/api/v1/student/domain/rebuild")
        return pid

    def _passo(self) -> dict:
        r = self.client.get(
            f"/api/v1/student/activities/{self.atividade}/readiness")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["next_step"]

    # -- o propósito chega, e volta ---------------------------------------

    def test_o_proposito_e_gravado_e_devolvido(self):
        self._responder(quantas=3, acertos=0, purpose=PROPOSITO_VERIFICACAO)
        itens = self.client.get("/api/v1/student/practice").json()["items"]
        self.assertTrue(itens, "nenhuma prática listada")
        self.assertEqual(PROPOSITO_VERIFICACAO, itens[-1]["purpose"])

    def test_quem_nao_informa_continua_sendo_pratica(self):
        self._responder(quantas=3, acertos=0)
        itens = self.client.get("/api/v1/student/practice").json()["items"]
        self.assertEqual(PROPOSITO_PRATICA, itens[-1]["purpose"])

    def test_proposito_desconhecido_e_recusado(self):
        """Lista fechada: um valor novo é decisão consciente, não um campo
        livre que o navegador preenche."""
        r = self.client.post("/api/v1/student/practice",
                             json={"content_code": BALANC, "question_count": 3,
                                   "purpose": "QUALQUER_COISA"})
        self.assertEqual(422, r.status_code, r.text)

    # -- a regra pedagógica ------------------------------------------------

    def _ate_a_verificacao(self) -> None:
        """Duas tentativas fracas e uma forte: a trajetória que pede VERIFY.

        Com uma fraca só, a média já sai da faixa de lacuna e o passo volta a
        ser diagnóstico - medido ao montar este cenário.
        """
        self._responder(quantas=5, acertos=1)
        self._responder(quantas=5, acertos=1)
        self._responder(quantas=5, acertos=5)
        self.assertEqual("VERIFY", self._passo()["kind"],
                         "o cenário não chegou à verificação")

    def test_depois_de_verificacao_falha_nao_vem_praticar_sozinho(self):
        """O achado medido: 0/3 na verificação e a tela oferecia mais lote.

        Pelo caminho real, quando a verificação falha o teto de ciclos já foi
        alcançado - chegar a VERIFY exige três tentativas - e o passo vira
        ESCALATE. O guarda em `_acao` cobre o caso abaixo do teto, e está
        provado isoladamente em `test_assessor_verificacao_falha.py`. O que
        este teste trava é o CONTRATO visível: depois de uma verificação
        falhada, o próximo passo nunca é mais um lote sozinho.
        """
        self._ate_a_verificacao()
        self._responder(quantas=3, acertos=0, purpose=PROPOSITO_VERIFICACAO)
        depois = self._passo()
        self.assertNotEqual(PASSO_PRATICA, depois["kind"],
                            "verificação falhou e o sistema mandou praticar "
                            "sozinho de novo")

    def test_a_tela_nao_diz_tentar_de_novo_sozinho(self):
        """A frase é a decisão. Se o backend não manda praticar, ela muda."""
        self._ate_a_verificacao()
        self._responder(quantas=3, acertos=0, purpose=PROPOSITO_VERIFICACAO)
        fala = (self._passo().get("feedback") or {}).get("titulo") or ""
        self.assertTrue(fala, "o passo depois da verificação ficou sem fala")
        self.assertNotIn("sozinho", fala.lower(),
                         "a tela continua mandando tentar sozinho")


if __name__ == "__main__":
    unittest.main()
