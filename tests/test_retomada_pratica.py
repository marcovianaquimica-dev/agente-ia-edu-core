"""Retomar a prática aberta, em vez de criar outra por baixo do aluno.

O BUG, MEDIDO NO NAVEGADOR E CONFERIDO NO BANCO
================================================
O aluno respondeu a primeira questão de uma prática, saiu e voltou. A Home
dizia "Continuar prática" — e o clique abriu uma prática NOVA, da questão 1,
em branco. No banco ficaram duas práticas IN_PROGRESS do mesmo conteúdo, a
primeira com a resposta dele dentro, inalcançável.

A resposta não se perdeu: ela foi gravada. O que se perdeu foi o caminho de
volta até ela.

POR QUE O BOTÃO ESTAVA CERTO E A AÇÃO ERRADA
=============================================
`_estado_do_passo` JÁ encontrava a prática aberta para dizer EM_ANDAMENTO —
e jogava o identificador fora. O backend sabia qual retomar e não contava.
O frontend, sem saber, chamava `POST /student/practice`, que cria.

Então o passo passa a dizer QUAL: `next_step.resume_assignment_id`. Quem
decide continua sendo o backend; a tela só obedece.

A REGRA QUE ESTE TESTE FIXA
============================
Há `resume_assignment_id` exatamente quando o estado é EM_ANDAMENTO. Num
passo não iniciado ele é nulo — oferecer "retomar" o que não existe seria o
mesmo erro na direção oposta.
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
from agente_ia_edu.services.proximo_passo import (
    ESTADO_EM_ANDAMENTO,
    ESTADO_NAO_INICIADO,
)

from test_invariantes_piloto_zero import BALANC, ESTEQ, _ctx, _seed


class RetomadaDaPraticaTests(unittest.TestCase):

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

    def _passo(self) -> dict:
        r = self.client.get(
            f"/api/v1/student/activities/{self.atividade}/readiness")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["next_step"]

    def _abrir_pratica(self, conteudo: str) -> str:
        """Cria a prática e a ABRE — é a abertura que a torna 'em andamento'."""
        r = self.client.post("/api/v1/student/practice",
                             json={"content_code": conteudo, "question_count": 3})
        self.assertEqual(r.status_code, 200, r.text)
        pid = r.json()["assignment_id"]
        r = self.client.post(f"/api/v1/student/activities/{pid}/attempt")
        self.assertEqual(r.status_code, 200, r.text)
        return pid

    # ----------------------------------------------------------------------

    def test_sem_nada_aberto_nao_ha_o_que_retomar(self):
        passo = self._passo()
        self.assertEqual(passo["state"], ESTADO_NAO_INICIADO)
        self.assertIsNone(passo.get("resume_assignment_id"),
                          "ofereceu retomar algo que o aluno nunca abriu")

    def test_com_pratica_aberta_o_passo_diz_qual_retomar(self):
        pid = self._abrir_pratica(self._passo()["content_code"])
        passo = self._passo()
        self.assertEqual(passo["state"], ESTADO_EM_ANDAMENTO)
        self.assertEqual(passo.get("resume_assignment_id"), pid,
                         "o passo esta EM_ANDAMENTO mas nao diz o que retomar")

    def test_o_id_oferecido_abre_de_verdade(self):
        """Um identificador que nao abre seria pior que nenhum."""
        self._abrir_pratica(self._passo()["content_code"])
        pid = self._passo()["resume_assignment_id"]
        r = self.client.post(f"/api/v1/student/activities/{pid}/attempt")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "IN_PROGRESS")

    def test_retomar_nao_cria_uma_segunda_pratica(self):
        """A asserção central: o aluno volta para a MESMA prática."""
        primeira = self._abrir_pratica(self._passo()["content_code"])
        self.assertEqual(self._passo()["resume_assignment_id"], primeira)
        # Abrir de novo pelo caminho oferecido e idempotente.
        self.client.post(f"/api/v1/student/activities/{primeira}/attempt")
        abertas = [p for p in self.client.get(
            "/api/v1/student/practice").json()["items"]
            if "IN_PROGRESS" in (p.get("state") or "")]
        self.assertEqual(len(abertas), 1,
                         f"{len(abertas)} praticas abertas ao mesmo tempo")

    def test_pratica_de_outro_conteudo_nao_e_oferecida_para_retomar(self):
        """Retomar tem de ser do assunto do passo.

        Do zero, o passo aponta para o pré-requisito (BALANCEAMENTO). Uma
        prática aberta de ESTEQUIOMETRIA não pode virar "continuar" dele:
        devolveria o aluno ao assunto errado.
        """
        alvo = self._passo()["content_code"]
        self.assertEqual(alvo, BALANC,
                         "o cenario mudou: o passo inicial nao e mais a base")
        self._abrir_pratica(ESTEQ)
        passo = self._passo()
        self.assertEqual(passo["content_code"], BALANC)
        self.assertIsNone(passo.get("resume_assignment_id"),
                          "ofereceu retomar a pratica de outro conteudo")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
