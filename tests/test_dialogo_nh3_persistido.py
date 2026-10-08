"""A JORNADA NH₃ PELO SERVIÇO — os quatro caminhos do §5, com banco.

`test_caso_nh3` prova que a cadeia existe no domínio. Este prova que ela
sobrevive à persistência: o que fica gravado, o que é derivado, e o que
NUNCA é escrito.

A INVARIANTE QUE ESTE ARQUIVO EXISTE PARA GUARDAR
==================================================
Toda a conversa acontece em `guided_practice_items` — a tabela da interação
assistida, que o mapa de domínio não lê. Responder a abertura, errar, ser
investigado, acertar com ajuda: nada disso escreve em nenhuma outra tabela.
Há teste varrendo o `metadata` inteiro depois da jornada completa.
"""

from __future__ import annotations

import asyncio
import unittest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models.guided_practice import GuidedPracticeItem
from agente_ia_edu.services.grafo_estequiometria import (
    CONTEUDO,
    LEITURA_FORMULA,
    MASSA_MOLAR,
)
from agente_ia_edu.services.hipotese_pedagogica import APOIADA, ENFRAQUECIDA
from agente_ia_edu.services.investigacao_do_erro import investigacao_para
from agente_ia_edu.services.resposta_do_aluno import (
    OBS_AMBIGUA,
    OBS_CORRETA,
    OBS_INCORRETA,
    OBS_NAO_SEI,
)
from agente_ia_edu.services.servico_de_investigacao import InvestigacaoService

ALUNO = "aluno_qa_dialogo"
INV = investigacao_para(CONTEUDO, MASSA_MOLAR)


class _Requester:
    external_user_id = ALUNO
    is_platform_admin = False
    school_id = None


class _Base(unittest.TestCase):

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                          poolclass=StaticPool)
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession,
                                          expire_on_commit=True)

        async def prep():
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        self.loop.run_until_complete(prep())
        self.req = _Requester()

    def tearDown(self):
        self.loop.run_until_complete(self.engine.dispose())
        self.loop.close()

    def _run(self, corrotina_de):
        async def run():
            async with self.factory() as s:
                return await corrotina_de(InvestigacaoService(s))
        return self.loop.run_until_complete(run())

    def abrir(self):
        return self._run(lambda sv: sv.abrir(ALUNO, CONTEUDO, MASSA_MOLAR,
                                             requester=self.req))

    def responder_abertura(self, texto):
        return self._run(lambda sv: sv.responder_abertura(
            ALUNO, INV.key, texto, requester=self.req))

    def responder(self, ordem, texto):
        return self._run(lambda sv: sv.responder(
            ALUNO, INV.key, ordem, texto, requester=self.req))


class AABERTURAVEMPRIMEIRO(_Base):

    def test_a_investigacao_abre_na_pergunta_e_nao_na_etapa(self):
        v = self.abrir()
        self.assertTrue(v["perguntar_abertura"])
        self.assertIsNone(v["etapa"])

    def test_a_pergunta_chega_a_tela(self):
        self.assertIn("NH₃", self.abrir()["abertura"]["pergunta"])

    def test_a_resposta_certa_NAO_viaja_na_abertura(self):
        import json
        bruto = json.dumps(self.abrir(), ensure_ascii=False)
        self.assertNotIn('"resposta"', bruto)
        self.assertNotIn("17", bruto)

    def test_so_depois_de_responder_a_etapa_aparece(self):
        self.responder_abertura("15")
        v = self.abrir()
        self.assertFalse(v["perguntar_abertura"])
        self.assertIsNotNone(v["etapa"])


class QUINZEABREHIPOTESE(_Base):

    def test_quinze_e_observado_como_incorreto(self):
        self.assertEqual(OBS_INCORRETA, self.responder_abertura("15")["observacao"])

    def test_quinze_produz_a_frase_hedgeada(self):
        a = self.responder_abertura("15")["abertura"]
        self.assertEqual("INDEX_OMISSION", a["hipotese_codigo"])
        self.assertIn("pode indicar", a["hipotese"].lower())

    def test_a_tela_recebe_o_que_ELE_escreveu(self):
        self.assertEqual("15", self.responder_abertura("15")["abertura"]["resposta_do_aluno"])

    def test_quinze_com_unidade_tambem_dispara(self):
        a = self.responder_abertura("15 g/mol")["abertura"]
        self.assertEqual("INDEX_OMISSION", a["hipotese_codigo"])

    def test_numero_errado_sem_gatilho_nao_inventa_hipotese(self):
        a = self.responder_abertura("99")["abertura"]
        self.assertIsNone(a["hipotese_codigo"])
        self.assertIsNone(a["hipotese"])

    def test_a_primeira_etapa_servida_e_a_discriminante(self):
        v = self.responder_abertura("15")
        self.assertEqual(1, v["etapa"]["ordem"])
        self.assertEqual(LEITURA_FORMULA, v["etapa"]["skill"])


