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
from agente_ia_edu.services.investigacao_do_erro import (
    Investigacao,
    conferir_resposta,
    investigacao_para,
    para_o_aluno,
    proxima_etapa,
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

    async def _resolvidas(self, aluno: str, inv: Investigacao) -> dict[int, str]:
        """{ordem: resposta} a partir do que esta gravado.

        Etapa com linha concluida vale como respondida CORRETAMENTE - e ela so
        fica concluida tendo sido acertada, porque e `responder` quem marca.
        Etapa com linha aberta vale como tentada e nao resolvida.
        """
        chaves = {_chave(inv.key, e.ordem): e.ordem for e in inv.etapas}
        linhas = (await self._session.execute(
            select(GuidedPracticeItem).where(
                GuidedPracticeItem.student_external_id == aluno,
                GuidedPracticeItem.item_key.in_(list(chaves)),
            ))).scalars().all()
        saida: dict[int, str] = {}
        for linha in linhas:
            ordem = chaves[linha.item_key]
            etapa = next(e for e in inv.etapas if e.ordem == ordem)
            saida[ordem] = etapa.correta if linha.completed else _TENTOU_E_ERROU
        return saida

    def _resolver(self, content_code: str, skill: str | None) -> Investigacao:
        inv = investigacao_para(content_code, skill)
        if inv is None:
            raise SemInvestigacao(f"{content_code}/{skill}")
        return inv

    async def abrir(self, aluno: str, content_code: str, skill: str | None, *,
                    requester: Requester) -> dict:
        """Comeca (ou retoma) a investigacao daquela lacuna.

        Nao cria linha nenhuma: abrir nao e tentar. A primeira linha nasce na
        primeira resposta, que e quando algo de fato aconteceu.
        """
        self._so_o_proprio(aluno, requester)
        inv = self._resolver(content_code, skill)
        return para_o_aluno(inv, await self._resolvidas(aluno, inv))

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
        return proxima_etapa(inv, await self._resolvidas(aluno, inv)) is not None

    # -- escrita -----------------------------------------------------------

    async def _linha(self, aluno: str, chave: str) -> GuidedPracticeItem | None:
        return (await self._session.execute(
            select(GuidedPracticeItem).where(
                GuidedPracticeItem.student_external_id == aluno,
                GuidedPracticeItem.item_key == chave,
            ))).scalar_one_or_none()

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

        resolvidas = await self._resolvidas(aluno, inv)
        aberta = proxima_etapa(inv, resolvidas)
        acertou = conferir_resposta(etapa, escolha)

        if aberta is not None and aberta.ordem == etapa.ordem:
            await self._registrar(aluno, inv, etapa, acertou=acertou)
            resolvidas = await self._resolvidas(aluno, inv)
        else:
            # Fora da vez: nada e gravado, e a visao devolvida e a real.
            acertou = False

        saida = para_o_aluno(inv, resolvidas)
        saida["correct"] = acertou
        return saida

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


__all__ = ["InvestigacaoService", "SemInvestigacao"]
