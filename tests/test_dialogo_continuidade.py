"""A CONVERSA SOBREVIVE — ao recarregar, a sair e a voltar.

O QUE ESTE ARQUIVO EXISTE PARA GUARDAR
=======================================
Até 2026-10-07 o diálogo estava inteiro dentro do turno: o "15" e a frase da
hipótese viajavam na resposta do POST e morriam ali. Recarregar a página
devolvia um Edu que lembrava QUE o aluno respondeu e se acertou, mas não o
QUE ele disse — e a conversa recomeçava do meio, sem a fala que a motivou.

Cada teste aqui abre uma SESSÃO NOVA entre escrever e ler (é o que `_run`
faz). Não é detalhe de fixture: é exatamente o recarregar da página, e um
teste que reusasse a sessão passaria com o produto quebrado.

O QUE É GRAVADO E O QUE É REDERIVADO
=====================================
Gravado: o texto que o aluno escreveu, e o pedido de ajuda. Nada mais.

Rederivado a cada leitura, pelas mesmas funções puras do turno: a resposta
normalizada, a observação pedagógica, a hipótese e o estado dela. Guardar
qualquer um desses seria uma segunda fonte de verdade para algo recalculável
— e as duas divergiriam no primeiro ajuste do conteúdo curado.
"""

from __future__ import annotations

import asyncio
import unittest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models.guided_practice import GuidedPracticeItem
from agente_ia_edu.services.grafo_estequiometria import CONTEUDO, MASSA_MOLAR
from agente_ia_edu.services.investigacao_do_erro import (
    ORDEM_DA_ABERTURA,
    investigacao_para,
)
from agente_ia_edu.services.resposta_do_aluno import (
    OBS_CORRETA,
    OBS_INCORRETA,
    OBS_NAO_SEI,
)
from agente_ia_edu.services.servico_de_investigacao import InvestigacaoService

ALUNO = "aluno_qa_continuidade"
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
        # `expire_on_commit=True` como a aplicação: com False, ler um
        # atributo depois do commit funciona no teste e levanta
        # MissingGreenlet no navegador.
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
        """UMA SESSÃO NOVA por chamada — é o recarregar da página."""
        async def run():
            async with self.factory() as s:
                return await corrotina_de(InvestigacaoService(s))
        return self.loop.run_until_complete(run())

    # -- os movimentos do aluno -------------------------------------------

    def abrir(self):
        return self._run(lambda sv: sv.abrir(ALUNO, CONTEUDO, MASSA_MOLAR,
                                             requester=self.req))

    def responder_abertura(self, texto):
        return self._run(lambda sv: sv.responder_abertura(
            ALUNO, INV.key, texto, requester=self.req))

    def responder(self, ordem, texto):
        return self._run(lambda sv: sv.responder(
            ALUNO, INV.key, ordem, texto, requester=self.req))

    def linhas(self):
        async def ler():
            async with self.factory() as s:
                return (await s.execute(
                    select(GuidedPracticeItem).where(
                        GuidedPracticeItem.student_external_id == ALUNO)
                )).scalars().all()
        return self.loop.run_until_complete(ler())

    def linha_da(self, ordem):
        chave = f"{INV.key}#{ordem}"
        return next((x for x in self.linhas() if x.item_key == chave), None)


class OQUEELEESCREVEUSOBREVIVE(_Base):
    """P0-A: a fala do aluno é o único fato não rederivável da conversa."""

    def test_o_quinze_volta_depois_de_recarregar(self):
        self.responder_abertura("15")
        self.assertEqual("15", self.abrir()["abertura"]["resposta_do_aluno"])

    def test_com_unidade_volta_COMO_ELE_ESCREVEU(self):
        """A nossa leitura ("15.0") não substitui o que ele disse."""
        self.responder_abertura("15 g/mol")
        self.assertEqual("15 g/mol",
                         self.abrir()["abertura"]["resposta_do_aluno"])

    def test_a_resposta_por_extenso_volta_por_extenso(self):
        self.responder_abertura("quinze")
        self.assertEqual("quinze",
                         self.abrir()["abertura"]["resposta_do_aluno"])


class AOBSERVACAOEREDERIVADA(_Base):
    """Não gravada: recalculada do texto, pela mesma função do turno."""

    def test_o_erro_continua_sendo_erro_depois_de_recarregar(self):
        self.responder_abertura("15")
        self.assertEqual(OBS_INCORRETA,
                         self.abrir()["abertura"]["observacao"])

    def test_e_o_acerto_continua_acerto(self):
        self.responder_abertura("17")
        self.assertEqual(OBS_CORRETA, self.abrir()["abertura"]["observacao"])


