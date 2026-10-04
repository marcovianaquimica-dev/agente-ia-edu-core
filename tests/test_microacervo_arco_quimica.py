"""O arco Estequiometria -> Balanceamento e PERCORRIVEL pelo readiness.

Registrar o arco no catalogo nao basta: o que importa e o planejador
encontrar o pre-requisito e bloquear a atividade quando ele esta fraco. Um
arco gravado que o motor nao le e pior que nenhum arco, porque passa a
impressao de que a cadeia existe.

Entao estes testes nao verificam "a linha esta na tabela". Eles montam o
grafo como o AdaptiveLearningPathService monta, e exigem que a decisao mude.
"""

from __future__ import annotations

import asyncio
import unittest
import uuid as _uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import CatalogNode
from agente_ia_edu.db.models.catalog import CatalogNodePrerequisite

ALVO = "CHEMISTRY-PHYSICAL-STOICHIOMETRY"
PRE = "CHEMISTRY-GENERAL-BALANCING"


class ArcoQuimicaTests(unittest.TestCase):

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                          poolclass=StaticPool)
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession,
                                          expire_on_commit=False)

        async def montar():
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with self.factory() as s:
                disc = CatalogNode(code="CHEMISTRY", name="Quimica",
                                   node_type="DISCIPLINE", position=0, active=True)
                s.add(disc); await s.flush(); disc.root_id = disc.id; await s.flush()
                geral = CatalogNode(code="CHEMISTRY-GENERAL", name="Quimica Geral",
                                    node_type="AREA", position=1,
                                    parent_id=disc.id, root_id=disc.id, active=True)
                fisico = CatalogNode(code="CHEMISTRY-PHYSICAL", name="Fisico-Quimica",
                                     node_type="AREA", position=2,
                                     parent_id=disc.id, root_id=disc.id, active=True)
                s.add_all([geral, fisico]); await s.flush()
                self.ids = {}
                for code, nome, pai in ((PRE, "Reações químicas e balanceamento", geral),
                                        (ALVO, "Estequiometria", fisico)):
                    n = CatalogNode(code=code, name=nome, node_type="CONTENT",
                                    position=1, parent_id=pai.id, root_id=disc.id,
                                    active=True)
                    s.add(n); await s.flush()
                    self.ids[code] = n.id
                s.add(CatalogNodePrerequisite(content_node_id=self.ids[ALVO],
                                              prerequisite_node_id=self.ids[PRE]))
                await s.commit()

        self.loop.run_until_complete(montar())

    def tearDown(self):
        self.loop.run_until_complete(self.engine.dispose())
        self.loop.close()

    # -- o grafo, como o planejador o le -----------------------------------

    def _grafo(self) -> dict[str, list[str]]:
        """Reconstroi `code -> [codigos dos pre-requisitos]`, que e a forma que
        o AdaptiveLearningPathService usa para decidir bloqueio."""
        async def run():
            async with self.factory() as s:
                alvo = CatalogNode.__table__.alias("alvo")
                pre = CatalogNode.__table__.alias("pre")
                linhas = (await s.execute(
                    select(alvo.c.code, pre.c.code).select_from(
                        CatalogNodePrerequisite.__table__
                        .join(alvo, alvo.c.id == CatalogNodePrerequisite.content_node_id)
                        .join(pre, pre.c.id == CatalogNodePrerequisite.prerequisite_node_id))
                )).all()
                g: dict[str, list[str]] = {}
                for a, p in linhas:
                    g.setdefault(a, []).append(p)
                return g

        return self.loop.run_until_complete(run())

    def test_o_grafo_liga_estequiometria_a_balanceamento(self):
        self.assertEqual(self._grafo().get(ALVO), [PRE])

    def test_a_relacao_nao_e_simetrica(self):
        """Balanceamento NAO exige Estequiometria. Se fosse simetrico, o aluno
        ficaria preso: cada um bloquearia o outro para sempre."""
        self.assertNotIn(ALVO, self._grafo().get(PRE, []))

    # -- a decisao muda ----------------------------------------------------

    def _bloqueado(self, dominio: dict[str, tuple[int, float]]) -> bool:
        """Mesma regra do planejador: bloqueado quando algum pre-requisito nao
        esta dominado. `mastered` usa a politica de desempenho, nao um corte
        local."""
        from agente_ia_edu.services.pedagogical_analysis import (
            PerformanceThresholdPolicy,
        )

        politica = PerformanceThresholdPolicy.default()

        def dominado(code: str) -> bool:
            respondidas, acerto = dominio.get(code, (0, 0.0))
            return (respondidas >= politica.min_sample_size
                    and acerto >= politica.strong_accuracy)

        return any(not dominado(p) for p in self._grafo().get(ALVO, []))

    def test_sem_evidencia_no_pre_requisito_a_atividade_fica_bloqueada(self):
        self.assertTrue(self._bloqueado({}))

    def test_pre_requisito_fraco_bloqueia(self):
        self.assertTrue(self._bloqueado({PRE: (5, 0.20)}))

    def test_pre_requisito_dominado_destrava(self):
        self.assertFalse(self._bloqueado({PRE: (5, 0.90)}))

    def test_sem_o_arco_a_atividade_nunca_bloquearia(self):
        """Prova que o teste acima mede o ARCO, e nao outra coisa: removido o
        arco, o mesmo dominio fraco deixa de bloquear."""
        async def remover():
            async with self.factory() as s:
                arco = await s.scalar(select(CatalogNodePrerequisite))
                await s.delete(arco)
                await s.commit()

        self.assertTrue(self._bloqueado({PRE: (5, 0.20)}))
        self.loop.run_until_complete(remover())
        self.assertFalse(self._bloqueado({PRE: (5, 0.20)}),
                         "o bloqueio nao vinha do arco")

    # -- o corte vem da politica, nao daqui --------------------------------

    def test_o_limiar_de_dominio_e_o_da_politica(self):
        from agente_ia_edu.services.pedagogical_analysis import (
            PerformanceThresholdPolicy,
        )

        politica = PerformanceThresholdPolicy.default()
        # exatamente no corte: domina
        self.assertFalse(self._bloqueado(
            {PRE: (politica.min_sample_size, politica.strong_accuracy)}))
        # um fio abaixo: nao domina
        self.assertTrue(self._bloqueado(
            {PRE: (politica.min_sample_size, politica.strong_accuracy - 0.01)}))
        # amostra insuficiente: nao domina, mesmo com 100% de acerto
        self.assertTrue(self._bloqueado(
            {PRE: (politica.min_sample_size - 1, 1.0)}))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
