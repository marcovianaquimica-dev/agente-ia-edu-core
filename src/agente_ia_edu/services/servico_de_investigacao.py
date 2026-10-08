"""A INVESTIGACAO, PERSISTIDA - sem tabela nova, e sem encostar no dominio.

ONDE ELA MORA, E POR QUE ALI
=============================
Em `guided_practice_items`, UMA LINHA POR ETAPA. A tabela ja existe, ja tem a
semantica certa - interacao assistida - e, o que mais importa, o mapa de
dominio ja nao a le, por projeto da PHASE 22. Pondo a investigacao ali, a
garantia "conseguir com ajuda != dominar sozinho" e herdada em vez de
reconstruida; uma tabela nova precisaria provar a mesma coisa de novo.

Uma linha por ETAPA, e nao uma por investigacao, porque o estado que precisa
sobreviver e "qual etapa ainda nao esta de pe". Com uma linha so, errar a
etapa 2 e nao ter chegado nela ficariam indistinguiveis.

E ha um ganho de dado: cada linha grava a MICRO-HABILIDADE que aquela etapa
isola. Antes, o sistema sabia "errou uma questao de estequiometria"; agora
sabe "a conversao massa-mol esta de pe, a proporcao nao" - e isso veio de uma
pergunta que isola uma etapa, nao de uma questao que mistura quatro.

O QUE ELE NAO FAZ
==================
Nao escreve em nenhuma outra tabela, e ha teste varrendo o `metadata` inteiro
depois de uma investigacao completa para confirmar isso. Nao consulta modelo
de IA. Nao devolve nada que signifique dominio: `completed` responde "o apoio
pode diminuir", nunca "ele aprendeu".

ERRAR NAO AVANCA
=================
Quem errou a etapa 1 precisa da etapa 1. Perguntar a etapa 2 a quem acabou de
mostrar que a 1 nao esta de pe produz uma resposta que nao significa nada - e
o sistema concluiria a coisa errada a partir dela.

A LIMITACAO CONHECIDA
======================
A linha nao guarda QUAL distrator o aluno marcou, porque a tabela nao tem
coluna para isso e este bloco preferiu zero migration. Hoje nao faz
diferenca: a hipotese e curada por micro-habilidade, nao montada em tempo de
execucao a partir da letra. Se um dia a hipotese passar a depender do
distrator, ai havera necessidade arquitetural de uma coluna - e nao antes.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.db.models.guided_practice import GuidedPracticeItem
from agente_ia_edu.services.hipotese_pedagogica import (
    ABERTA,
    atualizar as atualizar_hipotese,
)
from agente_ia_edu.services.investigacao_do_erro import (
    ORDEM_DA_ABERTURA,
    Investigacao,
    avaliar_resposta,
    hipotese_para,
    investigacao_para,
    para_o_aluno,
    proxima_etapa,
)
from agente_ia_edu.services.resposta_do_aluno import (
    OBS_AMBIGUA,
    OBS_CORRETA,
    OBS_NAO_SEI,
    OBS_SEM_RESPOSTA,
    normalizar,
    observar,
)

# AS TRES OBSERVACOES QUE NAO SAO RESPOSTA.
#
# Ambiguidade e ausencia porque o Edu nao leu; "nao sei" porque o aluno
# disse honestamente que nao sabe. Nenhuma das tres gasta tentativa, e
# nenhuma das tres fecha uma pergunta. Numa constante, e nao repetidas em
# cada caminho de escrita, para que mudar a politica seja um lugar so.
_NAO_E_RESPOSTA = (OBS_AMBIGUA, OBS_SEM_RESPOSTA, OBS_NAO_SEI)
from agente_ia_edu.services.question_list_store import Requester

# Marcador de "tentou e nao acertou" para montar a visao do aluno.
#
# Nao e a letra que ele marcou - ela nao e guardada (ver o cabecalho). O que
# a visao precisa saber e apenas que a etapa foi tentada e nao esta de pe, e
# qualquer valor diferente da correta expressa isso. Nao e uma letra valida de
# proposito: se um dia escapar para a tela, nao se confunde com alternativa.
_TENTOU_E_ERROU = "-"


class SemInvestigacao(LookupError):
    """404 - nao ha cadeia de microperguntas para esta lacuna, ou a etapa
    pedida nao existe.

    Devolver uma cadeia generica seria pior: o aluno responderia perguntas
    cujo resultado o sistema nao saberia interpretar.
    """


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _chave(inv_key: str, ordem: int) -> str:
    return f"{inv_key}#{int(ordem)}"


# O teto da coluna `response_text`, e o mesmo que a API ja impoe na entrada.
# Cortar aqui tambem - e nao so na borda - porque o servico e chamado por
# testes e scripts que nao passam pelo Pydantic, e uma resposta de 500
# caracteres derrubaria a gravacao no Postgres em vez de ser truncada.
_TETO_DA_FALA = 400


def _cortado(texto: str | None) -> str | None:
    if texto is None:
        return None
    limpo = texto.strip()
    return limpo[:_TETO_DA_FALA] or None


class InvestigacaoService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- autorizacao -------------------------------------------------------

    @staticmethod
    def _so_o_proprio(aluno: str, requester: Requester) -> None:
        """A investigacao e da pessoa. Mesma regra da pratica guiada - nao ha
        autorizacao paralela aqui."""
        if getattr(requester, "is_platform_admin", False):
            return
        if (requester.external_user_id or "") != aluno:
            raise PermissionError("esta investigacao nao e sua")

    # -- leitura -----------------------------------------------------------

    async def _estado_gravado(
            self, aluno: str, inv: Investigacao
    ) -> tuple[dict[int, str], dict[int, int], dict[int, str]]:
        """({ordem: resposta}, {ordem: tentativas}, {ordem: fala}) do gravado.

        Etapa com linha concluida vale como respondida CORRETAMENTE - e ela so
        fica concluida tendo sido acertada, porque e `responder` quem marca.
        Etapa com linha aberta E COM TENTATIVA vale como tentada e nao
        resolvida; linha com zero tentativas e um pedido de ajuda, e nao
        pode valer como erro.

        A CONTAGEM DE TENTATIVAS VIAJA JUNTO porque e ela que escolhe o nivel
        do retorno: na primeira vez o aluno recebe a regra sem o numero, e da
        segunda em diante a regra aplicada. Sem este segundo dicionario, a
        visao cairia sempre no primeiro nivel e quem errou tres vezes leria a
        mesma pista tres vezes.

        A FALA viaja no terceiro, e e o unico dos tres que nao e derivado:
        os outros dois saem do estado da linha, este e o texto cru.
        """
        chaves = {_chave(inv.key, e.ordem): e.ordem for e in inv.etapas}
        linhas = (await self._session.execute(
            select(GuidedPracticeItem).where(
                GuidedPracticeItem.student_external_id == aluno,
                GuidedPracticeItem.item_key.in_(list(chaves)),
            ))).scalars().all()
        respostas: dict[int, str] = {}
        tentativas: dict[int, int] = {}
        falas: dict[int, str] = {}
        for linha in linhas:
            ordem = chaves[linha.item_key]
            etapa = next(e for e in inv.etapas if e.ordem == ordem)
            quantas = int(linha.attempts or 0)
            if linha.completed:
                respostas[ordem] = etapa.correta
            elif quantas > 0:
                respostas[ordem] = _TENTOU_E_ERROU
            # quantas == 0 e nao concluida: so pediu ajuda. A etapa segue
            # ABERTA e sem tentativa - nao entra em `respostas`, senao a
            # visao a trataria como erro e serviria o retorno do erro.
            tentativas[ordem] = quantas
            if linha.response_text:
                falas[ordem] = linha.response_text
        return respostas, tentativas, falas

    def _resolver(self, content_code: str, skill: str | None) -> Investigacao:
        inv = investigacao_para(content_code, skill)
        if inv is None:
            raise SemInvestigacao(f"{content_code}/{skill}")
        return inv

    async def _abertura(self, aluno: str, inv: Investigacao) -> dict:
        """O que ja se sabe sobre a pergunta de abertura - RECONSTRUIDO.

        Ela mora na mesma tabela das etapas, na ordem ZERO - ver
        `ORDEM_DA_ABERTURA`. Da linha sai UM fato: o texto que o aluno
        escreveu (`response_text`, desde a migration 068).

        TODO O RESTO E REFEITO AQUI, pelas mesmas funcoes do turno:

            texto -> normalizar -> observar -> hipotese_para

        E por isso que recarregar a pagina devolve a MESMA conversa, e nao
        uma aproximacao dela. E e por isso que nada disso e gravado: a
        observacao e a hipotese sao consequencia do texto e do conteudo
        curado, e guardar consequencia ao lado da causa cria duas verdades.

        Linha antiga, de antes da coluna existir, volta so com
        `respondida` e o acerto - e a tela mostra a conversa sem a fala, que
        e o comportamento anterior. Nao ha migracao de dado: nao existe de
        onde reconstruir um texto que nunca foi gravado.
        """
        if inv.abertura is None:
            return {}
        linha = await self._linha(aluno, _chave(inv.key, ORDEM_DA_ABERTURA))
        if linha is None:
            return {}
        pedidos = int(linha.help_requests or 0)
        if not int(linha.attempts or 0):
            # LINHA SEM TENTATIVA E PEDIDO DE AJUDA, nao resposta.
            #
            # E ela FECHA a abertura, sem fingir que houve resposta: nao
            # saber a massa molar e motivo para investigar, nao para parar
            # na porta. O aluno segue para a primeira micropergunta, e o
            # que fica registrado e o pedido - nunca um erro que nao houve.
            return {"respondida": True,
                    "observacao": OBS_NAO_SEI if pedidos else None,
                    "tentativas": 0,
                    "pedidos_de_ajuda": pedidos}
        bruto = linha.response_text
        if not bruto:
            return {
                "respondida": True,
                "observacao": (OBS_CORRETA if linha.completed else None),
                "tentativas": int(linha.attempts or 0),
            }
        normalizada = normalizar(bruto, espera=inv.abertura.espera)
        observacao = observar(normalizada,
                              esperado_numero=inv.abertura.resposta)
        hipotese = hipotese_para(inv, normalizada.numero)
        return {
            "respondida": True,
            "bruto": bruto,
            "observacao": observacao,
            "tentativas": int(linha.attempts or 0),
            "hipotese_como_dizer": hipotese.como_dizer if hipotese else None,
            "hipotese_codigo": hipotese.codigo if hipotese else None,
            # O ESTADO tambem e reconstruido, e nao reposto em ABERTA: quem
            # move a hipotese e a resposta a DISCRIMINANTE, e o desfecho
            # dela esta gravado na linha daquela etapa. Ver
            # `_estado_da_hipotese`.
            "hipotese_estado": (
                await self._estado_da_hipotese(aluno, inv, hipotese)
                if hipotese else None),
        }

    async def _estado_da_hipotese(self, aluno: str, inv: Investigacao,
                                  hipotese) -> str:
        """O estado da suposicao, refeito do desfecho da discriminante.

        Nao e gravado em lugar nenhum, e nao precisa ser: a etapa
        discriminante tem linha propria, e o que ela diz basta.

            discriminante resolvida        -> ENFRAQUECIDA (ele conta certo)
            tentada e nao resolvida        -> APOIADA
            nao tentada, ou so pedido      -> ABERTA

        A ordem importa: "ainda nao perguntei" e "perguntei e ele acertou"
        sao conclusoes diferentes, e tratar a primeira como a segunda faria
        o Edu abandonar uma hipotese que nunca foi testada.
        """
        linha = await self._linha(
            aluno, _chave(inv.key, hipotese.discriminante))
        if linha is None:
            return ABERTA
        if linha.completed:
            return atualizar_hipotese(ABERTA, discriminante_correta=True)
        if int(linha.attempts or 0) > 0:
            return atualizar_hipotese(ABERTA, discriminante_correta=False)
        return ABERTA

    async def abrir(self, aluno: str, content_code: str, skill: str | None, *,
                    requester: Requester) -> dict:
        """Comeca (ou retoma) a investigacao daquela lacuna.

        Nao cria linha nenhuma: abrir nao e tentar. A primeira linha nasce na
        primeira resposta, que e quando algo de fato aconteceu.
        """
        self._so_o_proprio(aluno, requester)
        inv = self._resolver(content_code, skill)
        respostas, tentativas, falas = await self._estado_gravado(aluno, inv)
        return para_o_aluno(inv, respostas, tentativas=tentativas,
                            abertura=await self._abertura(aluno, inv),
                            falas=falas)

    async def pendente(self, aluno: str, content_code: str, skill: str | None,
                       *, requester: Requester) -> bool:
        """A investigacao desta lacuna ainda esta de pe?

        E o que a escada de apoio consulta para saber se o degrau mais alto
        ainda e o proximo. Lacuna sem cadeia escrita nunca esta pendente - nao
        ha o que percorrer.
        """
        self._so_o_proprio(aluno, requester)
        inv = investigacao_para(content_code, skill)
        if inv is None:
            return False
        if inv.abertura is not None and not (
                await self._abertura(aluno, inv)).get("respondida"):
            # A abertura ainda nao foi feita: ha o que percorrer.
            return True
        respostas, _, _ = await self._estado_gravado(aluno, inv)
        return proxima_etapa(inv, respostas) is not None

    # -- escrita -----------------------------------------------------------

    async def _linha(self, aluno: str, chave: str) -> GuidedPracticeItem | None:
        return (await self._session.execute(
            select(GuidedPracticeItem).where(
                GuidedPracticeItem.student_external_id == aluno,
                GuidedPracticeItem.item_key == chave,
            ))).scalar_one_or_none()

    async def responder_abertura(self, aluno: str, inv_key: str, texto: str,
                                 *, requester: Requester) -> dict:
        """A resposta ESCRITA a pergunta que abre a investigacao.

        Tres coisas acontecem, nesta ordem, e nenhuma delas pula a anterior:

            texto -> RESPOSTA NORMALIZADA -> OBSERVACAO -> HIPOTESE

        A hipotese so nasce quando o VALOR tem gatilho escrito no conteudo
        curado. Numero errado sem gatilho nao produz suposicao nenhuma - o
        sistema so supoe onde alguem decidiu de antemao qual suposicao
        aquele numero sustenta.

        E a hipotese nasce ABERTA. Ela nao e conclusao: e a razao de fazer a
        proxima pergunta.
        """
        self._so_o_proprio(aluno, requester)
        inv = next((i for i in _todas() if i.key == inv_key), None)
        if inv is None or inv.abertura is None:
            raise SemInvestigacao(inv_key)

        normalizada = normalizar(texto, espera=inv.abertura.espera)
        observacao = observar(normalizada,
                              esperado_numero=inv.abertura.resposta)
        acertou = observacao == OBS_CORRETA

        # O QUE O EDU NAO LEU NAO FECHA A ABERTURA.
        #
        # A abertura e a porta da conversa: fecha-la com uma resposta
        # ilegivel levaria o aluno a discriminante sem ter dito nada, e a
        # cadeia investigaria um erro que ninguem observou. Fail-closed, e
        # visivel: a pergunta volta, e o Edu diz por que.
        #
        # "NAO SEI" NAO ENTRA NESSA REGRA, e a diferenca e pedagogica: ele
        # nao e uma leitura que falhou, e uma informacao. Nao saber a massa
        # molar e exatamente o motivo pelo qual a investigacao existe, e
        # devolver a mesma pergunta a quem acabou de dizer que nao sabe
        # seria insistir no que ja foi respondido. Ele segue para a
        # micropergunta, e o pedido de ajuda fica contado.
        leu = observacao not in (OBS_AMBIGUA, OBS_SEM_RESPOSTA)

        ja = await self._abertura(aluno, inv)
        if leu and not ja.get("respondida"):
            # O PEDIDO DE AJUDA FICA CONTADO EM VEZ DE SUMIR. Sem isto,
            # "nao sei" na porta da conversa nao deixaria rastro nenhum - e
            # e o pedido de ajuda mais informativo que o aluno pode fazer.
            pediu = observacao == OBS_NAO_SEI
            await self._registrar_ordem(
                aluno, inv, ordem=ORDEM_DA_ABERTURA,
                habilidade=inv.habilidade_alvo,
                acertou=acertou and not pediu,
                texto=None if pediu else normalizada.bruto,
                ajuda=pediu)

        hipotese = (hipotese_para(inv, normalizada.numero)
                    if leu and observacao != OBS_NAO_SEI else None)
        respostas, tentativas, falas = await self._estado_gravado(aluno, inv)
        saida = para_o_aluno(
            inv, respostas, tentativas=tentativas, falas=falas,
            abertura=(await self._abertura(aluno, inv)) if not leu else {
                "respondida": True,
                "bruto": normalizada.bruto,
                "observacao": observacao,
                "hipotese_como_dizer": hipotese.como_dizer if hipotese else None,
                "hipotese_codigo": hipotese.codigo if hipotese else None,
                "hipotese_estado": ABERTA if hipotese else None,
            })
        saida["correct"] = acertou
        saida["observacao"] = observacao
        return saida

    async def responder(self, aluno: str, inv_key: str, ordem: int,
                        escolha: str, *, requester: Requester) -> dict:
        """Uma tentativa numa etapa. A conferencia e AQUI, nunca no cliente.

        A ETAPA NAO E ESCOLHIDA PELO CLIENTE. Se a etapa pedida nao e a que
        esta aberta, a resposta e conferida e descartada: responder a etapa 3
        sem ter resolvido a 1 nao resolve a 3, porque a 3 so significa algo
        depois das anteriores. Sem isso, um cliente adulterado pularia a
        cadeia inteira e o sistema registraria um percurso que nao houve.
        """
        self._so_o_proprio(aluno, requester)
        inv = next((i for i in _todas() if i.key == inv_key), None)
        if inv is None:
            raise SemInvestigacao(inv_key)
        etapa = next((e for e in inv.etapas if e.ordem == int(ordem)), None)
        if etapa is None:
            raise SemInvestigacao(f"{inv_key}#{ordem}")

        resolvidas, tentativas, falas = await self._estado_gravado(aluno, inv)
        aberta = proxima_etapa(inv, resolvidas)
        # A OBSERVACAO VEM JUNTO do acerto. "errou" e "nao entendi o que
        # voce escreveu" pedem respostas diferentes do Edu, e perder essa
        # distincao faria o aluno ambiguo ser tratado como quem errou.
        acertou, observacao = avaliar_resposta(etapa, escolha)

        if aberta is not None and aberta.ordem == etapa.ordem:
            # TRES COISAS NAO GASTAM TENTATIVA: ambiguidade, ausencia e
            # "nao sei".
            #
            # As duas primeiras porque o aluno nao errou - o sistema nao
            # leu. A terceira porque dizer "nao sei" tambem nao e errar: e
            # a informacao mais honesta que ele pode dar, e cobra-la como
            # tentativa faria o retorno escalar de pista para regra
            # aplicada sem que ele tivesse tentado uma vez sequer.
            #
            # MAS "NAO SEI" DEIXA RASTRO, desde a migration 068.
            #
            # `help_requests` conta o pedido sem contar uma tentativa, e o
            # CheckConstraint `ck_guided_practice_unaided_has_no_help`
            # impede que quem pediu ajuda e acertou depois seja gravado
            # como tendo resolvido sozinho. Ate aqui, esse pedido sumia
            # junto com o turno - e o acerto seguinte virava autonomia.
            #
            # A ambiguidade e a ausencia continuam sem rastro, e de
            # proposito: nao houve pedido nem tentativa, houve uma leitura
            # que falhou. Registra-las como ajuda inflaria o contador com o
            # que e, na verdade, um problema de interface.
            if observacao not in _NAO_E_RESPOSTA:
                await self._registrar(aluno, inv, etapa, acertou=acertou,
                                      texto=escolha)
                resolvidas, tentativas, falas = await self._estado_gravado(
                    aluno, inv)
            elif observacao == OBS_NAO_SEI:
                await self._registrar(aluno, inv, etapa, acertou=False,
                                      ajuda=True)
                resolvidas, tentativas, falas = await self._estado_gravado(
                    aluno, inv)
        else:
            # Fora da vez: nada e gravado, e a visao devolvida e a real.
            acertou = False

        saida = para_o_aluno(inv, resolvidas, tentativas=tentativas,
                             falas=falas,
                             abertura=await self._abertura(aluno, inv))
        saida["correct"] = acertou
        saida["observacao"] = observacao
        # A HIPOTESE DEPOIS DA DISCRIMINANTE.
        #
        # Acertar enfraquece, errar apoia - e so a etapa que a hipotese
        # declarou como discriminante move o estado. Derivado, nao gravado.
        hipotese = _hipotese_aberta(inv)
        if hipotese is not None and hipotese.discriminante == etapa.ordem:
            saida["hipotese_estado"] = atualizar_hipotese(
                ABERTA, discriminante_correta=acertou if observacao not in (
                    "AMBIGUOUS_RESPONSE", "EMPTY_RESPONSE") else None)
            saida["hipotese_codigo"] = hipotese.codigo
            saida["hipotese_suspeita"] = hipotese.habilidade_suspeita
        return saida

    async def _registrar_ordem(self, aluno: str, inv: Investigacao, *,
                               ordem: int, habilidade: str,
                               acertou: bool, texto: str | None = None,
                               ajuda: bool = False) -> None:
        """Grava uma linha por (aluno, ordem). Serve a abertura e as etapas."""
        class _Falsa:
            pass

        falsa = _Falsa()
        falsa.ordem = ordem
        falsa.habilidade = habilidade
        await self._registrar(aluno, inv, falsa, acertou=acertou,
                              texto=texto, ajuda=ajuda)

    async def _registrar(self, aluno: str, inv: Investigacao, etapa,
                         *, acertou: bool, texto: str | None = None,
                         ajuda: bool = False) -> None:
        """Grava a tentativa - e NUNCA le um atributo depois de um commit.

        A fabrica de sessao da aplicacao nao passa `expire_on_commit=False`,
        entao o commit expira os objetos e o primeiro acesso a um atributo
        tenta ir ao banco fora do contexto async, levantando MissingGreenlet.
        `pratica_guiada` resolveu isso recarregando a linha; aqui o caminho
        comum nem precisa, porque a linha nasce ja com os valores finais - so
        ha UM commit por tentativa.

        Os testes deste servico usam `expire_on_commit=True` pelo mesmo
        motivo: com False o bug nao aparece no teste e aparece no navegador.
        """
        chave = _chave(inv.key, etapa.ordem)
        linha = await self._linha(aluno, chave)

        if linha is None:
            agora = _agora()
            nova = GuidedPracticeItem(
                student_external_id=aluno, item_key=chave,
                content_code=inv.content_code,
                # A micro-habilidade da ETAPA, nao a da investigacao: e a
                # etapa que isola uma coisa so.
                skill=etapa.habilidade, started_at=agora,
                # PEDIR AJUDA NAO E TENTAR. A linha nasce com zero
                # tentativas, e e isso que a distingue de um erro: a etapa
                # continua aberta e o retorno nao escala.
                attempts=0 if ajuda else 1,
                help_requests=1 if ajuda else 0,
                completed=False if ajuda else acertou,
                completed_at=None if ajuda else (agora if acertou else None),
                response_text=_cortado(texto),
                # DE PRIMEIRA. Nao e dominio - e a informacao de que esta
                # etapa nao precisou de ensino, e e isso que permite ao apoio
                # diminuir mais rapido. `hints_used` fica em zero: a
                # investigacao nao tem niveis de dica, e o CheckConstraint da
                # tabela exige essa coerencia.
                solved_unaided=False if ajuda else acertou)
            self._session.add(nova)
            try:
                await self._session.commit()
                return
            except IntegrityError:
                # Dois cliques simultaneos: a UNIQUE decidiu, e quem perdeu
                # segue pelo caminho de atualizacao abaixo.
                await self._session.rollback()
                linha = await self._linha(aluno, chave)
                if linha is None:  # pragma: no cover - a UNIQUE garante
                    raise

        linha.updated_at = _agora()
        if ajuda:
            # So o contador sobe. Nenhuma tentativa, nenhum texto: "nao sei"
            # nao e uma resposta a exibir como fala do aluno na etapa - ele
            # disse que nao sabe, e o Edu responde a isso no turno.
            linha.help_requests = int(linha.help_requests or 0) + 1
            await self._session.commit()
            return

        tentativas = linha.attempts + 1
        linha.attempts = tentativas
        if texto:
            linha.response_text = _cortado(texto)
        if acertou and not linha.completed:
            linha.completed = True
            linha.completed_at = _agora()
            # DE PRIMEIRA E SEM TER PEDIDO AJUDA. Quem disse "nao sei" e
            # acertou em seguida nao resolveu sozinho - e o
            # CheckConstraint `ck_guided_practice_unaided_has_no_help`
            # rejeitaria a linha se este `and` fosse esquecido.
            linha.solved_unaided = (tentativas == 1
                                    and not int(linha.help_requests or 0))
        await self._session.commit()


def _todas():
    from agente_ia_edu.services.investigacao_do_erro import INVESTIGACOES
    return INVESTIGACOES


def _hipotese_aberta(inv: Investigacao):
    """A hipotese que esta investigacao sabe levantar.

    Hoje ha no maximo uma por investigacao, e ela e a do gatilho. Quando
    houver mais, quem escolhe entre elas e o VALOR observado na abertura -
    e por isso a escolha mora em `hipotese_para`, nao aqui.
    """
    return inv.gatilhos[0].hipotese if inv.gatilhos else None


__all__ = ["InvestigacaoService", "SemInvestigacao"]
