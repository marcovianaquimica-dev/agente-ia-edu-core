"""OS QUATRO NÍVEIS DE AUTONOMIA — §14.

O §14 não fala de quanto o aluno sabe. Ele fala de QUANTO O EDU PODE FAZER
sozinho, e separa três naturezas de plano que não podem se misturar:

    planejamento institucional        o que a escola decidiu
    planejamento pessoal confirmado   o que o aluno confirmou
    recomendação adaptativa           o que o Edu sugere

E quatro níveis:

    1  intervenção pedagógica local — pode ocorrer automaticamente
    2  ajuste dentro de compromisso existente — quando permitido, COM TRANSPARÊNCIA
    3  mudança relevante em horário, carga, prioridade ou objetivo CONFIRMADO — exige confirmação
    4  obrigação ou decisão institucional — exige autoridade e permissão

FECHADO POR FALHA, E NÃO POR LISTA
===================================
A decisão de projeto que mais importa aqui: ação que ninguém classificou cai
no nível 4. Uma política que respondesse "nível 1" para o desconhecido
autorizaria automaticamente justamente o que ninguém pensou — e o §14 existe
para o contrário.

A FRASE QUE VIROU TESTE
========================
"O Edu não poderá alterar silenciosamente compromissos escolares." Isso não
se prova com uma constante: prova-se fazendo o aluno percorrer o ciclo
inteiro e conferindo que a tarefa da escola saiu de lá com o mesmo prazo, o
mesmo alvo e os mesmos metadados com que entrou. É o que a última classe faz.
"""

from __future__ import annotations

import asyncio
import unittest
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import (
    get_current_authenticated_context,
    get_session_factory,
)
from agente_ia_edu.db.base import Base

from test_invariantes_piloto_zero import ESTEQ, _ctx, _seed


class OSQUATRONIVEISEXISTEM(unittest.TestCase):

    def test_sao_quatro_e_na_ordem_do_14(self):
        from agente_ia_edu.services import niveis_de_autonomia as na

        self.assertEqual((1, 2, 3, 4), na.NIVEIS)

    def test_cada_nivel_exige_uma_coisa_diferente(self):
        from agente_ia_edu.services import niveis_de_autonomia as na

        exigencias = {na.exigencia_do_nivel(n) for n in na.NIVEIS}
        self.assertEqual(4, len(exigencias), f"níveis empatados: {exigencias}")

    def test_so_o_nivel_1_age_sozinho(self):
        from agente_ia_edu.services import niveis_de_autonomia as na

        self.assertTrue(na.nivel_age_sozinho(1))
        for n in (2, 3, 4):
            with self.subTest(n):
                self.assertFalse(na.nivel_age_sozinho(n))


class TODAACAOTEMNIVEL(unittest.TestCase):

    def test_nenhuma_acao_conhecida_ficou_sem_nivel_valido(self):
        from agente_ia_edu.services import niveis_de_autonomia as na

        self.assertTrue(na.ACOES)
        for acao in na.ACOES:
            with self.subTest(acao):
                self.assertIn(na.nivel_da_acao(acao), na.NIVEIS)

    def test_e_nenhuma_ficou_sem_natureza_de_plano(self):
        from agente_ia_edu.services import niveis_de_autonomia as na

        for acao in na.ACOES:
            with self.subTest(acao):
                self.assertIn(na.plano_afetado(acao), na.PLANOS)

    def test_as_tres_naturezas_de_plano_sao_distintas(self):
        from agente_ia_edu.services import niveis_de_autonomia as na

        self.assertEqual(3, len(set(na.PLANOS)))


class ODESCONHECIDONAOEAUTOMATICO(unittest.TestCase):
    """A decisão de projeto do módulo: fechado por falha."""

    def test_acao_que_ninguem_classificou_cai_no_nivel_4(self):
        from agente_ia_edu.services import niveis_de_autonomia as na

        self.assertEqual(4, na.nivel_da_acao("INVENTAR_ALGO_AMANHA"))

    def test_e_portanto_nao_pode_acontecer_sozinha(self):
        from agente_ia_edu.services import niveis_de_autonomia as na

        self.assertFalse(na.pode_agir_sozinho("INVENTAR_ALGO_AMANHA"))
        self.assertTrue(na.exige_autoridade("INVENTAR_ALGO_AMANHA"))

    def test_nem_com_nome_vazio_ou_nulo(self):
        from agente_ia_edu.services import niveis_de_autonomia as na

        for ruim in ("", None, "   "):
            with self.subTest(repr(ruim)):
                self.assertEqual(4, na.nivel_da_acao(ruim))