class AHIPOTESEEREDERIVADA(_Base):
    """A suposição renasce do valor observado — não é guardada."""

    def test_a_frase_hedgeada_volta_depois_de_recarregar(self):
        self.responder_abertura("15")
        a = self.abrir()["abertura"]
        self.assertEqual("INDEX_OMISSION", a["hipotese_codigo"])
        self.assertIn("pode indicar", (a["hipotese"] or "").lower())

    def test_numero_sem_gatilho_continua_sem_hipotese(self):
        self.responder_abertura("99")
        a = self.abrir()["abertura"]
        self.assertIsNone(a["hipotese_codigo"])
        self.assertIsNone(a["hipotese"])

    def test_o_acerto_nao_inventa_hipotese_ao_recarregar(self):
        self.responder_abertura("17")
        self.assertIsNone(self.abrir()["abertura"]["hipotese_codigo"])


class ASRESPOSTASDASETAPASSOBREVIVEM(_Base):
    """Não só a abertura: cada micropergunta respondida guarda a fala."""

    def test_o_tres_da_discriminante_volta(self):
        """ELE escreveu "três" — e não a grafia da alternativa certa.

        Escrito assim de propósito: respondendo "3", a fala do aluno e o
        texto do gabarito coincidem, e o teste passaria lendo o gabarito.
        """
        self.responder_abertura("15")
        self.responder(1, "três")
        concluidas = self.abrir()["concluidas"]
        primeira = next(c for c in concluidas if c["ordem"] == 1)
        self.assertEqual("três", primeira["resposta_texto"])

    def test_a_cadeia_inteira_volta_com_as_falas(self):
        """Nenhuma das três coincide com a grafia do gabarito."""
        self.responder_abertura("15")
        self.responder(1, "três")
        self.responder(2, "3")
        self.responder(3, "17")
        ditas = {c["ordem"]: c["resposta_texto"]
                 for c in self.abrir()["concluidas"]}
        self.assertEqual({1: "três", 2: "3", 3: "17"}, ditas)

    def test_a_tentativa_ERRADA_tambem_volta(self):
        """Quem errou precisa ver o que escreveu — é o fio da conversa."""
        self.responder_abertura("15")
        self.responder(1, "1")
        self.assertEqual("1", self.abrir()["etapa"]["resposta_do_aluno"])


class OPEDIDODEAJUDAFICAGRAVADO(_Base):
    """"Não sei" não é erro — e também não pode ser esquecido."""

    def test_nao_sei_nao_gasta_tentativa(self):
        self.responder_abertura("15")
        self.responder(1, "não sei")
        linha = self.linha_da(1)
        self.assertIsNotNone(linha, "o pedido de ajuda não deixou registro")
        self.assertEqual(0, linha.attempts)

    def test_mas_fica_contado_como_ajuda(self):
        self.responder_abertura("15")
        self.responder(1, "não sei")
        self.assertEqual(1, self.linha_da(1).help_requests)

    def test_dois_pedidos_contam_dois(self):
        self.responder_abertura("15")
        self.responder(1, "não sei")
        self.responder(1, "me ajuda")
        self.assertEqual(2, self.linha_da(1).help_requests)

    def test_quem_pediu_ajuda_NAO_resolve_sozinho(self):
        """A invariante que o §2 protege: acertar com ajuda != sem ajuda."""
        self.responder_abertura("15")
        self.responder(1, "não sei")
        self.responder(1, "3")
        linha = self.linha_da(1)
        self.assertTrue(linha.completed)
        self.assertFalse(linha.solved_unaided,
                         "pediu ajuda e ainda assim contou como autônomo")

    def test_quem_NAO_pediu_ajuda_resolve_sozinho(self):
        self.responder_abertura("15")
        self.responder(1, "3")
        self.assertTrue(self.linha_da(1).solved_unaided)

    def test_a_etapa_resolvida_NAO_exibe_nao_sei_como_a_resposta(self):
        """O texto do pedido é sobrescrito pela tentativa que resolveu.

        Sem isto, uma etapa acertada depois de um "não sei" apareceria no
        fio como `Você: não sei` seguido de "Isso." — o Edu concordando com
        quem disse que não sabia.
        """
        self.responder_abertura("15")
        self.responder(1, "não sei")
        self.responder(1, "três")
        concluida = next(c for c in self.abrir()["concluidas"]
                         if c["ordem"] == 1)
        self.assertEqual("três", concluida["resposta_texto"])


