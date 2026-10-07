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
    OBS_CORRETA,
    normalizar,
    observar,
)
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
            self, aluno: str,
            inv: Investigacao) -> tuple[dict[int, str], dict[int, int]]:
        """({ordem: resposta}, {ordem: tentativas}) a partir do que esta gravado.

        Etapa com linha concluida vale como respondida CORRETAMENTE - e ela so
        fica concluida tendo sido acertada, porque e `responder` quem marca.
        Etapa com linha aberta vale como tentada e nao resolvida.

        A CONTAGEM DE TENTATIVAS VIAJA JUNTO porque e ela que escolhe o nivel
        do retorno: na primeira vez o aluno recebe a regra sem o numero, e da
        segunda em diante a regra aplicada. Sem este segundo dicionario, a
        visao cairia sempre no primeiro nivel e quem errou tres vezes leria a
        mesma pista tres vezes.
        """
        chaves = {_chave(inv.key, e.ordem): e.ordem for e in inv.etapas}
        linhas = (await self._session.execute(
            select(GuidedPracticeItem).where(
                GuidedPracticeItem.student_external_id == aluno,
                GuidedPracticeItem.item_key.in_(list(chaves)),
            ))).scalars().all()
        respostas: dict[int, str] = {}
        tentativas: dict[int, int] = {}
        for linha in linhas:
            ordem = chaves[linha.item_key]
            etapa = next(e for e in inv.etapas if e.ordem == ordem)
            respostas[ordem] = (etapa.correta if linha.completed
                                else _TENTOU_E_ERROU)
            tentativas[ordem] = int(linha.attempts or 0)
        return respostas, tentativas

    def _resolver(self, content_code: str, skill: str | None) -> Investigacao:
        inv = investigacao_para(content_code, skill)
        if inv is None:
            raise SemInvestigacao(f"{content_code}/{skill}")
        return inv

    async def _abertura(self, aluno: str, inv: Investigacao) -> dict:
        """O que ja se sabe sobre a pergunta de abertura.

        Ela mora na mesma tabela das etapas, na ordem ZERO - ver
        `ORDEM_DA_ABERTURA`. O que NAO cabe na tabela e o texto que o aluno
        escreveu; `GuidedPracticeItem` nao tem coluna para resposta, e este
        bloco manteve zero migration.

        A consequencia esta declarada: ao RECARREGAR a pagina, o Edu lembra
        QUE ele respondeu e se acertou, mas nao reexibe o "15" nem a frase
        da hipotese. Dentro do turno - que e quando isso importa - os dois
        viajam na resposta do POST.
        """
        if inv.abertura is None:
            return {}
        linha = await self._linha(aluno, _chave(inv.key, ORDEM_DA_ABERTURA))
        if linha is None:
            return {}
        return {
            "respondida": True,
            "observacao": (OBS_CORRETA if linha.completed else None),
            "tentativas": int(linha.attempts or 0),
        }

    async def abrir(self, aluno: str, content_code: str, skill: str | None, *,
                    requester: Requester) -> dict:
        """Comeca (ou retoma) a investigacao daquela lacuna.

        Nao cria linha nenhuma: abrir nao e tentar. A primeira linha nasce na
        primeira resposta, que e quando algo de fato aconteceu.
        """
        self._so_o_proprio(aluno, requester)
        inv = self._resolver(content_code, skill)
        respostas, tentativas = await self._estado_gravado(aluno, inv)
        return para_o_aluno(inv, respostas, tentativas=tentativas,
                            abertura=await self._abertura(aluno, inv))

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
        respostas, _ = await self._estado_gravado(aluno, inv)
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

        ja = await self._abertura(aluno, inv)
        if not ja.get("respondida"):
            await self._registrar_ordem(
                aluno, inv, ordem=ORDEM_DA_ABERTURA,
                habilidade=inv.habilidade_alvo, acertou=acertou)

        hipotese = hipotese_para(inv, normalizada.numero)
        respostas, tentativas = await self._estado_gravado(aluno, inv)
        saida = para_o_aluno(
            inv, respostas, tentativas=tentativas,
            abertura={
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

        resolvidas, tentativas = await self._estado_gravado(aluno, inv)
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
            # O CUSTO ESTA DECLARADO: o pedido de ajuda nao fica gravado.
            # `GuidedPracticeItem` nao tem coluna para distinguir "tentou e
            # errou" de "pediu ajuda", e grava-lo como tentativa seria
            # registrar um erro que nao houve. Se um dia for preciso contar
            # quantas vezes ele pediu ajuda, ai havera necessidade
            # arquitetural de coluna - e nao antes.
            from agente_ia_edu.services.resposta_do_aluno import (
                OBS_AMBIGUA,
                OBS_NAO_SEI,
                OBS_SEM_RESPOSTA,
            )
            if observacao not in (OBS_AMBIGUA, OBS_SEM_RESPOSTA, OBS_NAO_SEI):
                await self._registrar(aluno, inv, etapa, acertou=acertou)
                resolvidas, tentativas = await self._estado_gravado(aluno, inv)
        else:
            # Fora da vez: nada e gravado, e a visao devolvida e a real.
            acertou = False

        saida = para_o_aluno(inv, resolvidas, tentativas=tentativas,
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
                               acertou: bool) -> None:
        """Grava uma linha por (aluno, ordem). Serve a abertura e as etapas."""
        class _Falsa:
            pass

        falsa = _Falsa()
        falsa.ordem = ordem
        falsa.habilidade = habilidade
        await self._registrar(aluno, inv, falsa, acertou=acertou)

    async def _registrar(self, aluno: str, inv: Investigacao, etapa,
                         *, acertou: bool) -> None:
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
                attempts=1, completed=acertou,
                completed_at=agora if acertou else None,
                # DE PRIMEIRA. Nao e dominio - e a informacao de que esta
                # etapa nao precisou de ensino, e e isso que permite ao apoio
                # diminuir mais rapido. `hints_used` fica em zero: a
                # investigacao nao tem niveis de dica, e o CheckConstraint da
                # tabela exige essa coerencia.
                solved_unaided=acertou)
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

        tentativas = linha.attempts + 1
        linha.attempts = tentativas
        linha.updated_at = _agora()
        if acertou and not linha.completed:
            linha.completed = True
            linha.completed_at = _agora()
            linha.solved_unaided = (tentativas == 1)
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
