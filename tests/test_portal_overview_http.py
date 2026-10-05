"""`GET /api/v1/portal/overview` — o que o Portal mostra a quem entrou.

É a primeira chamada que o produto faz. Ela responde três coisas:

    quem sou      identidade e papel reais, do vínculo
    onde estou    a instituição, pelo nome
    o que tenho   os seis módulos, com o estado de cada um

NADA AQUI É INVENTADO
======================
O nome da escola vem de `schools`. O papel vem de `UserSchoolLink`. Os
módulos contratados vêm de `school_modules`, pela mesma
`AuthorizationService.resolve_context` que o resto do sistema usa. Se um dado
não existir, ele não aparece — a Home prefere um espaço vazio a um número
bonito e falso.
"""

from __future__ import annotations

import asyncio
import unittest
import uuid

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import School, UserSchoolLink
from agente_ia_edu.db.models.admin import SchoolModule
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.services.modulos_do_portal import DISPONIVEL, EM_BREVE

ESCOLA = "Escola ABC"


def _ident(user: str) -> ExternalIdentityContext:
    return ExternalIdentityContext(provider="test", external_user_id=user)


class PortalOverviewTests(unittest.TestCase):

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
                escola = School(id=uuid.uuid4(), code="ABC", name=ESCOLA)
                s.add(escola)
                await s.flush()
                for chave in ("AGENTE_IA_EDU", "REDACAO_IA"):
                    s.add(SchoolModule(id=uuid.uuid4(), school_id=escola.id,
                                       module_key=chave, enabled=True))
                for quem, papel in (("aluna", "STUDENT"),
                                    ("professora", "TEACHER"),
                                    ("secretaria", "SECRETARY")):
                    s.add(UserSchoolLink(external_user_id=quem,
                                         school_id=escola.id, role=papel,
                                         scope_type="SCHOOL", active=True))
                # Uma segunda escola, que so contratou o Assessor.
                outra = School(id=uuid.uuid4(), code="XYZ", name="Escola XYZ")
                s.add(outra)
                await s.flush()
                s.add(SchoolModule(id=uuid.uuid4(), school_id=outra.id,
                                   module_key="AGENTE_IA_EDU", enabled=True))
                s.add(UserSchoolLink(external_user_id="aluno_xyz",
                                     school_id=outra.id, role="STUDENT",
                                     scope_type="SCHOOL", active=True))
                await s.commit()

        self.loop.run_until_complete(prep())
        self.app = create_app()
        self.app.dependency_overrides[get_session_factory] = lambda: self.factory
        self.client = TestClient(self.app)

    def tearDown(self):
        self.app.dependency_overrides.clear()
        self.client.close()
        self.loop.run_until_complete(self.engine.dispose())
        self.loop.close()

    def _como(self, quem: str) -> dict:
        self.app.dependency_overrides[get_current_identity] = lambda: _ident(quem)
        r = self.client.get("/api/v1/portal/overview")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _modulos(self, quem: str) -> dict:
        return {m["key"]: m for m in self._como(quem)["modules"]}

    # -- A e B: quem sou, onde estou ---------------------------------------

    def test_A_o_portal_responde(self):
        self.assertIn("modules", self._como("aluna"))

    def test_B_a_identidade_e_a_instituicao_sao_reais(self):
        d = self._como("aluna")
        self.assertEqual(d["user"]["external_id"], "aluna")
        self.assertEqual(d["user"]["role"], "STUDENT")
        self.assertEqual(d["institution"]["name"], ESCOLA)

    def test_B_o_papel_vem_do_vinculo_nao_de_um_padrao(self):
        self.assertEqual(self._como("professora")["user"]["role"], "TEACHER")

    def test_B_quem_nao_tem_vinculo_nao_recebe_instituicao_inventada(self):
        d = self._como("ninguem")
        self.assertIsNone(d["institution"]["name"])
        self.assertEqual([m for m in d["modules"] if m["can_access"]], [])

    # -- C e D: os dois modulos prontos ------------------------------------

    def test_C_redacao_acessivel_para_quem_tem_direito(self):
        m = self._modulos("aluna")["REDACAO_IA"]
        self.assertTrue(m["can_access"])
        self.assertEqual(m["route"], "/redacao")

    def test_D_assessor_acessivel_para_quem_tem_direito(self):
        m = self._modulos("aluna")["AGENTE_IA_EDU"]
        self.assertTrue(m["can_access"])
        self.assertTrue(m["route"].startswith("/student/"))

    # -- E: nao autorizado nao vira acesso ---------------------------------

    def test_E_escola_sem_o_modulo_nao_recebe_rota(self):
        m = self._modulos("aluno_xyz")["REDACAO_IA"]
        self.assertFalse(m["can_access"])
        self.assertIsNone(m["route"])

    def test_E_papel_sem_permissao_nao_recebe_rota(self):
        m = self._modulos("secretaria")["AGENTE_IA_EDU"]
        self.assertFalse(m["can_access"])
        self.assertIsNone(m["route"])

    # -- F a I: os quatro modulos futuros ----------------------------------

    def test_FGHI_os_quatro_futuros_aparecem_como_em_breve(self):
        m = self._modulos("aluna")
        for chave in ("SAEB", "ACADEMICO", "FORMACAO", "SIMULADOS"):
            with self.subTest(modulo=chave):
                self.assertEqual(m[chave]["status"], EM_BREVE)

    def test_O_nenhum_em_breve_tem_rota(self):
        """O critério que impede uma porta para lugar nenhum."""
        for chave, m in self._modulos("aluna").items():
            if m["status"] == EM_BREVE:
                with self.subTest(modulo=chave):
                    self.assertIsNone(m["route"])
                    self.assertFalse(m["can_access"])

    def test_os_seis_modulos_do_ecossistema_aparecem_sempre(self):
        self.assertEqual(len(self._modulos("aluna")), 6)
        self.assertEqual(len(self._modulos("aluno_xyz")), 6)

    # -- P: nenhum dado inventado ------------------------------------------

    def test_P_o_portal_nao_devolve_metrica_nenhuma(self):
        """A Home V1 não tem números. Se um aparecer aqui, alguém inventou."""
        d = self._como("aluna")
        proibidos = ("stats", "metrics", "counts", "pending", "notifications",
                     "grades", "students_count", "activities")
        for campo in proibidos:
            with self.subTest(campo=campo):
                self.assertNotIn(campo, d)

    def test_P_o_portal_so_devolve_o_que_precisa(self):
        self.assertEqual(set(self._como("aluna")),
                         {"user", "institution", "modules"})

    # -- M: o backend continua protegendo ----------------------------------

    def test_M_esconder_o_card_nao_e_a_protecao(self):
        """A secretaria não vê o card do Assessor com rota — e, se montar a
        URL na mão, o guard da própria rota é que a barra. O Portal apresenta;
        quem protege é o backend de cada módulo.

        Aqui se prova a metade do Portal: ele não entrega rota a quem não
        pode. A outra metade é dos testes de autorização de cada módulo, que
        já existem e continuam verdes.
        """
        self.assertIsNone(self._modulos("secretaria")["AGENTE_IA_EDU"]["route"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
