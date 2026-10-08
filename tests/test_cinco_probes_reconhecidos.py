"""OS CINCO PROBES PUBLICADOS SÃO RECONHECIDOS — §11 do bloco.

O teste NÃO pergunta pelo código do item. Ele publica os cinco pelo script
real, carrega o acervo pelo mecanismo real e verifica que os METADADOS E
RELACIONAMENTOS pedagógicos de cada um o qualificam — que é o que o contrato
de fato usa.

POR QUE ISSO IMPORTA MAIS DO QUE PARECE
========================================
Um teste que procurasse `SOND-EST-MASSA-MOLAR-1` passaria mesmo que o
contrato fosse `external_id.startswith("SOND-")`. Este aqui quebra se a
publicação deixar de gravar `purpose`, se a classificação não for
HUMAN_VALIDATED, se o item sair DRAFT, se o gabarito não fechar — isto é,
quebra pelas razões certas.

O acervo é montado do zero em SQLite, pelo próprio
`scripts/publicar_sondagem_estequiometria.py`. Testar contra o Postgres de
desenvolvimento provaria que o banco de alguém está certo hoje, não que a
publicação produz itens qualificados.
"""

from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import unittest
import uuid as _uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import CatalogNode
from agente_ia_edu.services.grafo_estequiometria import (
    CONTEUDO,
    INTEGRADO,
    LEITURA_FORMULA,
    MASSA_MOL,
    MASSA_MOLAR,
    PROPORCAO,
)
from agente_ia_edu.services.instrumento_de_sondagem import (
    ORIGEM_CURADA,
    inelegibilidade,
)
from agente_ia_edu.services.seletor_de_sondagem import SeletorDeSondagem
from agente_ia_edu.services.sondagem_estequiometria import ITENS

_SCRIPT = (pathlib.Path(__file__).resolve().parent.parent
           / "scripts/publicar_sondagem_estequiometria.py")
_spec = importlib.util.spec_from_file_location("publicar_sondagem_p", _SCRIPT)
publicador = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(publicador)

# O que o bloco manda verificar, nome por nome.
ESPERADO = {
    "SOND-EST-FORMULA-1": LEITURA_FORMULA,
    "SOND-EST-MASSA-MOLAR-1": MASSA_MOLAR,
    "SOND-EST-MASSA-MOL-1": MASSA_MOL,
    "SOND-EST-PROPORCAO-1": PROPORCAO,
    "SOND-EST-INTEGRADO-1": INTEGRADO,
}