class CAMINHOA_TRES(_Base):
    """Ele conta os três: a hipótese enfraquece e a investigação segue."""

    def test_responder_tres_por_escrito_acerta(self):
        self.responder_abertura("15")
        r = self.responder(1, "3")
        self.assertTrue(r["correct"])
        self.assertEqual(OBS_CORRETA, r["observacao"])

    def test_e_a_hipotese_ENFRAQUECE(self):
        self.responder_abertura("15")
        r = self.responder(1, "3")
        self.assertEqual(ENFRAQUECIDA, r["hipotese_estado"])

    def test_tres_por_extenso_tambem(self):
        self.responder_abertura("15")
        self.assertTrue(self.responder(1, "três")["correct"])

    def test_a_investigacao_avanca_para_a_aplicacao_do_indice(self):
        self.responder_abertura("15")
        r = self.responder(1, "3")
        self.assertEqual(2, r["etapa"]["ordem"])
        self.assertEqual(MASSA_MOLAR, r["etapa"]["skill"])

    def test_a_cadeia_inteira_por_escrito(self):
        self.responder_abertura("15")
        self.responder(1, "3")
        self.responder(2, "3 g/mol")
        r = self.responder(3, "17")
        self.assertTrue(r["completed"])


class CAMINHOB_UM(_Base):
    """Ele lê um hidrogênio: a hipótese é apoiada e aponta o pré-requisito."""

    def test_responder_um_erra(self):
        self.responder_abertura("15")
        r = self.responder(1, "1")
        self.assertFalse(r["correct"])
        self.assertEqual(OBS_INCORRETA, r["observacao"])

    def test_e_a_hipotese_e_APOIADA(self):
        self.responder_abertura("15")
        r = self.responder(1, "1")
        self.assertEqual(APOIADA, r["hipotese_estado"])

    def test_a_suspeita_aponta_para_a_leitura_da_formula(self):
        self.responder_abertura("15")
        r = self.responder(1, "1")
        self.assertEqual(LEITURA_FORMULA, r["hipotese_suspeita"])

    def test_o_gargalo_localizado_e_o_pre_requisito(self):
        self.responder_abertura("15")
        self.assertEqual(LEITURA_FORMULA,
                         self.responder(1, "1")["bottleneck_skill"])

    def test_errar_nao_avanca_a_etapa(self):
        self.responder_abertura("15")
        self.assertEqual(1, self.responder(1, "1")["etapa"]["ordem"])


class CAMINHOC_NAO_SEI(_Base):

    def test_nao_sei_na_abertura_tem_observacao_propria(self):
        self.assertEqual(OBS_NAO_SEI,
                         self.responder_abertura("não sei")["observacao"])

    def test_e_nao_produz_hipotese(self):
        a = self.responder_abertura("não sei")["abertura"]
        self.assertIsNone(a["hipotese_codigo"])

    def test_a_investigacao_continua_mesmo_assim(self):
        """Não saber é motivo para investigar, não para parar."""
        v = self.responder_abertura("não sei")
        self.assertIsNotNone(v["etapa"])
        self.assertEqual(1, v["etapa"]["ordem"])

    def test_nao_sei_numa_etapa_NAO_gasta_tentativa(self):
        """Ele não errou: o sistema não leu uma tentativa dele.

        Este teste afirmava a AUSÊNCIA DA LINHA. Era um proxy, e deixou de
        valer na migration 068: o pedido de ajuda passou a ser contado, e
        contá-lo exige uma linha. O que importava sempre foi a tentativa, e
        agora é isso que está escrito — com a vantagem de que a asserção
        falha se alguém transformar o pedido em erro, o que a ausência da
        linha não detectava.
        """
        self.responder_abertura("15")
        self.responder(1, "não sei")

        async def ler():
            async with self.factory() as s:
                return (await s.execute(select(GuidedPracticeItem).where(
                    GuidedPracticeItem.item_key == f"{INV.key}#1"))
                ).scalar_one_or_none()
        linha = self.loop.run_until_complete(ler())
        self.assertIsNotNone(linha)
        self.assertEqual(0, linha.attempts)
        self.assertFalse(linha.completed)
        self.assertFalse(linha.solved_unaided)
        self.assertEqual(1, linha.help_requests)

    def test_e_a_observacao_e_de_nao_sei(self):
        self.responder_abertura("15")
        self.assertEqual(OBS_NAO_SEI, self.responder(1, "não sei")["observacao"])