class OQUEOEDUFAZSOZINHOENIVEL1(unittest.TestCase):
    """§14: "intervenções pedagógicas locais podem ocorrer automaticamente"."""

    def test_interromper_para_intervir_e_nivel_1(self):
        from agente_ia_edu.services import niveis_de_autonomia as na

        self.assertEqual(1, na.nivel_da_acao(na.ACAO_INTERROMPER_PARA_INTERVIR))
        self.assertTrue(na.pode_agir_sozinho(na.ACAO_INTERROMPER_PARA_INTERVIR))

    def test_oferecer_o_proximo_passo_tambem(self):
        from agente_ia_edu.services import niveis_de_autonomia as na

        self.assertTrue(na.pode_agir_sozinho(na.ACAO_OFERECER_PROXIMO_PASSO))

    def test_e_nenhuma_acao_de_nivel_1_toca_plano_institucional(self):
        from agente_ia_edu.services import niveis_de_autonomia as na

        for acao in na.acoes_do_nivel(1):
            with self.subTest(acao):
                self.assertNotEqual(na.PLANO_INSTITUCIONAL,
                                    na.plano_afetado(acao))


class COMPROMISSOEXIGEMAISQUEAUTOMATICO(unittest.TestCase):

    def test_mexer_no_prazo_da_tarefa_e_nivel_4(self):
        from agente_ia_edu.services import niveis_de_autonomia as na

        self.assertEqual(4, na.nivel_da_acao(na.ACAO_ALTERAR_PRAZO_DA_TAREFA))

    def test_dispensar_tarefa_da_escola_tambem(self):
        from agente_ia_edu.services import niveis_de_autonomia as na

        self.assertTrue(na.exige_autoridade(na.ACAO_DISPENSAR_TAREFA_DA_ESCOLA))

    def test_mudar_objetivo_CONFIRMADO_exige_confirmacao(self):
        from agente_ia_edu.services import niveis_de_autonomia as na

        self.assertEqual(3, na.nivel_da_acao(na.ACAO_MUDAR_OBJETIVO_CONFIRMADO))
        self.assertTrue(na.exige_confirmacao(na.ACAO_MUDAR_OBJETIVO_CONFIRMADO))

    def test_nenhuma_acao_sobre_plano_institucional_e_automatica(self):
        """A varredura da política: se alguém classificar errado, cai aqui."""
        from agente_ia_edu.services import niveis_de_autonomia as na

        for acao in na.ACOES:
            if na.plano_afetado(acao) == na.PLANO_INSTITUCIONAL:
                with self.subTest(acao):
                    self.assertFalse(na.pode_agir_sozinho(acao))

    def test_e_nenhuma_sobre_plano_pessoal_confirmado_tambem(self):
        from agente_ia_edu.services import niveis_de_autonomia as na

        for acao in na.ACOES:
            if na.plano_afetado(acao) == na.PLANO_PESSOAL_CONFIRMADO:
                with self.subTest(acao):
                    self.assertFalse(na.pode_agir_sozinho(acao))


class ONIVEL2EXIGETRANSPARENCIA(unittest.TestCase):
    """§14: "quando permitidos, com transparência"."""

    def test_criar_pratica_propria_e_nivel_2(self):
        from agente_ia_edu.services import niveis_de_autonomia as na

        self.assertEqual(2, na.nivel_da_acao(na.ACAO_CRIAR_PRATICA_PROPRIA))

    def test_e_exige_transparencia(self):
        from agente_ia_edu.services import niveis_de_autonomia as na

        self.assertTrue(na.exige_transparencia(na.ACAO_CRIAR_PRATICA_PROPRIA))

    def test_quem_exige_confirmacao_tambem_exige_transparencia(self):
        """Confirmar sem saber o que se confirma não é confirmação."""
        from agente_ia_edu.services import niveis_de_autonomia as na

        for acao in na.ACOES:
            if na.exige_confirmacao(acao):
                with self.subTest(acao):
                    self.assertTrue(na.exige_transparencia(acao))

    def test_a_descricao_da_acao_e_escrita_para_gente(self):
        from agente_ia_edu.services import niveis_de_autonomia as na

        for acao in na.ACOES:
            with self.subTest(acao):
                texto = na.descricao_da_acao(acao)
                self.assertTrue(texto)
                self.assertNotIn("_", texto, "vazou o código da ação")