class CincoProbes(unittest.TestCase):

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
            async with self.factory() as s:
                await publicador.publicar(s)

        self.loop.run_until_complete(prep())

    def tearDown(self):
        self.loop.run_until_complete(self.engine.dispose())
        self.loop.close()

    def _candidatos(self):
        async def run():
            async with self.factory() as s:
                return await SeletorDeSondagem(s).candidatos(CONTEUDO)
        return self.loop.run_until_complete(run())

    def _instrumentos(self, habilidades):
        async def run():
            async with self.factory() as s:
                return await SeletorDeSondagem(s).instrumentos(
                    conteudo=CONTEUDO, habilidades=habilidades)
        return self.loop.run_until_complete(run())

    # -- os cinco, um por um ------------------------------------------------

    def test_os_cinco_foram_publicados(self):
        self.assertEqual(len(ITENS), len(self._candidatos()))

    def test_cada_um_e_elegivel_para_a_sua_micro_habilidade(self):
        por_habilidade = {c.habilidade: c for c in self._candidatos()}
        for chave, habilidade in ESPERADO.items():
            with self.subTest(item=chave, habilidade=habilidade):
                candidato = por_habilidade.get(habilidade)
                self.assertIsNotNone(
                    candidato, f"{chave} não produziu candidato em {habilidade}")
                motivo = inelegibilidade(candidato, habilidade=habilidade,
                                         conteudo=CONTEUDO)
                self.assertIsNone(motivo, f"{chave} inelegível: {motivo}")

    def test_cada_um_e_elegivel_PELOS_METADADOS_e_nao_pelo_codigo(self):
        """A asserção que o bloco pede: os campos reais qualificam."""
        por_habilidade = {c.habilidade: c for c in self._candidatos()}
        for chave, habilidade in ESPERADO.items():
            c = por_habilidade[habilidade]
            with self.subTest(item=chave):
                self.assertEqual("PROBE", c.finalidade)
                self.assertEqual("HUMAN_VALIDATED", c.provenance)
                self.assertTrue((c.validado_por or "").strip())
                self.assertEqual("ACTIVE", c.lifecycle)
                self.assertEqual("PUBLISHED", c.status_da_questao)
                self.assertEqual("PUBLIC", c.visibilidade)
                self.assertEqual(CONTEUDO, c.conteudo)
                self.assertTrue(c.gabarito_definido)
                self.assertFalse(c.dependencia_visual)
                self.assertFalse(c.protegida)

    def test_cada_habilidade_recebe_o_SEU_instrumento_e_como_curado(self):
        habilidades = list(ESPERADO.values())
        escolhas = self._instrumentos(habilidades)
        self.assertEqual(len(habilidades), len(escolhas))
        for escolha in escolhas:
            with self.subTest(habilidade=escolha.habilidade):
                self.assertEqual(ORIGEM_CURADA, escolha.origem)

    def test_a_ordem_pedida_e_a_ordem_devolvida(self):
        habilidades = [MASSA_MOLAR, LEITURA_FORMULA, PROPORCAO]
        escolhas = self._instrumentos(habilidades)
        self.assertEqual(habilidades, [e.habilidade for e in escolhas])

    def test_nenhum_item_e_servido_duas_vezes(self):
        escolhas = self._instrumentos(list(ESPERADO.values()))
        ids = [e.question_version_id for e in escolhas]
        self.assertEqual(len(set(ids)), len(ids))

    def test_o_instrumento_servido_e_o_classificado_naquela_habilidade(self):
        """A prova de que a habilidade não vaza (§10.D) no acervo real."""
        por_id = {c.question_version_id: c for c in self._candidatos()}
        for escolha in self._instrumentos(list(ESPERADO.values())):
            with self.subTest(habilidade=escolha.habilidade):
                self.assertEqual(escolha.habilidade,
                                 por_id[escolha.question_version_id].habilidade)

    def test_habilidade_sem_item_nao_produz_instrumento(self):
        """`CONCEITO_DE_MOL` não tem questão nenhuma: a lista vem curta."""
        self.assertEqual([], self._instrumentos(["CONCEITO_DE_MOL"]))

    def test_as_cinco_habilidades_aparecem_como_mensuraveis(self):
        async def run():
            async with self.factory() as s:
                return await SeletorDeSondagem(s).habilidades_mensuraveis(
                    CONTEUDO)
        self.assertEqual(set(ESPERADO.values()),
                         self.loop.run_until_complete(run()))


class AREPETICAONAOMUDAONADA(unittest.TestCase):
    """§10.G, contra o acervo real: o resultado não depende de sorte."""

    def setUp(self):
        CincoProbes.setUp(self)

    def tearDown(self):
        CincoProbes.tearDown(self)

    def test_dez_execucoes_devolvem_exatamente_o_mesmo(self):
        async def run():
            async with self.factory() as s:
                sel = SeletorDeSondagem(s)
                return [
                    tuple((e.habilidade, e.question_version_id, e.origem)
                          for e in await sel.instrumentos(
                              conteudo=CONTEUDO,
                              habilidades=list(ESPERADO.values())))
                    for _ in range(10)
                ]

        execucoes = self.loop.run_until_complete(run())
        self.assertEqual(1, len(set(execucoes)))


if __name__ == "__main__":
    unittest.main()
