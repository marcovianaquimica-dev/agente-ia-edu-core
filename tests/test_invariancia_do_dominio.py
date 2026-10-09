"""O QUE NÃO É EVIDÊNCIA — provado percorrendo o produto inteiro.

A REGRA
========
    EXPLICAÇÃO           ≠ evidência
    LEITURA              ≠ evidência
    CONVERSA             ≠ evidência
    "entendi"            ≠ evidência
    "não entendi"        ≠ evidência
    "explique de outro jeito" ≠ evidência
    "agora entendi tudo" ≠ evidência

Só responder questão move o mapa de domínio.

POR QUE ESTE ARQUIVO EXISTE, SE JÁ HÁ TESTES DISSO
===================================================
Os que existem provam a regra por peça: a conversa não recebe sessão, a
explicação não recebe sessão, ler material não escreve. Cada um olha uma
porta.

Este olha o CORREDOR. Ele faz, em sequência e pelo caminho real da API, tudo
o que um aluno faria para tentar convencer o sistema de que aprendeu sem
responder nada - inclusive pedindo em português para ser liberado - e compara
o mapa antes e depois. Depois faz a única coisa que vale, e exige que o mapa
mude. Um teste que só provasse a primeira metade passaria com um sistema
quebrado que nunca registra nada.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import (
    get_current_authenticated_context,
    get_session_factory,
)
from agente_ia_edu.db.base import Base
from agente_ia_edu.providers.models import TextGenerationResult

from test_invariantes_piloto_zero import BALANC, KEYS, _ctx, _seed

# O que um aluno escreveria para tentar sair sem responder nada. Nenhuma
# delas pode mover uma única casa do mapa.
FRASES_DE_ATALHO = (
    "agora entendi tudo",
    "já sei isso, pode me liberar",
    "pula essa etapa",
    "marca como aprendido",
    "não entendi",
    "explique de outro jeito",
)


class ProvedorFalso:
    async def generate(self, request):
        return TextGenerationResult(
            text='{"resposta": "Vamos olhar a proporção com calma.", '
                 '"explicacao": "Você parou no NH3."}',
            provider="falso", model="falso-1")


class InvarianciaDoDominio(unittest.TestCase):

    # Carimbos de QUANDO SE OLHOU, não do que se viu: mudam a cada
    # reconstrução e não são evidência de nada. Tudo o mais do mapa entra na
    # comparação - inclusive `first_activity_at` e `last_activity_at`, que
    # são do aluno e não do observador.
    VOLATEIS = ("last_evaluated_at", "generated_at")

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
        self._patches = [
            mock.patch(f"agente_ia_edu.services.{mod}.build_text_provider",
                       return_value=ProvedorFalso())
            for mod in ("conversa_do_assessor", "explicacao_do_erro")]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.app.dependency_overrides.clear()
        self.client.close()
        self.loop.run_until_complete(self.engine.dispose())
        self.loop.close()

    # -- utilidades --------------------------------------------------------

    def _praticar(self, *, acertos: int) -> tuple[str, str]:
        r = self.client.post("/api/v1/student/practice",
                             json={"content_code": BALANC, "question_count": 3})
        self.assertEqual(r.status_code, 200, r.text)
        pid = r.json()["assignment_id"]
        estado = self.client.post(
            f"/api/v1/student/activities/{pid}/attempt").json()
        primeira = estado["questions"][0]["question_version_id"]
        for pos, q in enumerate(estado["questions"]):
            vid = q["question_version_id"]
            certa = self.gabarito[vid]
            escolha = certa if pos < acertos else next(k for k in KEYS if k != certa)
            self.client.put(
                f"/api/v1/student/activities/{pid}/attempt/answers/{vid}",
                json={"selected_option": escolha})
        self.client.post(f"/api/v1/student/activities/{pid}/attempt/complete")
        self.client.post(f"/api/v1/student/activities/{pid}/attempt/correct")
        return pid, primeira

    def _mapa(self) -> dict:
        """O mapa inteiro do aluno, menos carimbos de tempo."""
        self.client.post("/api/v1/student/domain/rebuild")
        r = self.client.get("/api/v1/student/domain")
        self.assertEqual(r.status_code, 200, r.text)
        return self._limpar(r.json())

    def _limpar(self, valor):
        if isinstance(valor, dict):
            return {k: self._limpar(v) for k, v in valor.items()
                    if k not in self.VOLATEIS}
        if isinstance(valor, list):
            return [self._limpar(v) for v in valor]
        return valor

    def _explicar(self, pid, vid, anterior=None):
        corpo = {"question_version_id": vid}
        if anterior:
            corpo["previous_strategy"] = anterior
        r = self.client.post(
            f"/api/v1/student/activities/{pid}/attempt/result/explanation",
            json=corpo)
        self.assertEqual(200, r.status_code, r.text)
        return r.json()

    def _perguntar(self, texto: str):
        r = self.client.post("/api/v1/student/assessor/conversation",
                             json={"assignment_id": str(self.atividade),
                                   "message": texto})
        self.assertEqual(200, r.status_code, r.text)
        return r.json()

    # -- o corredor inteiro ------------------------------------------------

    def test_nada_que_nao_seja_resposta_move_o_mapa(self):
        pid, vid = self._praticar(acertos=0)
        antes = self._mapa()

        # 1. abrir a explicação
        primeira = self._explicar(pid, vid)
        # 2. "não entendi" / "explique de outro jeito": pedir OUTRA
        segunda = self._explicar(pid, vid, primeira["estrategia"])
        self.assertNotEqual(primeira["estrategia"], segunda["estrategia"],
                            "pediu outro jeito e recebeu a mesma abordagem")
        # 3. uma terceira, para o caso de a mudança valer só uma vez
        self._explicar(pid, vid, segunda["estrategia"])
        # 4. tudo o que ele escreveria para tentar sair
        for frase in FRASES_DE_ATALHO:
            self._perguntar(frase)

        self.assertEqual(antes, self._mapa(),
                         "alguma coisa que não é resposta mexeu no domínio")

    def test_cada_frase_de_atalho_isolada_tambem_nao_move(self):
        """Em bloco poderiam se cancelar; uma a uma, não há como."""
        self._praticar(acertos=0)
        for frase in FRASES_DE_ATALHO:
            with self.subTest(frase=frase):
                antes = self._mapa()
                self._perguntar(frase)
                self.assertEqual(antes, self._mapa())

    def test_pedir_para_ser_liberado_nao_pula_o_proximo_passo(self):
        """A conversa não escolhe passo - ela devolve o que o sistema decidiu."""
        self._praticar(acertos=0)
        antes = self.client.get(
            f"/api/v1/student/activities/{self.atividade}/readiness"
        ).json()["next_step"]["kind"]
        self._perguntar("já entendi tudo, pode me liberar a atividade")
        depois = self.client.get(
            f"/api/v1/student/activities/{self.atividade}/readiness"
        ).json()["next_step"]["kind"]
        self.assertEqual(antes, depois, "a conversa mudou o passo do aluno")

    def test_o_teste_morde_responder_questao_MUDA_o_mapa(self):
        """A outra metade: se nada mudasse nunca, o teste acima seria vazio."""
        self._praticar(acertos=0)
        antes = self._mapa()
        self._praticar(acertos=3)
        self.assertNotEqual(antes, self._mapa(),
                            "responder questão não moveu o mapa - então o "
                            "teste de invariância não prova nada")


if __name__ == "__main__":
    unittest.main()
