"""A prática guiada: ajuda progressiva que não vira domínio.

A REGRA QUE SUSTENTA O BLOCO
=============================
    CONSEGUIR COM AJUDA  ≠  DOMINAR SOZINHO

O motor de domínio conta toda resposta respondida, sem ponderar ajuda. Se a
prática guiada passasse por ele, acertar depois de quatro dicas entraria como
acerto igual a acertar sozinho. Então ela não passa: fica em
`guided_practice_items`, que o mapa de domínio não lê — a mesma escolha que
`material_progress` já fez para a leitura.

Este arquivo cobre os casos adversariais do bloco:

    A acertou sem dica            F recarregou no meio
    B acertou após a dica 1       G saiu e voltou
    C só acertou com ajuda máxima H tentou pular etapas
    D pediu dica antes de tentar  I a correta não vaza
    E pediu todas sem tentar      J concluir não vira domínio
"""

from __future__ import annotations

import asyncio
import unittest

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.services.itens_guiados import ITENS, item_para
from agente_ia_edu.services.pratica_guiada import (
    PraticaGuiadaService,
    SemItemGuiado,
)
from agente_ia_edu.services.question_list_store import Requester

ALUNO = "aluno_guiado"
BALANC = "CHEMISTRY-GENERAL-BALANCING"
SKILL = "CONSERVACAO_DE_ATOMOS"


def _req(quem=ALUNO):
    return Requester(external_user_id=quem, school_id=None, role="STUDENT",
                     is_platform_admin=False)


