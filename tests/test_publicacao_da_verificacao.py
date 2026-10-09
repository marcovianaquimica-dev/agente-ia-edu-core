"""A PUBLICAÇÃO DA VERIFICAÇÃO L0 — e a separação das duas finalidades.

Os três itens são a fonte de verdade pedagógica no repositório. O aluno é
servido pelo Question Bank — mesmo motor de seleção, mesmo player, mesma
correção determinística. Este script é a ponte, como o da sondagem.

O QUE ESTE ARQUIVO TRAVA
=========================
1. publicar duas vezes não duplica nada;
2. cada questão declara a micro-habilidade que verifica;
3. o gabarito publicado é o do item curado, não outro;
4. gabarito que não fecha impede a publicação — fail closed;
5. **a finalidade publicada é VERIFICATION, e não PROBE.**

O quinto é o que este bloco acrescenta, e é o que impede o pior dos casos:
um item escrito para ser respondido DEPOIS do ensino sendo servido como
sondagem — ou, pior, a verificação reservindo o item que o aluno já errou no
diagnóstico, que até 2026-10-08 era o único item de MASSA_MOLAR no banco.
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
from agente_ia_edu.services.grafo_estequiometria import CONTEUDO
from agente_ia_edu.services.instrumento_de_sondagem import FINALIDADE_SONDAGEM
from agente_ia_edu.services.verificacao import FINALIDADE_VERIFICACAO
from agente_ia_edu.services.verificacao_estequiometria import ITENS

_SCRIPT = (pathlib.Path(__file__).resolve().parent.parent
           / "scripts/publicar_verificacao_estequiometria.py")
_spec = importlib.util.spec_from_file_location("publicar_verificacao", _SCRIPT)
publicador = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(publicador)


class _Base(unittest.TestCase):

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


class PUBLICAOSTRES(_Base):

    def test_publica_todos_os_itens(self):
        r = self._publicar()
        self.assertEqual(len(ITENS), len(r["criados"]))
        self.assertEqual(len(ITENS), self._contar(Question))
        self.assertEqual(len(ITENS), self._contar(QuestionVersion))

    def test_cada_questao_declara_a_micro_habilidade_que_verifica(self):
        self._publicar()

        async def run():
            async with self.factory() as s:
                return {c.subcontent for c in
                        (await s.execute(select(PedagogicalClassification))
                         ).scalars().all()}

        self.assertEqual({i.habilidade for i in ITENS},
                         self.loop.run_until_complete(run()))

    def test_toda_questao_fica_ligada_ao_no_curricular(self):
        self._publicar()
        self.assertEqual(len(ITENS), self._contar(ContentQuestionLink))

    def test_toda_questao_ganha_entrada_de_gabarito(self):
        self._publicar()
        self.assertEqual(len(ITENS), self._contar(AnswerKeyEntry))


class AFINALIDADEPUBLICADAEADEVERIFICACAO(_Base):
    """O que separa este acervo do da sondagem."""

    def test_a_classificacao_declara_VERIFICATION(self):
        self._publicar()

        async def run():
            async with self.factory() as s:
                return [(c.metadata_ or {}).get("purpose") for c in
                        (await s.execute(select(PedagogicalClassification))
                         ).scalars().all()]

        finalidades = self.loop.run_until_complete(run())
        self.assertEqual([FINALIDADE_VERIFICACAO] * len(ITENS), finalidades)

    def test_e_NENHUMA_declara_PROBE(self):
        """Se declarasse, o diagnóstico passaria a medir com estes itens."""
        self._publicar()

        async def run():
            async with self.factory() as s:
                return [(c.metadata_ or {}).get("purpose") for c in
                        (await s.execute(select(PedagogicalClassification))
                         ).scalars().all()]

        self.assertNotIn(FINALIDADE_SONDAGEM,
                         self.loop.run_until_complete(run()))

    def test_a_questao_tambem_carrega_a_finalidade(self):
        """Quem olha a questão, e não a classificação, vê o mesmo."""
        self._publicar()

        async def run():
            async with self.factory() as s:
                return [(q.metadata_ or {}).get("purpose") for q in
                        (await s.execute(select(Question))).scalars().all()]

        self.assertEqual([FINALIDADE_VERIFICACAO] * len(ITENS),
                         self.loop.run_until_complete(run()))

    def test_a_validacao_humana_tem_nome(self):
        """O CheckConstraint exige, e ele está certo: sem nome não rastreia."""
        self._publicar()

        async def run():
            async with self.factory() as s:
                return [c.validated_by_external_identity for c in
                        (await s.execute(select(PedagogicalClassification))
                         ).scalars().all()]

        for nome in self.loop.run_until_complete(run()):
            self.assertTrue((nome or "").strip())

    def test_o_erro_de_cada_distrator_viaja_com_a_questao(self):
        self._publicar()

        async def run():
            async with self.factory() as s:
                return {(q.metadata_ or {}).get("verificacao_key"):
                        (q.metadata_ or {}).get("erros")
                        for q in (await s.execute(select(Question))
                                  ).scalars().all()}

        gravado = self.loop.run_until_complete(run())
        for item in ITENS:
            with self.subTest(item.key):
                self.assertEqual(dict(item.erros), gravado[item.key])


class OGABARITOPUBLICADOEODOITEMCURADO(_Base):
    """A asserção que impede ensinar química errada."""

    def test_a_alternativa_valida_e_a_correta_do_item(self):
        self._publicar()

        async def run():
            async with self.factory() as s:
                saida = {}
                for q in (await s.execute(select(Question))).scalars().all():
                    chave = (q.metadata_ or {}).get("verificacao_key")
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
            with self.subTest(item.key):
                self.assertEqual(item.correta, publicado[item.key])

    def test_ha_exatamente_uma_alternativa_valida_por_item(self):
        """Duas tornariam a correção ambígua; zero, impossível."""
        self._publicar()

        async def run():
            async with self.factory() as s:
                por_versao = {}
                for o in (await s.execute(select(QuestionOption))
                          ).scalars().all():
                    if o.is_valid_option:
                        chave = str(o.question_version_id)
                        por_versao[chave] = por_versao.get(chave, 0) + 1
                return por_versao

        contagem = self.loop.run_until_complete(run())
        self.assertEqual(len(ITENS), len(contagem))
        for chave, n in contagem.items():
            with self.subTest(chave):
                self.assertEqual(1, n)

    def test_gabarito_que_nao_fecha_impede_a_publicacao(self):
        """Fail closed — e verificado mexendo no módulo, não no banco."""
        from agente_ia_edu.services import verificacao_estequiometria as mod

        original = mod.conferir
        publicador.conferir = lambda: ["gabarito inventado"]
        try:
            with self.assertRaises(SystemExit):
                self._publicar()
            self.assertEqual(0, self._contar(Question))
        finally:
            publicador.conferir = original


class RODARDUASVEZESNAODUPLICA(_Base):

    def test_a_segunda_execucao_nao_cria_nada(self):
        self._publicar()
        r = self._publicar()
        self.assertEqual([], r["criados"])
        self.assertEqual(len(ITENS), len(r["ja_existiam"]))

    def test_e_os_contadores_ficam_iguais(self):
        self._publicar()
        antes = (self._contar(Question), self._contar(QuestionVersion),
                 self._contar(QuestionOption),
                 self._contar(PedagogicalClassification),
                 self._contar(AnswerKeyEntry),
                 self._contar(ContentQuestionLink))
        self._publicar()
        depois = (self._contar(Question), self._contar(QuestionVersion),
                  self._contar(QuestionOption),
                  self._contar(PedagogicalClassification),
                  self._contar(AnswerKeyEntry),
                  self._contar(ContentQuestionLink))
        self.assertEqual(antes, depois)

    def test_tres_execucoes_tambem(self):
        self._publicar()
        self._publicar()
        self._publicar()
        self.assertEqual(len(ITENS), self._contar(Question))


if __name__ == "__main__":
    unittest.main()