class OEDUNAOALTERACOMPROMISSOESCOLAR(unittest.TestCase):
    """A frase do §14, medida no banco depois do ciclo inteiro.

    Não é uma constante que prova isto: é o aluno praticando, respondendo,
    sendo corrigido e pedindo o relatório — e a tarefa da escola saindo de lá
    com o mesmo prazo, o mesmo alvo e os mesmos metadados.
    """

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                          poolclass=StaticPool)
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession,
                                          expire_on_commit=True)

        async def prep():
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            return await _seed(self.factory)

        self.fx = self.loop.run_until_complete(prep())
        self.app = create_app()
        self.app.dependency_overrides[get_session_factory] = lambda: self.factory
        self.app.dependency_overrides[get_current_authenticated_context] = _ctx
        self.client = TestClient(self.app)

    def tearDown(self):
        self.app.dependency_overrides.clear()
        self.client.close()
        self.loop.run_until_complete(self.engine.dispose())
        self.loop.close()

    def _tarefa_da_escola(self) -> dict:
        from agente_ia_edu.db.models import ActivityAssignment

        async def ler():
            async with self.factory() as s:
                linha = (await s.execute(
                    select(ActivityAssignment).where(
                        # UUID, e nao texto: a coluna e `Uuid`, e um
                        # `==` com string estoura no dialeto que valida tipo.
                        ActivityAssignment.id == uuid.UUID(self.fx["atividade"])))
                ).scalar_one()
                return {"due_at": linha.due_at,
                        "available_from": linha.available_from,
                        "target_type": linha.target_type,
                        "target_id": linha.target_id,
                        "status": linha.status,
                        "metadata": dict(linha.metadata_ or {})}

        return self.loop.run_until_complete(ler())

    def test_o_ciclo_do_aluno_nao_mexe_na_tarefa_da_escola(self):
        antes = self._tarefa_da_escola()

        r = self.client.post("/api/v1/student/practice",
                             json={"content_code": ESTEQ, "question_count": 3})
        self.assertEqual(r.status_code, 200, r.text)
        self.client.get("/api/v1/student/readiness")
        self.client.get("/api/v1/student/progress")
        self.client.get("/api/v1/student/learning-support-report")

        self.assertEqual(antes, self._tarefa_da_escola(),
                         "o Edu alterou a tarefa da escola pelo caminho do aluno")

    def test_a_pratica_do_edu_nasce_separada_da_tarefa_da_escola(self):
        """§14: a separação entre plano institucional e recomendação."""
        from agente_ia_edu.services.curriculum_domain_map import (
            ORIGIN_OFFICIAL_ACTIVITY,
        )

        r = self.client.post("/api/v1/student/practice",
                             json={"content_code": ESTEQ, "question_count": 3})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotEqual(ORIGIN_OFFICIAL_ACTIVITY, r.json().get("origin"))

    def test_e_o_aluno_nao_pode_PEDIR_origem_institucional(self):
        """O campo não existe no contrato — pedir não é exprimível."""
        r = self.client.post("/api/v1/student/practice",
                             json={"content_code": ESTEQ, "question_count": 3,
                                   "origin": "OFFICIAL_ACTIVITY"})
        if r.status_code == 200:
            from agente_ia_edu.services.curriculum_domain_map import (
                ORIGIN_OFFICIAL_ACTIVITY,
            )
            self.assertNotEqual(ORIGIN_OFFICIAL_ACTIVITY, r.json().get("origin"),
                                "o navegador conseguiu forjar uma tarefa da escola")
        else:
            self.assertIn(r.status_code, (400, 422))

    def test_a_criacao_diz_em_que_nivel_de_autonomia_ela_aconteceu(self):
        """Transparência do §14 no contrato, e não só no comentário."""
        r = self.client.post("/api/v1/student/practice",
                             json={"content_code": ESTEQ, "question_count": 3})
        self.assertEqual(r.status_code, 200, r.text)
        a = r.json().get("autonomia") or {}
        self.assertEqual(2, a.get("nivel"))
        self.assertTrue(a.get("exige"))
        self.assertTrue(a.get("descricao"))


if __name__ == "__main__":
    unittest.main()
