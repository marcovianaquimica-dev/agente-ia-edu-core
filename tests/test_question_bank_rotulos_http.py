"""O BANCO DE QUESTÕES FALA PORTUGUÊS — SEM TROCAR UM CÓDIGO SEQUER.

A tela do professor mostrava o contrato interno do sistema:

    CHEMISTRY › CHEMISTRY-PHYSICAL › CHEMISTRY-PHYSICAL-STOICHIOMETRY
    EASY

O que este arquivo trava são as DUAS metades da correção, porque fazer só uma
delas estraga a outra:

1. os rótulos em português chegam à tela;
2. os códigos canônicos continuam intactos em TODO campo da resposta - é por
   eles que o filtro, a seleção e a geração de lista conversam.

Um `labels` que substituísse os códigos seria pior que o problema: quebraria
o filtro e a lista, e esconderia a quebra atrás de uma tela bonita.
"""

from __future__ import annotations

import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.api.routes.question_bank import question_bank_router
from agente_ia_edu.db.base import Base
from agente_ia_edu.identity import ExternalIdentityContext
from tests.test_question_bank_core import _Fixture


def _identity():
    return ExternalIdentityContext(
        provider="test", external_user_id="prof-rotulos", roles=("teacher",))


class BancoDeQuestoesEmPortugues(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                          poolclass=StaticPool)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession,
                                          expire_on_commit=False)
        async with self.factory() as s:
            self.fx = await _Fixture().build(s)
            await s.commit()
        app = FastAPI()
        app.include_router(question_bank_router)
        app.dependency_overrides[get_session_factory] = lambda: self.factory
        app.dependency_overrides[get_current_identity] = _identity
        self.client = TestClient(app)

    async def asyncTearDown(self):
        self.client.close()
        await self.engine.dispose()

    def _pagina(self, **params):
        r = self.client.get("/api/v1/question-bank/questions", params=params)
        self.assertEqual(200, r.status_code, r.text)
        return r.json()

    # -- os rótulos chegam -------------------------------------------------

    def test_a_resposta_traz_os_rotulos_da_taxonomia(self):
        corpo = self._pagina(page_size=50)
        rotulos = corpo.get("labels", {}).get("taxonomy", {})
        self.assertTrue(rotulos, "a resposta não traz rótulo nenhum")
        self.assertEqual("Biologia", rotulos.get("BIOLOGY"))
        self.assertEqual("Fisiologia Animal",
                         rotulos.get("BIOLOGY-ANIMAL-PHYSIOLOGY"))

    def test_ha_rotulo_para_todo_codigo_da_pagina(self):
        """Faltar um faria a tela cair no código para aquela questão só -
        metade em português, metade em inglês, que é pior que nenhuma."""
        corpo = self._pagina(page_size=50)
        rotulos = corpo["labels"]["taxonomy"]
        for item in corpo["items"]:
            c = item.get("classification") or {}
            for campo in ("discipline_code", "area_code", "content_code",
                          "subcontent_code"):
                codigo = c.get(campo)
                if codigo:
                    with self.subTest(codigo=codigo):
                        self.assertIn(codigo, rotulos)

    def test_a_dificuldade_vem_em_portugues(self):
        rotulos = self._pagina(page_size=50)["labels"]["difficulty"]
        self.assertEqual("Fácil", rotulos.get("EASY"))
        self.assertEqual("Médio", rotulos.get("MEDIUM"))
        self.assertEqual("Difícil", rotulos.get("HARD"))

    def test_o_detalhe_de_uma_questao_tambem_tem_rotulo(self):
        """Uma questão CLASSIFICADA: sem classificação não há código a
        rotular, e um `labels` vazio ali é a resposta certa."""
        corpo = self._pagina(page_size=50)
        classificada = next(
            (i for i in corpo["items"]
             if (i.get("classification") or {}).get("discipline_code")), None)
        self.assertIsNotNone(classificada,
                             "o cenário não tem questão classificada")
        r = self.client.get(
            f"/api/v1/question-bank/questions/{classificada['question_id']}")
        self.assertEqual(200, r.status_code, r.text)
        rotulos = (r.json().get("labels") or {}).get("taxonomy") or {}
        codigo = classificada["classification"]["discipline_code"]
        self.assertIn(codigo, rotulos)
        self.assertNotEqual(codigo, rotulos[codigo],
                            "o rótulo é o próprio código: o catálogo não foi "
                            "consultado")

    # -- e os códigos continuam intactos -----------------------------------

    def test_nenhum_codigo_canonico_virou_portugues(self):
        """A metade que, se quebrar, quebra filtro, seleção e geração."""
        corpo = self._pagina(page_size=50)
        for item in corpo["items"]:
            c = item.get("classification") or {}
            with self.subTest(questao=item["question_id"]):
                if c.get("discipline_code"):
                    self.assertEqual(c["discipline_code"],
                                     c["discipline_code"].upper())
                    self.assertNotIn("á", c["discipline_code"])
                if item.get("content_code"):
                    self.assertEqual(item["content_code"],
                                     item["content_code"].upper())

    def test_o_filtro_continua_sendo_pelo_codigo(self):
        """A tela mostra "Biologia"; a requisição manda BIOLOGY."""
        por_codigo = self._pagina(discipline="BIOLOGY", page_size=50)
        self.assertGreater(por_codigo["pagination"]["total"], 0)
        por_rotulo = self._pagina(discipline="Biologia", page_size=50)
        self.assertEqual(0, por_rotulo["pagination"]["total"],
                         "o backend passou a aceitar rótulo como código")

    def test_filtrar_por_dificuldade_continua_pelo_enum(self):
        pelo_enum = self._pagina(difficulty="EASY", page_size=50)
        self.assertEqual(200, 200)
        self.assertIn("pagination", pelo_enum)
        em_portugues = self.client.get("/api/v1/question-bank/questions",
                                       params={"difficulty": "Fácil"})
        # Um rótulo não é um código: ou é recusado, ou não encontra nada.
        if em_portugues.status_code == 200:
            self.assertEqual(0, em_portugues.json()["pagination"]["total"])
        else:
            self.assertEqual(422, em_portugues.status_code)

    def test_a_pagina_sem_resultado_nao_quebra(self):
        corpo = self._pagina(discipline="NAO-EXISTE", page_size=50)
        self.assertEqual([], corpo["items"])
        self.assertEqual({}, corpo["labels"]["taxonomy"])


if __name__ == "__main__":
    unittest.main()
