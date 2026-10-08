"""O pré-requisito chega com a evidência que já existe sobre ele.

O BUG, MEDIDO NO NAVEGADOR
==========================
O aluno respondeu 3 perguntas sobre a base (2 certas, 0,667) e entregou a
atividade. O mapa de domínio sabia disso:

    CHEMISTRY-GENERAL-BALANCING   respondidas 3   acerto 0,667

E o `/readiness` devolvia o mesmo conteúdo, como pré-requisito, assim:

    {"code": "CHEMISTRY-GENERAL-BALANCING", "answered": None,
     "accuracy": None, "content_state": None, "mastered": False}

Sem evidência nenhuma. E `proximo_passo`, que usa `answered` e `accuracy`
para distinguir "ainda não sei" de "já medi, e falta", lia ausência e mandava
DIAGNOSTICAR — o aluno rediagnosticando o que acabara de responder.

A CAUSA, CONFERIDA E NÃO SUPOSTA
=================================
Rodando `build_path` contra o banco de desenvolvimento, o planejador DEVOLVE
a base com `questions_answered=3` e `accuracy=0.6667`. O dado existia.

Quem o descartava era este trecho do `/readiness`:

    "prerequisites": c.get("prerequisites") or await _prereqs_do_catalogo(...)

A lista do planejador traz só `{code, name}`. `_prereqs_do_catalogo`, o
caminho ALTERNATIVO, é que cruzava com a evidência — então o pré-requisito
só chegava completo quando o planejador NÃO o conhecia. Exatamente ao
contrário do necessário.

O enriquecimento passou a ser uma etapa própria, aplicada aos dois caminhos.

SOBRE OS TESTES DE INTEGRAÇÃO DAQUI
====================================
Eles passaram na primeira execução: neste cenário o planejador não devolve
`prerequisites`, então a travessia cai no caminho alternativo, que já estava
certo. Ficam como regressão desse caminho — não foram eles que pegaram o
bug. Quem pega o bug é `EnriquecimentoTests`, sobre a função nova.
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

from agente_ia_edu.services.readiness_route import com_evidencia

from test_invariantes_piloto_zero import BALANC, KEYS, _ctx, _seed


class EnriquecimentoTests(unittest.TestCase):
    """A função que faltava: completar um pré-requisito com o que já se sabe
    sobre ele, venha a lista de onde vier."""

    OBSERVADO = {BALANC: {"content_code": BALANC, "content_state": "READY",
                          "questions_answered": 3, "accuracy": 0.6667}}

    def test_a_lista_crua_do_planejador_ganha_a_evidencia(self):
        """`{code, name}` e so isso - era com esta lista que o passo decidia."""
        saida = com_evidencia([{"code": BALANC, "name": "Balanceamento"}],
                              self.OBSERVADO)
        self.assertEqual(saida[0]["answered"], 3)
        self.assertAlmostEqual(saida[0]["accuracy"], 0.6667, places=3)
        self.assertEqual(saida[0]["content_state"], "READY")

    def test_o_nome_nao_se_perde(self):
        saida = com_evidencia([{"code": BALANC, "name": "Balanceamento"}],
                              self.OBSERVADO)
        self.assertEqual(saida[0]["name"], "Balanceamento")

    def test_sem_nada_observado_o_prerequisito_fica_zerado_e_nao_dominado(self):
        saida = com_evidencia([{"code": BALANC, "name": "Balanceamento"}], {})
        self.assertEqual(saida[0]["answered"], 0)
        self.assertIsNone(saida[0]["accuracy"])
        self.assertFalse(saida[0]["mastered"])

    def test_so_MASTERED_conta_como_dominado(self):
        """READY e "da para seguir", nao "ja sabe" - a diferenca que fez o
        aluno com 0 de 5 ser liberado uma vez."""
        self.assertFalse(com_evidencia([{"code": BALANC}], self.OBSERVADO)[0]["mastered"])
        dominado = {BALANC: dict(self.OBSERVADO[BALANC], content_state="MASTERED")}
        self.assertTrue(com_evidencia([{"code": BALANC}], dominado)[0]["mastered"])

    def test_prerequisito_sem_codigo_e_descartado(self):
        """Uma entrada sem `code` nao da para cruzar com nada, e passaria
        adiante como pre-requisito fantasma."""
        self.assertEqual(com_evidencia([{"name": "sem codigo"}], {}), [])


class PrerequisitoComEvidenciaTests(unittest.TestCase):

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

    def _readiness(self) -> dict:
        r = self.client.get(
            f"/api/v1/student/activities/{self.atividade}/readiness")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _prereq(self, codigo: str) -> dict:
        for c in self._readiness()["required_contents"]:
            for p in c.get("prerequisites") or []:
                if p.get("code") == codigo:
                    return p
        self.fail(f"{codigo} nao aparece como pre-requisito")

    def _responder_base(self, *, quantas=3, acertos=2) -> None:
        """Responde `quantas` questões da base, acertando `acertos`."""
        r = self.client.post("/api/v1/student/practice",
                             json={"content_code": BALANC,
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
        self.client.post("/api/v1/student/domain/rebuild")

    # ----------------------------------------------------------------------

    def test_sem_evidencia_o_prerequisito_vem_zerado(self):
        """O caso honesto: nada respondido, nada a reportar."""
        p = self._prereq(BALANC)
        self.assertFalse(p.get("answered"))
        self.assertIsNone(p.get("accuracy"))

    def test_com_evidencia_o_prerequisito_traz_quantas_e_quanto(self):
        self._responder_base(quantas=3, acertos=2)
        p = self._prereq(BALANC)
        self.assertEqual(p.get("answered"), 3,
                         "o pre-requisito chegou sem a evidencia que existe")
        self.assertIsNotNone(p.get("accuracy"))
        self.assertAlmostEqual(p["accuracy"], 2 / 3, places=2)

    def test_medido_o_sistema_nao_manda_rediagnosticar(self):
        """A consequência que motivou tudo isto."""
        self._responder_base(quantas=3, acertos=2)
        passo = self._readiness()["next_step"]
        if passo.get("content_code") != BALANC:
            return  # o passo nem e mais sobre a base
        self.assertNotEqual(
            passo["kind"], "DIAGNOSTIC",
            "mandou diagnosticar de novo o que acabou de ser respondido")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
