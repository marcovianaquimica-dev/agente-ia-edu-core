"""A PUBLICAÇÃO DO MATERIAL — e a garantia de rodá-la duas vezes.

O conteúdo curado é a fonte de verdade no repositório. O aluno, porém, é
servido pelas tabelas da PHASE 23 e por `GET /student/materials`. Este
script é a ponte, e este arquivo o executa de verdade — não uma cópia dele.

POR QUE ISSO IMPORTA
=====================
Até 2026-10-06 não havia material publicado de Estequiometria: o único
registro era "lista teste", PRIVATE e DRAFT. `ha_material=False` fazia a
decisão do Assessor cair direto em PRÁTICA, e o aluno recebia cinco questões
onde devia receber uma explicação. O passo de ENSINO existia e não tinha o
que servir.

O QUE ESTE ARQUIVO TRAVA
=========================
1. publicar duas vezes não duplica material, seção nem bloco;
2. o material sai PUBLIC e a versão PUBLISHED — senão o aluno não a vê;
3. há uma seção por micro-habilidade, e cada uma traz o exemplo sequencial;
4. conta errada impede a publicação — fail closed;
5. sem nó curricular, não publica.
"""

from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import unittest
import uuid as _uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import CatalogNode
from agente_ia_edu.db.models.catalog import (
    MaterialBlock,
    MaterialSection,
    TheoryMaterial,
    TheoryMaterialVersion,
)
from agente_ia_edu.services.conteudo_estequiometria import (
    CONTENT_CODE,
    MICRO_HABILIDADES,
    SECOES,
)

_SCRIPT = (pathlib.Path(__file__).resolve().parent.parent
           / "scripts/publicar_material_estequiometria.py")
_spec = importlib.util.spec_from_file_location("publicar_material_est", _SCRIPT)
publicador = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(publicador)

TOTAL_BLOCOS = sum(len(s["blocks"]) for s in SECOES)


class PublicacaoDoMaterial(unittest.TestCase):

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                          poolclass=StaticPool)
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession,
                                          expire_on_commit=False)

        async def prep():
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with self.factory() as s:
                disc = CatalogNode(id=_uuid.uuid4(), code="CHEMISTRY",
                                   name="Quimica", node_type="DISCIPLINE",
                                   position=0, active=True)
                s.add(disc)
                await s.flush()
                disc.root_id = disc.id
                s.add(CatalogNode(id=_uuid.uuid4(), code=CONTENT_CODE,
                                  name="Estequiometria", node_type="CONTENT",
                                  position=0, parent_id=disc.id,
                                  root_id=disc.id, active=True))
                await s.commit()

        self.loop.run_until_complete(prep())

    def tearDown(self):
        self.loop.run_until_complete(self.engine.dispose())
        self.loop.close()

    def _publicar(self) -> dict:
        async def run():
            async with self.factory() as s:
                return await publicador.publicar(s)
        return self.loop.run_until_complete(run())

    def _contar(self, modelo) -> int:
        async def run():
            async with self.factory() as s:
                return len((await s.execute(select(modelo))).scalars().all())
        return self.loop.run_until_complete(run())

    # -- publica ------------------------------------------------------------

    def test_publica_um_material_com_uma_secao_por_habilidade(self):
        r = self._publicar()
        self.assertTrue(r["criou"])
        self.assertEqual(1, self._contar(TheoryMaterial))
        self.assertEqual(len(MICRO_HABILIDADES), self._contar(MaterialSection))
        self.assertEqual(TOTAL_BLOCOS, self._contar(MaterialBlock))

    def test_o_material_sai_publico_e_a_versao_publicada(self):
        """PRIVATE/DRAFT é exatamente o estado que fazia o aluno não ver
        nada — e foi o que se mediu antes deste bloco."""
        self._publicar()

        async def ler():
            async with self.factory() as s:
                m = (await s.execute(select(TheoryMaterial))).scalars().one()
                v = (await s.execute(
                    select(TheoryMaterialVersion))).scalars().one()
                return m.visibility_scope, v.status

        self.assertEqual(("PUBLIC", "PUBLISHED"),
                         self.loop.run_until_complete(ler()))

    def test_cada_secao_traz_o_exemplo_sequencial(self):
        self._publicar()

        async def ler():
            async with self.factory() as s:
                return [b.block_type for b in (await s.execute(
                    select(MaterialBlock))).scalars().all()]

        tipos = self.loop.run_until_complete(ler())
        self.assertEqual(len(MICRO_HABILIDADES),
                         tipos.count("STEP_SEQUENCE"))

    def test_os_passos_do_exemplo_viajam_no_metadata(self):
        """Um bloco por passo pareceria quatro exemplos; a tela precisa
        revelá-los em ordem, e para isso eles viajam juntos."""
        self._publicar()

        async def ler():
            async with self.factory() as s:
                blocos = (await s.execute(select(MaterialBlock).where(
                    MaterialBlock.block_type == "STEP_SEQUENCE"))
                ).scalars().all()
                return [(b.metadata_ or {}).get("passos") or [] for b in blocos]

        for passos in self.loop.run_until_complete(ler()):
            with self.subTest(n=len(passos)):
                self.assertGreaterEqual(len(passos), 2)
                for p in passos:
                    self.assertTrue(p["resultado"].strip())

    def test_cada_secao_aponta_para_o_no_do_conteudo(self):
        self._publicar()

        async def ler():
            async with self.factory() as s:
                no = (await s.execute(select(CatalogNode).where(
                    CatalogNode.code == CONTENT_CODE))).scalars().one()
                secoes = (await s.execute(
                    select(MaterialSection))).scalars().all()
                return {s_.content_node_id for s_ in secoes}, {no.id}

        achados, esperado = self.loop.run_until_complete(ler())
        self.assertEqual(esperado, achados)

    # -- e publica de novo --------------------------------------------------

    def test_rodar_duas_vezes_nao_duplica_nada(self):
        self._publicar()
        segunda = self._publicar()
        self.assertFalse(segunda["criou"])
        self.assertEqual(1, self._contar(TheoryMaterial))
        self.assertEqual(1, self._contar(TheoryMaterialVersion))
        self.assertEqual(len(MICRO_HABILIDADES), self._contar(MaterialSection))
        self.assertEqual(TOTAL_BLOCOS, self._contar(MaterialBlock))

    def test_a_terceira_vez_tambem_nao(self):
        self._publicar()
        self._publicar()
        self._publicar()
        self.assertEqual(TOTAL_BLOCOS, self._contar(MaterialBlock))

    # -- fail closed --------------------------------------------------------

    def test_conta_errada_impede_a_publicacao(self):
        from unittest import mock

        with mock.patch.object(publicador, "conferir",
                               return_value=["a conta nao fecha"]):
            with self.assertRaises(SystemExit):
                self._publicar()
        self.assertEqual(0, self._contar(TheoryMaterial))

    def test_sem_no_curricular_nao_publica(self):
        async def apagar():
            async with self.factory() as s:
                no = (await s.execute(select(CatalogNode).where(
                    CatalogNode.code == CONTENT_CODE))).scalars().first()
                await s.delete(no)
                await s.commit()

        self.loop.run_until_complete(apagar())
        with self.assertRaises(SystemExit):
            self._publicar()
        self.assertEqual(0, self._contar(TheoryMaterial))


if __name__ == "__main__":
    unittest.main()