class AABERTURANAOFECHASEMRESPOSTA(_Base):
    """Fail-closed na pergunta que abre a conversa.

    A abertura é a porta: se ela fechar com uma resposta que o Edu não leu,
    o aluno passa para a discriminante sem ter dito nada — e a cadeia inteira
    investiga um erro que ninguém observou.
    """

    def test_resposta_ambigua_NAO_fecha_a_abertura(self):
        self.responder_abertura("15 ou 17")
        self.assertTrue(self.abrir()["perguntar_abertura"],
                        "a abertura fechou com uma resposta ilegível")

    def test_resposta_vazia_NAO_fecha_a_abertura(self):
        self.responder_abertura("")
        self.assertTrue(self.abrir()["perguntar_abertura"])

    def test_e_nao_deixa_linha_gravada(self):
        self.responder_abertura("15 ou 17")
        self.assertIsNone(self.linha_da(ORDEM_DA_ABERTURA))

    def test_depois_de_escrever_de_novo_a_abertura_fecha(self):
        self.responder_abertura("15 ou 17")
        self.responder_abertura("15")
        v = self.abrir()
        self.assertFalse(v["perguntar_abertura"])
        self.assertEqual("15", v["abertura"]["resposta_do_aluno"])

    def test_mas_nao_sei_FECHA_a_abertura(self):
        """"Não sei" não é uma leitura que falhou — é uma informação.

        Escrevi este teste ao contrário primeiro, e o teste antigo de
        `CAMINHOC_NAO_SEI` me corrigiu: não saber a massa molar é
        exatamente o motivo pelo qual a investigação existe, e devolver a
        mesma pergunta a quem acabou de dizer que não sabe seria insistir
        no que já foi respondido.
        """
        self.responder_abertura("não sei")
        v = self.abrir()
        self.assertFalse(v["perguntar_abertura"])
        self.assertEqual(1, v["etapa"]["ordem"])

    def test_e_o_pedido_de_ajuda_da_abertura_fica_contado(self):
        self.responder_abertura("não sei")
        linha = self.linha_da(ORDEM_DA_ABERTURA)
        self.assertEqual(1, linha.help_requests)
        self.assertEqual(0, linha.attempts)

    def test_o_nao_sei_dele_SOBREVIVE_ao_recarregar(self):
        """A regra: o Edu grava o que LEU.

        "Não sei" é lido — tem observação própria, muda o rumo da conversa e
        aparece no fio como fala dele. Então sobrevive. Ambiguidade e
        ausência não são lidas, e por isso não sobrevivem: ver
        `AAMBIGUIDADENAOENTRANOFIO`.

        Escrevi o contrário primeiro, e o navegador me corrigiu: o fio
        mostrava "Você: não sei" dentro do turno e o perdia no F5, porque a
        frase vinha da memória da tela e não do backend.
        """
        self.responder_abertura("não sei")
        self.assertEqual("não sei",
                         self.abrir()["abertura"]["resposta_do_aluno"])

    def test_e_continua_sendo_observado_como_nao_sei(self):
        self.responder_abertura("não sei")
        self.assertEqual(OBS_NAO_SEI, self.abrir()["abertura"]["observacao"])

    def test_sem_virar_hipotese(self):
        """Ele não mostrou raciocínio — não há o que supor."""
        self.responder_abertura("não sei")
        self.assertIsNone(self.abrir()["abertura"]["hipotese_codigo"])

    def test_e_sem_virar_tentativa(self):
        self.responder_abertura("não sei")
        self.assertEqual(0, self.linha_da(ORDEM_DA_ABERTURA).attempts)


class AAMBIGUIDADENAOENTRANOFIO(_Base):
    """O que o Edu não leu não é fala da conversa."""

    def test_resposta_ambigua_nao_vira_resposta_gravada(self):
        self.responder_abertura("15")
        self.responder(1, "3 ou 4")
        linha = self.linha_da(1)
        if linha is not None:
            self.assertIsNone(linha.response_text)

    def test_e_nao_gasta_tentativa(self):
        self.responder_abertura("15")
        self.responder(1, "3 ou 4")
        linha = self.linha_da(1)
        self.assertEqual(0, 0 if linha is None else linha.attempts)


class ACONVERSANAOESCREVEDOMINIO(_Base):
    """A continuidade não pode ter aberto uma porta nova para evidência."""

    def test_a_jornada_inteira_toca_UMA_tabela_so(self):
        self.responder_abertura("15")
        self.responder(1, "não sei")
        self.responder(1, "3")
        self.responder(2, "3 g/mol")
        self.responder(3, "17")

        async def varrer():
            async with self.factory() as s:
                tocadas = []
                for tabela in Base.metadata.sorted_tables:
                    n = (await s.execute(
                        select(tabela).limit(1))).first()
                    if n is not None:
                        tocadas.append(tabela.name)
                return tocadas

        self.assertEqual(["guided_practice_items"],
                         self.loop.run_until_complete(varrer()))

    def test_a_abertura_mora_na_ordem_zero(self):
        self.responder_abertura("15")
        self.assertIsNotNone(self.linha_da(ORDEM_DA_ABERTURA))


if __name__ == "__main__":
    unittest.main()