class PraticaGuiadaTests(unittest.TestCase):

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                          poolclass=StaticPool)
        # `expire_on_commit=True` DE PROPOSITO - e o que a aplicacao usa
        # (`create_session_factory` nao passa a opcao, e o padrao do
        # SQLAlchemy e expirar). Com False, ler um atributo depois do commit
        # funciona no teste e levanta MissingGreenlet em producao: foi
        # exatamente o que aconteceu aqui, e so o navegador pegou.
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession,
                                          expire_on_commit=True)

        async def prep():
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        self.loop.run_until_complete(prep())
        self.item = item_para(BALANC, SKILL)
        self.certa = self.item["correta"]
        self.errada = next(l for l in self.item["alternativas"] if l != self.certa)

    def tearDown(self):
        self.loop.run_until_complete(self.engine.dispose())
        self.loop.close()

    # -- utilidades --------------------------------------------------------

    def _rodar(self, corotina):
        async def executar():
            async with self.factory() as s:
                return await corotina(PraticaGuiadaService(s))
        return self.loop.run_until_complete(executar())

    def _abrir(self, skill=SKILL):
        return self._rodar(lambda svc: svc.abrir(
            ALUNO, BALANC, skill, requester=_req()))

    def _responder(self, escolha):
        return self._rodar(lambda svc: svc.responder(
            ALUNO, self.item["key"], escolha, requester=_req()))

    def _pedir_ajuda(self):
        return self._rodar(lambda svc: svc.pedir_ajuda(
            ALUNO, self.item["key"], requester=_req()))

    def _estado(self):
        return self._rodar(lambda svc: svc.estado(
            ALUNO, self.item["key"], requester=_req()))

    # -- abrir -------------------------------------------------------------

    def test_abrir_devolve_o_item_da_habilidade_medida(self):
        d = self._abrir()
        self.assertEqual(d["item_key"], self.item["key"])
        self.assertEqual(d["skill"], SKILL)

    def test_I_a_resposta_correta_nao_vaza_ao_abrir(self):
        """O caso adversarial que mais importa numa múltipla escolha.

        Escrevi este teste procurando a LETRA no payload e ele reprovou por
        motivo errado: a letra de cada alternativa está lá porque o aluno
        precisa vê-la. O vazamento seria outra coisa — um campo que diga qual
        delas é a certa, ou uma alternativa com forma diferente das outras.
        """
        d = self._abrir()
        texto = repr(d)
        self.assertNotIn("correta", d)
        self.assertNotIn("correct_option", d)
        self.assertNotIn("balanceada", texto)

    def test_I_as_alternativas_sao_indistinguiveis_entre_si(self):
        """Um campo a mais só na correta a entregaria sem dizer o nome dela."""
        opcoes = self._abrir()["options"]
        formas = {tuple(sorted(o)) for o in opcoes}
        self.assertEqual(len(formas), 1, f"alternativas com formas diferentes: {formas}")

    def test_abrir_duas_vezes_nao_zera_o_que_ja_aconteceu(self):
        self._abrir()
        self._pedir_ajuda()
        d = self._abrir()
        self.assertEqual(d["hints_used"], 1)

    def test_conteudo_sem_item_guiado_recusa_em_vez_de_inventar(self):
        with self.assertRaises(SemItemGuiado):
            self._rodar(lambda svc: svc.abrir(
                ALUNO, "CONTEUDO-SEM-NADA", None, requester=_req()))

    # -- A, B, C: os tres desfechos ----------------------------------------

    def test_A_acertou_sem_dica_e_registrado_como_sozinho(self):
        self._abrir()
        r = self._responder(self.certa)
        self.assertTrue(r["correct"])
        self.assertTrue(r["completed"])
        self.assertTrue(r["solved_unaided"])
        self.assertEqual(r["hints_used"], 0)

    def test_B_errou_pediu_ajuda_e_acertou_NAO_e_sozinho(self):
        self._abrir()
        self._responder(self.errada)
        self._pedir_ajuda()
        r = self._responder(self.certa)
        self.assertTrue(r["correct"])
        self.assertTrue(r["completed"])
        self.assertFalse(r["solved_unaided"],
                         "acertar depois de ajuda foi contado como sozinho")
        self.assertEqual(r["attempts"], 2)
        self.assertEqual(r["max_hint_level"], 1)

    def test_C_so_acertou_com_ajuda_maxima(self):
        self._abrir()
        total = len(self.item["ajudas"])
        for _ in range(total):
            self._responder(self.errada)
            self._pedir_ajuda()
        r = self._responder(self.certa)
        self.assertTrue(r["completed"])
        self.assertFalse(r["solved_unaided"])
        self.assertEqual(r["max_hint_level"], total)
        self.assertEqual(r["hints_used"], total)

    # -- D, E: pedir ajuda sem tentar --------------------------------------

    def test_D_pedir_ajuda_antes_de_tentar_e_permitido_e_contado(self):
        """Travar o pedido obrigaria a errar de propósito para receber ajuda —
        o aluno aprenderia a chutar, não o conteúdo."""
        self._abrir()
        a = self._pedir_ajuda()
        self.assertEqual(a["ajudas"][-1]["nivel"], 1)
        self.assertEqual(a["attempts"], 0)
        self.assertEqual(a["hints_used"], 1)

    def test_E_pedir_todas_as_ajudas_sem_tentar_nao_conclui_nada(self):
        self._abrir()
        for _ in range(len(self.item["ajudas"]) + 3):
            self._pedir_ajuda()
        e = self._estado()
        self.assertFalse(e["completed"], "concluiu sem responder nada")
        self.assertEqual(e["hints_used"], len(self.item["ajudas"]),
                         "contou ajuda que nao existe")
        self.assertFalse(e["solved_unaided"])

    def test_a_ajuda_chega_um_nivel_por_vez(self):
        """Entregar os quatro níveis de uma vez é entregar a resolução."""
        self._abrir()
        for esperado in range(1, len(self.item["ajudas"]) + 1):
            a = self._pedir_ajuda()
            with self.subTest(nivel=esperado):
                self.assertEqual(len(a["ajudas"]), esperado)
                self.assertEqual(a["ajudas"][-1]["nivel"], esperado)

    # -- F, G: retomada ----------------------------------------------------

    def test_FG_voltar_recupera_tentativas_e_ajudas(self):
        self._abrir()
        self._responder(self.errada)
        self._pedir_ajuda()
        e = self._estado()
        self.assertEqual(e["attempts"], 1)
        self.assertEqual(e["hints_used"], 1)
        self.assertEqual([a["nivel"] for a in e["ajudas"]], [1])

    def test_FG_voltar_nao_conclui_nem_promove(self):
        self._abrir()
        self._responder(self.errada)
        e = self._estado()
        self.assertFalse(e["completed"])
        self.assertFalse(e["solved_unaided"])

    def test_FG_estado_de_quem_nunca_abriu_e_vazio_sem_estourar(self):
        e = self._estado()
        self.assertEqual(e["attempts"], 0)
        self.assertFalse(e["completed"])

    # -- H: tentar pular etapas --------------------------------------------

    def test_H_responder_sem_abrir_funciona_e_registra(self):
        """Não é trapaça — é um refresh perdendo o passo de abrir. O que não
        pode é isso virar um registro fantasma sem contadores."""
        r = self._responder(self.certa)
        self.assertTrue(r["correct"])
        self.assertEqual(r["attempts"], 1)

    def test_H_depois_de_concluir_novas_respostas_nao_desfazem_o_registro(self):
        self._abrir()
        self._pedir_ajuda()
        self._responder(self.certa)
        depois = self._responder(self.certa)
        self.assertTrue(depois["completed"])
        self.assertFalse(depois["solved_unaided"],
                         "responder de novo apagou que houve ajuda")

    def test_H_escolha_invalida_nao_conclui_nem_quebra(self):
        self._abrir()
        r = self._responder("Z")
        self.assertFalse(r["correct"])
        self.assertFalse(r["completed"])

    def test_H_escolha_vazia_conta_como_tentativa_errada(self):
        self._abrir()
        r = self._responder("")
        self.assertFalse(r["correct"])

    # -- I: o gabarito so aparece na hora ----------------------------------

    def test_I_errar_nao_revela_a_correta(self):
        self._abrir()
        r = self._responder(self.errada)
        self.assertNotIn("correct_option", r)
        self.assertNotIn("correta", r)

    def test_I_pedir_ajuda_nao_revela_a_correta(self):
        self._abrir()
        for _ in range(len(self.item["ajudas"])):
            a = self._pedir_ajuda()
            with self.subTest(hints=a["hints_used"]):
                self.assertNotIn("correct_option", a)

    def test_I_so_depois_de_acertar_a_correta_pode_aparecer(self):
        self._abrir()
        r = self._responder(self.certa)
        self.assertEqual(r.get("correct_option"), self.certa)

    # -- J: concluir nao e dominio -----------------------------------------

    def test_J_concluir_a_pratica_guiada_nao_escreve_dominio(self):
        """A asserção central. Nenhuma linha de domínio nasce daqui."""
        from sqlalchemy import select

        from agente_ia_edu.db.models.assessments import DomainContentMastery

        self._abrir()
        for _ in range(len(self.item["ajudas"])):
            self._responder(self.errada)
            self._pedir_ajuda()
        self._responder(self.certa)

        async def contar():
            async with self.factory() as s:
                linhas = (await s.execute(select(DomainContentMastery))).all()
                return len(linhas)

        self.assertEqual(self.loop.run_until_complete(contar()), 0,
                         "a pratica guiada escreveu dominio")

    def test_J_o_registro_distingue_assistido_de_autonomo(self):
        """Quem lê depois precisa conseguir separar as duas coisas."""
        self._abrir()
        self._pedir_ajuda()
        self._responder(self.certa)
        e = self._estado()
        self.assertTrue(e["completed"])
        self.assertFalse(e["solved_unaided"])
        self.assertGreater(e["hints_used"], 0)

    # -- isolamento --------------------------------------------------------

    def test_a_pratica_de_um_aluno_nao_aparece_para_outro(self):
        self._abrir()
        self._pedir_ajuda()
        outro = self._rodar(lambda svc: svc.estado(
            "outro_aluno", self.item["key"], requester=_req("outro_aluno")))
        self.assertEqual(outro["hints_used"], 0)

    def test_ninguem_le_a_pratica_de_outro_aluno(self):
        self._abrir()
        with self.assertRaises(PermissionError):
            self._rodar(lambda svc: svc.estado(
                ALUNO, self.item["key"], requester=_req("bisbilhoteiro")))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
