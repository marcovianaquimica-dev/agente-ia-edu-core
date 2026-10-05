"""A lista "Minhas atividades" diz em que pé está cada atividade.

POR QUE ISTO É BACKEND
=======================
A primeira versão da tela montava o rótulo do botão no JavaScript, com um
mapa próprio:

    COMPLETED   -> "Ver resultado"
    IN_PROGRESS -> "Continuar atividade"
    resto       -> "Abrir"

É a MESMA decisão que `proximo_passo.cta_para` já toma — e já tinha
divergido: a matriz diz "Começar atividade" onde o JavaScript dizia "Abrir".
Duas tabelas para a mesma pergunta divergem; a segunda é a errada.

Então a lista passa a devolver `state` e `cta`, e o teste exige que o `cta`
seja exatamente o da matriz: se alguém escrever um rótulo à mão aqui, cai.

E CADA ALUNO VÊ O SEU
======================
O estado vem da tentativa DAQUELE aluno. O outro aluno abrindo a atividade
dele não pode mexer no que este vê.
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
    ESTADO_CONCLUIDO,
    ESTADO_EM_ANDAMENTO,
    ESTADO_NAO_INICIADO,
    PASSO_ATIVIDADE,
    cta_para,
)

from test_atividade_oficial_e2e import ALUNO, KEYS, OUTRO, _ctx, _seed


class ListaDeAtividadesTests(unittest.TestCase):
    """Reaproveita o CENÁRIO do ciclo oficial - mesma escola, mesma turma,
    mesma atividade de 5 questões, e uma segunda atividade de outro aluno -
    sem herdar a classe: herdar reexecutaria os 31 testes de lá a cada
    rodada, e um teste que roda duas vezes não prova nada duas vezes."""

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

    def _abrir(self) -> dict:
        r = self.client.post(f"/api/v1/student/activities/{self.atividade}/attempt")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _responder_todas(self, *, acertos: int) -> None:
        estado = self._abrir()
        for pos, q in enumerate(estado["questions"]):
            vid = q["question_version_id"]
            certa = self.gabarito[vid]
            escolha = certa if pos < acertos else next(k for k in KEYS if k != certa)
            r = self.client.put(
                f"/api/v1/student/activities/{self.atividade}/attempt/answers/{vid}",
                json={"selected_option": escolha})
            self.assertEqual(r.status_code, 200, r.text)

    def _finalizar_e_corrigir(self) -> None:
        r = self.client.post(
            f"/api/v1/student/activities/{self.atividade}/attempt/complete")
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.post(
            f"/api/v1/student/activities/{self.atividade}/attempt/correct")
        self.assertIn(r.status_code, (200, 201), r.text)

    def _lista(self) -> list[dict]:
        r = self.client.get("/api/v1/student/activities")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["items"]

    def _minha(self) -> dict:
        return next(a for a in self._lista()
                    if a["assignment_id"] == self.atividade)

    # -- o estado ----------------------------------------------------------

    def test_atividade_nunca_aberta_aparece_como_nao_iniciada(self):
        self.assertEqual(self._minha()["state"], ESTADO_NAO_INICIADO)

    def test_atividade_aberta_aparece_em_andamento(self):
        self._abrir()
        self.assertEqual(self._minha()["state"], ESTADO_EM_ANDAMENTO)

    def test_atividade_entregue_aparece_concluida(self):
        self._responder_todas(acertos=5)
        self._finalizar_e_corrigir()
        self.assertEqual(self._minha()["state"], ESTADO_CONCLUIDO)

    # -- o rótulo ----------------------------------------------------------

    def test_o_cta_e_o_da_matriz_em_todos_os_estados(self):
        """A asserção central: nenhum rótulo escrito à mão nesta rota."""
        self.assertEqual(self._minha()["cta"],
                         cta_para(PASSO_ATIVIDADE, ESTADO_NAO_INICIADO))
        self._abrir()
        self.assertEqual(self._minha()["cta"],
                         cta_para(PASSO_ATIVIDADE, ESTADO_EM_ANDAMENTO))
        self._responder_todas(acertos=5)
        self._finalizar_e_corrigir()
        self.assertEqual(self._minha()["cta"],
                         cta_para(PASSO_ATIVIDADE, ESTADO_CONCLUIDO))

    def test_toda_atividade_da_lista_traz_estado_e_cta(self):
        for a in self._lista():
            with self.subTest(atividade=a["title"]):
                self.assertIn(a["state"], (ESTADO_NAO_INICIADO,
                                           ESTADO_EM_ANDAMENTO,
                                           ESTADO_CONCLUIDO))
                self.assertTrue((a["cta"] or "").strip())

    # -- o estado da atividade na prontidao --------------------------------

    def test_a_prontidao_diz_o_estado_da_atividade_mesmo_quando_o_passo_volta(self):
        """O selo "Entregue" não pode depender do próximo passo.

        Medido no navegador: com 2 de 5, o planejador volta a mandar
        diagnosticar a base, `next_step.kind` deixa de ser ACTIVITY e a Home
        — que lia o selo do passo — parava de dizer que a atividade tinha
        sido entregue, enquanto "Minhas atividades" dizia "Concluída". As
        duas telas liam a mesma entrega e discordavam.
        """
        self._responder_todas(acertos=1)
        self._finalizar_e_corrigir()
        r = self.client.get(
            f"/api/v1/student/activities/{self.atividade}/readiness")
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertEqual(d.get("activity_state"), ESTADO_CONCLUIDO,
                         "a prontidao nao diz que a atividade foi entregue")
        self.assertEqual(self._minha()["state"], ESTADO_CONCLUIDO,
                         "a lista e a prontidao discordam sobre a mesma entrega")

    def test_antes_de_comecar_a_prontidao_diz_nao_iniciada(self):
        r = self.client.get(
            f"/api/v1/student/activities/{self.atividade}/readiness")
        self.assertEqual(r.json().get("activity_state"), ESTADO_NAO_INICIADO)

    # -- isolamento --------------------------------------------------------

    def test_a_tentativa_do_outro_aluno_nao_muda_o_meu_estado(self):
        outra = self.fx["atividade_do_outro"]
        self.app.dependency_overrides[get_current_authenticated_context] = \
            lambda: _ctx(user=OUTRO)
        try:
            r = self.client.post(f"/api/v1/student/activities/{outra}/attempt")
            self.assertEqual(r.status_code, 200, r.text)
        finally:
            self.app.dependency_overrides[get_current_authenticated_context] = \
                lambda: _ctx(user=ALUNO)
        self.assertEqual(self._minha()["state"], ESTADO_NAO_INICIADO)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
