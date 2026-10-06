"""A EXPLICAÇÃO DO ERRO PELA API - e o que ela nunca move.

O caminho do aluno: ele praticou, errou, e pede para entender. A rota existe
porque a explicação é uma intervenção pedagógica, não um campo a mais no
resultado: ela pode custar uma chamada de IA, e o aluno pode pedir OUTRA.

O QUE ESTE ARQUIVO TRAVA
=========================
1. a frase de dívida técnica não chega mais ao aluno;
2. pedir explicação não cria evidência nem mexe no mapa de domínio;
3. pedir de novo muda a abordagem;
4. errar o dono da tentativa não devolve explicação nenhuma;
5. a IA fora do ar não prende o aluno numa tela vazia.
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
from agente_ia_edu.services.explicacao_do_erro import (
    ESTRATEGIA_INICIAL,
    FONTE_FALLBACK,
    FONTE_IA,
)

from test_invariantes_piloto_zero import BALANC, BRUNO, KEYS, _ctx, _seed

FRASES_INTERNAS = ("fase futura", "não é usada aqui",
                   "não há resolução oficial")


class ProvedorFalso:
    def __init__(self):
        self.chamadas = []

    async def generate(self, request):
        self.chamadas.append(request.prompt)
        return TextGenerationResult(
            text='{"explicacao": "Você parou na quantidade de NH3."}',
            provider="falso", model="falso-1")


class ProvedorQueFalha:
    async def generate(self, request):
        raise TimeoutError("fora do ar")


class ExplicacaoDoErroHTTP(unittest.TestCase):

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
        self.app = create_app()
        self.app.dependency_overrides[get_session_factory] = lambda: self.factory
        self.app.dependency_overrides[get_current_authenticated_context] = _ctx
        self.client = TestClient(self.app)
        self.provedor = ProvedorFalso()
        # O provedor entra pela MESMA porta da conversa: a fábrica do módulo.
        # Nada de variável global de rota só para o teste alcançar.
        self._patch = mock.patch(
            "agente_ia_edu.services.explicacao_do_erro.build_text_provider",
            return_value=self.provedor)
        self._patch.start()
        self.pid, self.errada = self._errar()

    def _trocar_provedor(self, provedor):
        self._patch.stop()
        self._patch = mock.patch(
            "agente_ia_edu.services.explicacao_do_erro.build_text_provider",
            return_value=provedor)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self.app.dependency_overrides.clear()
        self.client.close()
        self.loop.run_until_complete(self.engine.dispose())
        self.loop.close()

    # -- utilidades --------------------------------------------------------

    def _errar(self) -> tuple[str, str]:
        """Pratica em que o aluno erra tudo. Devolve (assignment, 1ª questão)."""
        r = self.client.post("/api/v1/student/practice",
                             json={"content_code": BALANC, "question_count": 3})
        self.assertEqual(r.status_code, 200, r.text)
        pid = r.json()["assignment_id"]
        estado = self.client.post(
            f"/api/v1/student/activities/{pid}/attempt").json()
        primeira = estado["questions"][0]["question_version_id"]
        for q in estado["questions"]:
            vid = q["question_version_id"]
            certa = self.gabarito[vid]
            self.client.put(
                f"/api/v1/student/activities/{pid}/attempt/answers/{vid}",
                json={"selected_option": next(k for k in KEYS if k != certa)})
        self.client.post(f"/api/v1/student/activities/{pid}/attempt/complete")
        self.client.post(f"/api/v1/student/activities/{pid}/attempt/correct")
        return pid, primeira

    def _explicar(self, **corpo):
        base = {"question_version_id": self.errada}
        base.update(corpo)
        return self.client.post(
            f"/api/v1/student/activities/{self.pid}/attempt/result/explanation",
            json=base)

    # O QUE É EVIDÊNCIA, nomeado. `last_evaluated_at` muda a cada
    # reconstrução por ser carimbo de quando se olhou, não do que se viu -
    # comparar o dicionário inteiro faria o teste falhar por isso e esconder
    # a pergunta real atrás de ruído.
    EVIDENCIA = ("mastery_percent", "mastery_band", "questions_seen",
                 "questions_answered", "questions_correct",
                 "questions_incorrect", "accuracy", "evidence_count",
                 "evidence_state", "definitive_evidence_count",
                 "provisional_evidence_count", "origin_breakdown",
                 "first_activity_at", "last_activity_at", "subcontents")

    def _dominio(self) -> dict:
        self.client.post("/api/v1/student/domain/rebuild")
        r = self.client.get(f"/api/v1/student/domain/content/{BALANC}")
        conteudo = (r.json() or {}).get("content") or {}
        return {k: conteudo.get(k) for k in self.EVIDENCIA}

    # -- a explicação chega -------------------------------------------------

    def test_o_aluno_recebe_uma_explicacao(self):
        r = self._explicar()
        self.assertEqual(200, r.status_code, r.text)
        corpo = r.json()
        self.assertEqual(FONTE_IA, corpo["fonte"])
        self.assertEqual("Você parou na quantidade de NH3.", corpo["texto"])
        self.assertEqual(ESTRATEGIA_INICIAL, corpo["estrategia"])

    def test_a_questao_e_o_erro_chegam_ao_prompt(self):
        self._explicar()
        prompt = self.provedor.chamadas[0]
        self.assertIn("Enunciado da questão", prompt)
        self.assertIn("O que o aluno marcou", prompt)

    def test_a_frase_de_divida_tecnica_sumiu_do_resultado(self):
        r = self.client.get(
            f"/api/v1/student/activities/{self.pid}/attempt/result")
        self.assertEqual(200, r.status_code, r.text)
        bruto = r.text.lower()
        for frase in FRASES_INTERNAS:
            self.assertNotIn(frase, bruto,
                             "o aluno continua lendo a dívida técnica")

    # -- pedir de novo muda a abordagem ------------------------------------

    def test_pedir_outro_jeito_muda_a_estrategia(self):
        primeira = self._explicar().json()["estrategia"]
        segunda = self._explicar(previous_strategy=primeira).json()["estrategia"]
        self.assertNotEqual(primeira, segunda,
                            "pediu outro jeito e recebeu o mesmo")

    def test_a_abordagem_nova_chega_ao_prompt(self):
        primeira = self._explicar().json()["estrategia"]
        segunda = self._explicar(previous_strategy=primeira).json()["estrategia"]
        self.assertIn(segunda, self.provedor.chamadas[-1])

    # -- nada disso é evidência --------------------------------------------

    def test_pedir_explicacao_nao_mexe_no_mapa_de_dominio(self):
        antes = self._dominio()
        for _ in range(3):
            self._explicar()
        self.assertEqual(antes, self._dominio(),
                         "ler explicação virou evidência de domínio")

    # -- limites ------------------------------------------------------------

    def test_questao_que_nao_e_da_tentativa_e_recusada(self):
        import uuid

        r = self._explicar(question_version_id=str(uuid.uuid4()))
        self.assertIn(r.status_code, (404, 422), r.text)

    def test_tentativa_de_outro_aluno_nao_devolve_explicacao(self):
        self.app.dependency_overrides[get_current_authenticated_context] = (
            lambda: _ctx(BRUNO))
        r = self._explicar()
        self.assertIn(r.status_code, (403, 404), r.text)

    def test_ia_fora_do_ar_devolve_fallback_e_nao_erro(self):
        self._trocar_provedor(ProvedorQueFalha())
        r = self._explicar()
        self.assertEqual(200, r.status_code, r.text)
        corpo = r.json()
        self.assertEqual(FONTE_FALLBACK, corpo["fonte"])
        self.assertTrue(corpo["texto"].strip())
        self.assertIsNone(corpo["provider"])
        for proibido in ("Traceback", "TimeoutError", "{"):
            self.assertNotIn(proibido, corpo["texto"])


if __name__ == "__main__":
    unittest.main()
