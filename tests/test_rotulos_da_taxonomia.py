"""CÓDIGO É DO SISTEMA; NOME É DO PROFESSOR.

O QUE O PROFESSOR VÊ HOJE, NO BANCO DE QUESTÕES
================================================
    CHEMISTRY › CHEMISTRY-PHYSICAL › CHEMISTRY-PHYSICAL-STOICHIOMETRY
    EASY
    placeholder do filtro de conteúdo: "código curriculum-v2"

Isso é o contrato interno do sistema impresso na tela de quem dá aula.

A FONTE DOS RÓTULOS JÁ EXISTE, E NÃO É UM DICIONÁRIO NOVO
==========================================================
`catalog_nodes` já tem, para cada código, um `name` escrito por gente:

    CHEMISTRY                          Quimica
    CHEMISTRY-PHYSICAL                 Fisico-Quimica
    CHEMISTRY-PHYSICAL-STOICHIOMETRY   Estequiometria e cálculos químicos

Então não há tradução a inventar: há uma coluna a usar. Um mapa paralelo
escrito à mão divergiria do catálogo no primeiro conteúdo novo, e seria uma
segunda verdade sobre o currículo - exatamente o que o Núcleo não faz.

A DIFICULDADE É O ÚNICO CASO SEM FONTE
=======================================
`recommended_difficulty` é um enum de três valores, sem tabela por trás. Esse
mapa é pequeno, fechado e fica num lugar só.

O QUE NUNCA MUDA
=================
O código canônico. Ele continua no banco, nos filtros, nas requisições e nos
contratos. O que muda é só o que aparece.
"""

from __future__ import annotations

import asyncio
import unittest
import uuid as _uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import CatalogNode
from agente_ia_edu.services.rotulos_da_taxonomia import (
    DIFICULDADES,
    RotulosDaTaxonomia,
    rotulo_de_dificuldade,
)

DISC = "CHEMISTRY"
AREA = "CHEMISTRY-PHYSICAL"
CONT = "CHEMISTRY-PHYSICAL-STOICHIOMETRY"


class RotulosDaTaxonomiaTests(unittest.TestCase):

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                          poolclass=StaticPool)
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession,
                                          expire_on_commit=False)
        self.loop.run_until_complete(self._seed())

    async def _seed(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with self.factory() as s:
            disc = CatalogNode(id=_uuid.uuid4(), code=DISC, name="Quimica",
                               node_type="DISCIPLINE", position=0, active=True)
            s.add(disc)
            await s.flush()
            disc.root_id = disc.id
            s.add(CatalogNode(id=_uuid.uuid4(), code=AREA, name="Fisico-Quimica",
                              node_type="AREA", position=0, parent_id=disc.id,
                              root_id=disc.id, active=True))
            s.add(CatalogNode(id=_uuid.uuid4(), code=CONT,
                              name="Estequiometria e cálculos químicos",
                              node_type="CONTENT", position=0, parent_id=disc.id,
                              root_id=disc.id, active=True))
            await s.commit()

    def tearDown(self):
        self.loop.run_until_complete(self.engine.dispose())
        self.loop.close()

    def _para(self, codigos):
        async def run():
            async with self.factory() as s:
                return await RotulosDaTaxonomia(s).para(codigos)
        return self.loop.run_until_complete(run())

    # -- o nome vem do catálogo -------------------------------------------

    def test_o_rotulo_e_o_nome_do_catalogo(self):
        self.assertEqual({DISC: "Quimica", AREA: "Fisico-Quimica",
                          CONT: "Estequiometria e cálculos químicos"},
                         self._para([DISC, AREA, CONT]))

    def test_uma_consulta_so_para_a_pagina_inteira(self):
        """Uma página tem 20 questões e até 4 códigos cada: N+1 aqui seria
        oitenta consultas para desenhar uma lista."""
        async def run():
            async with self.factory() as s:
                svc = RotulosDaTaxonomia(s)
                consultas = []
                original = s.execute

                async def espiao(*a, **kw):
                    consultas.append(1)
                    return await original(*a, **kw)

                s.execute = espiao
                await svc.para([DISC, AREA, CONT] * 20)
                return len(consultas)

        self.assertEqual(1, self.loop.run_until_complete(run()))

    # -- o que fazer com o que não se conhece ------------------------------

    def test_codigo_desconhecido_volta_ele_mesmo(self):
        """Nunca inventar tradução: mostrar o código é honesto, chutar não."""
        self.assertEqual({"NAO-EXISTE": "NAO-EXISTE"},
                         self._para(["NAO-EXISTE"]))

    def test_lista_vazia_nao_consulta_nada(self):
        self.assertEqual({}, self._para([]))
        self.assertEqual({}, self._para(None))

    def test_none_e_vazio_na_lista_sao_ignorados(self):
        self.assertEqual({DISC: "Quimica"}, self._para([DISC, None, "", "  "]))

    def test_nome_em_branco_no_catalogo_nao_vira_rotulo_vazio(self):
        """Um nó sem nome mostraria um espaço em branco onde deveria haver
        conteúdo - pior que o código."""
        async def run():
            async with self.factory() as s:
                s.add(CatalogNode(id=_uuid.uuid4(), code="SEM-NOME", name="   ",
                                  node_type="CONTENT", position=0, active=True))
                await s.commit()
                return await RotulosDaTaxonomia(s).para(["SEM-NOME"])

        self.assertEqual({"SEM-NOME": "SEM-NOME"},
                         self.loop.run_until_complete(run()))


class DificuldadeEmPortugues(unittest.TestCase):

    def test_os_tres_valores_reais(self):
        self.assertEqual("Fácil", rotulo_de_dificuldade("EASY"))
        self.assertEqual("Médio", rotulo_de_dificuldade("MEDIUM"))
        self.assertEqual("Difícil", rotulo_de_dificuldade("HARD"))

    def test_o_mapa_cobre_exatamente_o_enum(self):
        self.assertEqual({"EASY", "MEDIUM", "HARD"}, set(DIFICULDADES))

    def test_valor_desconhecido_volta_ele_mesmo(self):
        self.assertEqual("QUALQUER", rotulo_de_dificuldade("QUALQUER"))

    def test_sem_dificuldade_nao_inventa_rotulo(self):
        """514 das 595 questões do acervo não têm dificuldade atribuída."""
        self.assertIsNone(rotulo_de_dificuldade(None))
        self.assertIsNone(rotulo_de_dificuldade(""))


if __name__ == "__main__":
    unittest.main()