class AAMBIGUIDADENAOGASTATENTATIVA(_Base):

    def test_resposta_ambigua_nao_vira_erro_registrado(self):
        self.responder_abertura("15")
        r = self.responder(1, "3 ou 4")
        self.assertEqual(OBS_AMBIGUA, r["observacao"])
        self.assertFalse(r["correct"])

    def test_e_a_etapa_continua_sem_linha(self):
        self.responder_abertura("15")
        self.responder(1, "3 ou 4")

        async def contar():
            async with self.factory() as s:
                return len((await s.execute(select(GuidedPracticeItem).where(
                    GuidedPracticeItem.item_key == f"{INV.key}#1"))
                ).scalars().all())
        self.assertEqual(0, self.loop.run_until_complete(contar()))


class CAMINHOD_DEZESSETE(_Base):

    def test_dezessete_na_abertura_e_correto(self):
        self.assertEqual(OBS_CORRETA, self.responder_abertura("17")["observacao"])

    def test_e_NAO_produz_hipotese_de_erro(self):
        a = self.responder_abertura("17")["abertura"]
        self.assertIsNone(a["hipotese_codigo"])

    def test_acertar_a_abertura_NAO_conclui_a_investigacao(self):
        """§5 caminho D: resposta correta não é, sozinha, domínio — e a
        cadeia continua disponível para confirmar."""
        v = self.responder_abertura("17")
        self.assertFalse(v["completed"])
        self.assertIsNotNone(v["etapa"])


class ACONVERSANAOESCREVEDOMINIO(_Base):
    """A invariante central: nada disso é evidência de mastery."""

    def _jornada_completa(self):
        self.responder_abertura("15")
        self.responder(1, "3")
        self.responder(2, "3 g/mol")
        self.responder(3, "17")

    def test_so_guided_practice_items_recebe_linha(self):
        self._jornada_completa()

        async def tabelas_com_linha():
            async with self.factory() as s:
                cheias = set()
                for tabela in Base.metadata.sorted_tables:
                    if (await s.execute(select(tabela).limit(1))).first():
                        cheias.add(tabela.name)
                return cheias

        self.assertEqual({"guided_practice_items"},
                         self.loop.run_until_complete(tabelas_com_linha()))

    def test_a_jornada_nao_devolve_nada_parecido_com_nota(self):
        self._jornada_completa()
        v = self.abrir()
        for proibido in ("mastery", "dominio", "score", "nota"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, {k.lower() for k in v})

    def test_a_abertura_fica_registrada_na_ordem_zero(self):
        self.responder_abertura("15")

        async def ler():
            async with self.factory() as s:
                return (await s.execute(select(GuidedPracticeItem).where(
                    GuidedPracticeItem.item_key == f"{INV.key}#0"))
                ).scalar_one_or_none()
        linha = self.loop.run_until_complete(ler())
        self.assertIsNotNone(linha)
        self.assertEqual(MASSA_MOLAR, linha.skill)


class IDEMPOTENCIA(_Base):
    """§30: duplo clique, refresh, mesma resposta duas vezes."""

    def test_responder_a_abertura_duas_vezes_nao_duplica_linha(self):
        self.responder_abertura("15")
        self.responder_abertura("15")

        async def contar():
            async with self.factory() as s:
                return len((await s.execute(select(GuidedPracticeItem).where(
                    GuidedPracticeItem.item_key == f"{INV.key}#0"))
                ).scalars().all())
        self.assertEqual(1, self.loop.run_until_complete(contar()))

    def test_a_mesma_resposta_certa_duas_vezes_nao_avanca_duas_etapas(self):
        self.responder_abertura("15")
        self.responder(1, "3")
        r = self.responder(1, "3")
        self.assertEqual(2, r["etapa"]["ordem"])

    def test_reabrir_devolve_o_mesmo_estado(self):
        self.responder_abertura("15")
        self.responder(1, "3")
        a, b = self.abrir(), self.abrir()
        self.assertEqual(a["etapa"], b["etapa"])
        self.assertEqual(a["concluidas"], b["concluidas"])


if __name__ == "__main__":
    unittest.main()
