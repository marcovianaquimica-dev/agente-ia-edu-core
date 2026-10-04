"""PERSISTENCIA DA ROTA PEDAGOGICA na sessao de estudo.

A migration 064 criou as colunas. Este arquivo cobre quem as PREENCHE.

A PERGUNTA QUE ISTO EXISTE PARA RESPONDER
==========================================
Hoje, "o aluno nao fez a tarefa de Estequiometria" e um balde unico. Dentro
dele moram duas pessoas muito diferentes:

    o que nao abriu a atividade
    o que abriu, descobriu que faltava Balanceamento, e esta estudando isso

Tratar as duas igual e o erro. A primeira precisa de cobranca; a segunda
precisa de tempo. Sem `readiness_route` + `objective_assignment_id` +
`objective_completed` gravados no momento em que a sessao e montada, a
diferenca entre elas nao e reconstruivel depois - a rota e resultado de uma
decisao tomada sobre o estado de dominio DAQUELE instante.

O QUE NAO PODE ACONTECER
=========================
Uma sessao ligada a uma tarefa perder o `objective_assignment_id` em
silencio. Se isso acontecer, o aluno vira "nao fez" sem que nada tenha dado
errado - o pior tipo de defeito, porque o dado parece consistente.
"""

from __future__ import annotations

import asyncio
import unittest
import uuid as _uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models.study_session import StudySession
from agente_ia_edu.services.question_list_store import Requester
from agente_ia_edu.services.study_session import StudySessionService

ROTAS = ("DIRECT", "DIAGNOSTIC", "PREREQUISITE_PREPARATION")
ALUNO = "pedro"


class RotaPersistidaTests(unittest.TestCase):

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                          connect_args={"check_same_thread": False},
                                          poolclass=StaticPool)
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession,
                                          expire_on_commit=False)

        async def montar():
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        self.loop.run_until_complete(montar())
        self.tarefa = _uuid.uuid4()

    def tearDown(self):
        self.loop.run_until_complete(self.engine.dispose())
        self.loop.close()

    def _requester(self):
        return Requester(external_user_id=ALUNO, school_id=None, role="STUDENT")

    def _criar(self, *, rota=None, objetivo=None, minutos=30):
        async def run():
            async with self.factory() as s:
                svc = StudySessionService(s)
                return await svc.create_student_session(
                    ALUNO, requester=self._requester(),
                    available_minutes=minutos,
                    readiness_route=rota,
                    objective_assignment_id=objetivo,
                )
        return self.loop.run_until_complete(run())

    def _linha(self, session_id):
        async def run():
            async with self.factory() as s:
                return await s.get(StudySession, _uuid.UUID(str(session_id)))
        return self.loop.run_until_complete(run())

    # -- grava de verdade ---------------------------------------------------

    def test_as_tres_rotas_sao_gravadas_na_linha_do_banco(self):
        for rota in ROTAS:
            with self.subTest(rota=rota):
                self.setUp()
                vista = self._criar(rota=rota, objetivo=self.tarefa)
                linha = self._linha(vista["id"])
                self.assertEqual(linha.readiness_route, rota)

    def test_o_objetivo_da_escola_fica_gravado_na_sessao(self):
        vista = self._criar(rota="PREREQUISITE_PREPARATION", objetivo=self.tarefa)
        linha = self._linha(vista["id"])
        self.assertEqual(str(linha.objective_assignment_id), str(self.tarefa))

    def test_sessao_nova_com_objetivo_nasce_nao_concluida(self):
        vista = self._criar(rota="DIRECT", objetivo=self.tarefa)
        linha = self._linha(vista["id"])
        self.assertFalse(linha.objective_completed)

    def test_sem_tarefa_da_escola_os_campos_ficam_nulos_e_isso_e_valido(self):
        vista = self._criar()
        linha = self._linha(vista["id"])
        self.assertIsNone(linha.readiness_route)
        self.assertIsNone(linha.objective_assignment_id)
        self.assertFalse(linha.objective_completed)

    # -- aparece no contrato ------------------------------------------------

    def test_a_rota_e_o_objetivo_aparecem_na_vista_da_sessao(self):
        vista = self._criar(rota="DIAGNOSTIC", objetivo=self.tarefa)
        self.assertEqual(vista["readiness_route"], "DIAGNOSTIC")
        self.assertEqual(str(vista["objective_assignment_id"]), str(self.tarefa))
        self.assertIs(vista["objective_completed"], False)

    # -- a rota invalida nao entra ------------------------------------------

    def test_uma_rota_inventada_e_recusada_antes_de_chegar_ao_banco(self):
        from agente_ia_edu.services.study_session import StudySessionError

        with self.assertRaises(StudySessionError):
            self._criar(rota="ROTA_QUE_NAO_EXISTE", objetivo=self.tarefa)

    # -- o objetivo nao se perde --------------------------------------------

    def test_concluir_a_sessao_nao_apaga_o_objetivo(self):
        """Terminar a sessao de preparacao nao significa ter feito a tarefa."""
        vista = self._criar(rota="PREREQUISITE_PREPARATION", objetivo=self.tarefa)

        async def run():
            async with self.factory() as s:
                svc = StudySessionService(s)
                return await svc.complete_session(
                    ALUNO, _uuid.UUID(vista["id"]), requester=self._requester())

        depois = self.loop.run_until_complete(run())
        linha = self._linha(vista["id"])
        self.assertEqual(str(linha.objective_assignment_id), str(self.tarefa))
        self.assertFalse(linha.objective_completed,
                         "concluir a preparacao marcou a TAREFA como concluida")
        self.assertEqual(depois["readiness_route"], "PREREQUISITE_PREPARATION")

    def test_so_quem_realmente_fez_a_tarefa_marca_concluida(self):
        vista = self._criar(rota="DIRECT", objetivo=self.tarefa)

        async def run():
            async with self.factory() as s:
                svc = StudySessionService(s)
                return await svc.marcar_objetivo_concluido(
                    ALUNO, _uuid.UUID(vista["id"]), requester=self._requester())

        self.loop.run_until_complete(run())
        linha = self._linha(vista["id"])
        self.assertTrue(linha.objective_completed)

    def test_marcar_concluido_sem_objetivo_e_recusado(self):
        """Nao da para concluir uma tarefa que a sessao nunca teve."""
        from agente_ia_edu.services.study_session import StudySessionError

        vista = self._criar()   # sem objetivo

        async def run():
            async with self.factory() as s:
                svc = StudySessionService(s)
                return await svc.marcar_objetivo_concluido(
                    ALUNO, _uuid.UUID(vista["id"]), requester=self._requester())

        with self.assertRaises(StudySessionError):
            self.loop.run_until_complete(run())

    # -- a distincao que tudo isto serve para permitir ----------------------

    def test_da_para_distinguir_nao_fez_de_esta_se_preparando(self):
        from sqlalchemy import select

        preparando = self._criar(rota="PREREQUISITE_PREPARATION", objetivo=self.tarefa)
        self.assertTrue(preparando["id"])

        async def contar():
            async with self.factory() as s:
                q = select(StudySession).where(
                    StudySession.objective_assignment_id == self.tarefa,
                    StudySession.objective_completed.is_(False),
                    StudySession.readiness_route == "PREREQUISITE_PREPARATION",
                )
                return len((await s.scalars(q)).all())

        self.assertEqual(self.loop.run_until_complete(contar()), 1,
                         "a consulta que Professor/Coordenacao farao nao encontra "
                         "o aluno que esta se preparando")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
