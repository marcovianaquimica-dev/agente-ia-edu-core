"""A PUBLICAÇÃO DA SONDAGEM — e a garantia de rodá-la duas vezes.

Os cinco itens são a fonte de verdade pedagógica no repositório. O aluno,
porém, é servido pelo Question Bank — mesmo motor de seleção, mesmo player,
mesma correção determinística. Este script é a ponte.

O QUE ESTE ARQUIVO TRAVA
=========================
1. publicar duas vezes não duplica nada;
2. cada questão carrega a micro-habilidade que mede;
3. o gabarito publicado é o do item curado, não outro;
4. gabarito que não fecha impede a publicação — fail closed.

O terceiro e o quarto são os que importam: um item de sondagem com gabarito
errado não falha ruidosamente. Ele mede errado, em silêncio, e o erro entra
no mapa de domínio como se fosse evidência.
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
from agente_ia_edu.db.models import (
    AnswerKeyEntry,
    CatalogNode,
    ContentQuestionLink,
    PedagogicalClassification,
    Question,
    QuestionOption,
    QuestionVersion,
)
from agente_ia_edu.services.grafo_estequiometria import CONTEUDO, GRAFO
from agente_ia_edu.services.sondagem_estequiometria import ITENS

_SCRIPT = (pathlib.Path(__file__).resolve().parent.parent
           / "scripts/publicar_sondagem_estequiometria.py")
_spec = importlib.util.spec_from_file_location("publicar_sondagem", _SCRIPT)
publicador = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(publicador)


class PublicacaoDaSondagem(unittest.TestCase):

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
                # O nó curricular do conteúdo precisa existir: o script se
                # recusa a publicar questão solta, sem currículo.
                disc = CatalogNode(id=_uuid.uuid4(), code="CHEMISTRY",
                                   name="Quimica", node_type="DISCIPLINE",
                                   position=0, active=True)
                s.add(disc)
                await s.flush()
                disc.root_id = disc.id
                s.add(CatalogNode(id=_uuid.uuid4(), code=CONTEUDO,
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

    def test_publica_os_cinco(self):
        r = self._publicar()
        self.assertEqual(len(ITENS), len(r["criados"]))
        self.assertEqual(len(ITENS), self._contar(Question))
        self.assertEqual(len(ITENS), self._contar(QuestionVersion))

    def test_cada_questao_declara_a_micro_habilidade_que_mede(self):
        self._publicar()

        async def run():
            async with self.factory() as s:
                return {c.subcontent for c in
                        (await s.execute(select(PedagogicalClassification))
                         ).scalars().all()}

        self.assertEqual({i.habilidade for i in ITENS},
                         self.loop.run_until_complete(run()))

    def test_o_gabarito_publicado_e_o_do_item_curado(self):
        """A asserção que impede ensinar química errada."""
        self._publicar()

        async def run():
            async with self.factory() as s:
                saida = {}
                for q in (await s.execute(select(Question))).scalars().all():
                    chave = (q.metadata_ or {}).get("sondagem_key")
                    v = (await s.execute(select(QuestionVersion).where(
                        QuestionVersion.question_id == q.id))).scalars().first()
                    certa = (await s.execute(select(QuestionOption).where(
                        QuestionOption.question_version_id == v.id,
                        QuestionOption.is_valid_option.is_(True)))
                    ).scalars().first()
                    saida[chave] = certa.option_key
                return saida

        publicado = self.loop.run_until_complete(run())
        for item in ITENS:
            with self.subTest(item=item.key):
                self.assertEqual(item.correta, publicado[item.key])

    def test_a_chave_de_resposta_oficial_tambem_bate(self):
        self._publicar()

        async def run():
            async with self.factory() as s:
                return [e.official_answer_label for e in
                        (await s.execute(select(AnswerKeyEntry))).scalars().all()]

        self.assertEqual(sorted(i.correta for i in ITENS),
                         sorted(self.loop.run_until_complete(run())))

    def test_cada_questao_fica_ligada_ao_no_do_conteudo(self):
        self._publicar()
        self.assertEqual(len(ITENS), self._contar(ContentQuestionLink))

    def test_os_prerequisitos_do_grafo_viajam_na_classificacao(self):
        self._publicar()

        async def run():
            async with self.factory() as s:
                return {c.subcontent: list(c.prerequisites or []) for c in
                        (await s.execute(select(PedagogicalClassification))
                         ).scalars().all()}

        publicado = self.loop.run_until_complete(run())
        for skill, pres in publicado.items():
            with self.subTest(skill=skill):
                self.assertEqual(list(GRAFO.prerequisitos(skill)), pres)

    # -- e publica de novo --------------------------------------------------

    def test_rodar_duas_vezes_nao_duplica_nada(self):
        self._publicar()
        segunda = self._publicar()
        self.assertEqual([], segunda["criados"])
        self.assertEqual(len(ITENS), len(segunda["ja_existiam"]))
        self.assertEqual(len(ITENS), self._contar(Question))
        self.assertEqual(len(ITENS), self._contar(QuestionVersion))
        self.assertEqual(len(ITENS), self._contar(PedagogicalClassification))
        self.assertEqual(len(ITENS), self._contar(AnswerKeyEntry))
        self.assertEqual(len(ITENS), self._contar(ContentQuestionLink))

    def test_a_terceira_vez_tambem_nao(self):
        self._publicar()
        self._publicar()
        self._publicar()
        self.assertEqual(len(ITENS), self._contar(Question))

    def test_o_gabarito_nao_muda_na_republicacao(self):
        self._publicar()

        async def gabaritos():
            async with self.factory() as s:
                return sorted(
                    (o.question_version_id.hex, o.option_key) for o in
                    (await s.execute(select(QuestionOption).where(
                        QuestionOption.is_valid_option.is_(True)))
                     ).scalars().all())

        antes = self.loop.run_until_complete(gabaritos())
        self._publicar()
        self.assertEqual(antes, self.loop.run_until_complete(gabaritos()))

    # -- fail closed --------------------------------------------------------

    def test_gabarito_que_nao_fecha_impede_a_publicacao(self):
        from unittest import mock

        with mock.patch.object(publicador, "conferir",
                               return_value=["conta nao fecha"]):
            with self.assertRaises(SystemExit):
                self._publicar()
        self.assertEqual(0, self._contar(Question))

    def test_sem_no_curricular_nao_publica(self):
        async def apagar():
            async with self.factory() as s:
                no = (await s.execute(select(CatalogNode).where(
                    CatalogNode.code == CONTEUDO))).scalars().first()
                await s.delete(no)
                await s.commit()

        self.loop.run_until_complete(apagar())
        with self.assertRaises(SystemExit):
            self._publicar()


if __name__ == "__main__":
    unittest.main()
