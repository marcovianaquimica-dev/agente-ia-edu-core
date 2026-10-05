"""PRATICA GUIADA - o aluno tenta, e a ajuda chega so quando precisa.

O QUE ESTE SERVICO E
=====================
A etapa entre o exemplo resolvido e a pratica autonoma:

    o aluno recebe um problema
    tenta
    errou (ou pediu) -> recebe UMA ajuda, um nivel por vez
    tenta de novo
    ...
    conclui - e o sistema sabe QUANTA ajuda foi necessaria

A REGRA QUE SUSTENTA TUDO
==========================
    CONSEGUIR COM AJUDA  !=  DOMINAR SOZINHO

Por isso nada daqui escreve dominio. O registro vai para
`guided_practice_items`, que o mapa de dominio NAO le - a mesma escolha que
`material_progress` ja fez para a leitura de material.

`_Grain.add`, no mapa, conta toda resposta respondida em `answered`/`correct`
e nao pondera ajuda. Se a pratica guiada passasse por ali, acertar depois de
quatro dicas entraria como acerto igual a acertar sozinho. A comprovacao
continua exigindo uma pratica AUTONOMA, pelo caminho de sempre.

O QUE NAO VAZA
===============
A letra correta nunca viaja antes de o aluno acertar. Nem no item, nem na
resposta de um erro, nem num pedido de ajuda - e o estado de cada equacao
(`balanceada`) tambem nao, porque dizer quais fecham e dizer a resposta com
outras palavras. `itens_guiados.para_o_aluno` e o unico caminho de saida.

SEM IA
=======
A ajuda e deterministica: num conteudo onde a verdade e contagem de atomos,
as dificuldades sao conhecidas. Um provider podera, depois, ADAPTAR a
linguagem de cada nivel - o contrato (o que cada nivel faz, quando ele e
liberado, e que nenhum deles entrega a resposta) continua sendo do Nucleo.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.db.models.guided_practice import GuidedPracticeItem
from agente_ia_edu.services.itens_guiados import (
    ITENS,
    conferir,
    item_para,
    para_o_aluno,
)
from agente_ia_edu.services.question_list_store import Requester


class SemItemGuiado(LookupError):
    """404 - nao ha pratica guiada para este conteudo.

    Devolver um item generico seria pior: o aluno praticaria outra coisa
    achando que esta fechando a lacuna dele.
    """


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _por_chave(item_key: str) -> dict | None:
    return next((i for i in ITENS if i["key"] == item_key), None)


class PraticaGuiadaService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- autorizacao -------------------------------------------------------

    @staticmethod
    def _so_o_proprio(aluno: str, requester: Requester) -> None:
        """A pratica guiada e da pessoa. Ninguem le a de outra.

        Mesma regra que `AdaptivePracticeService._authz_self` ja aplica - nao
        ha autorizacao paralela aqui.
        """
        if getattr(requester, "is_platform_admin", False):
            return
        if (requester.external_user_id or "") != aluno:
            raise PermissionError("esta pratica guiada nao e sua")

    # -- leitura -----------------------------------------------------------

    async def _linha(self, aluno: str, item_key: str) -> GuidedPracticeItem | None:
        return (await self._session.execute(
            select(GuidedPracticeItem).where(
                GuidedPracticeItem.student_external_id == aluno,
                GuidedPracticeItem.item_key == item_key,
            )
        )).scalar_one_or_none()

    async def _fresca(self, linha: GuidedPracticeItem) -> GuidedPracticeItem:
        """Recarrega a linha DEPOIS de um commit.

        A fabrica de sessao da aplicacao nao passa `expire_on_commit=False`,
        entao o commit expira os objetos e o primeiro acesso a um atributo
        tenta ir ao banco - fora do contexto async, o que levanta
        MissingGreenlet. Recarregar aqui mantem esse IO onde ele pode
        acontecer.

        Os testes deste servico usam `expire_on_commit=True` pelo mesmo
        motivo: com False o bug nao aparece no teste e aparece no navegador,
        que foi o que aconteceu.
        """
        await self._session.refresh(linha)
        return linha

    def _visao(self, item: dict, linha: GuidedPracticeItem | None) -> dict:
        """O que o cliente recebe. Nada alem disto sai daqui."""
        usadas = linha.hints_used if linha else 0
        saida = para_o_aluno(item, ajudas_liberadas=usadas)
        saida.update({
            "attempts": linha.attempts if linha else 0,
            "hints_used": usadas,
            "max_hint_level": linha.max_hint_level if linha else 0,
            "completed": bool(linha.completed) if linha else False,
            # A palavra que o resto do sistema precisa para NAO confundir
            # "conseguiu" com "domina".
            "solved_unaided": bool(linha.solved_unaided) if linha else False,
        })
        if saida["completed"]:
            # So depois de concluir a letra pode aparecer - e o fecho, que
            # diz o que ficou da ideia, tambem.
            saida["correct_option"] = item["correta"]
            saida["fecho"] = item.get("fecho")
        return saida

    async def estado(self, aluno: str, item_key: str, *,
                     requester: Requester) -> dict:
        self._so_o_proprio(aluno, requester)
        item = _por_chave(item_key)
        if item is None:
            raise SemItemGuiado(item_key)
        return self._visao(item, await self._linha(aluno, item_key))

    # -- escrita -----------------------------------------------------------

    async def _abrir_linha(self, aluno: str, item: dict) -> GuidedPracticeItem:
        """A linha daquele (aluno, item), criando-a na primeira vez.

        Idempotente de proposito: voltar reabre a MESMA linha. Uma segunda
        linha zeraria os contadores, e quem usou quatro dicas apareceria como
        quem usou zero.
        """
        linha = await self._linha(aluno, item["key"])
        if linha is not None:
            return linha
        linha = GuidedPracticeItem(
            student_external_id=aluno, item_key=item["key"],
            content_code=item["content_code"], skill=item.get("skill"),
            started_at=_agora())
        self._session.add(linha)
        try:
            await self._session.commit()
        except IntegrityError:
            # Dois cliques simultaneos no mesmo "comecar": a UNIQUE decidiu.
            await self._session.rollback()
            linha = await self._linha(aluno, item["key"])
            if linha is None:  # pragma: no cover - a UNIQUE garante que existe
                raise
            return linha
        return await self._fresca(linha)

    async def abrir(self, aluno: str, content_code: str, skill: str | None, *,
                    requester: Requester) -> dict:
        """Comeca (ou retoma) a pratica guiada da lacuna medida."""
        self._so_o_proprio(aluno, requester)
        item = item_para(content_code, skill)
        if item is None:
            raise SemItemGuiado(content_code)
        return self._visao(item, await self._abrir_linha(aluno, item))

    async def pedir_ajuda(self, aluno: str, item_key: str, *,
                          requester: Requester) -> dict:
        """Libera o PROXIMO nivel de ajuda - um por vez.

        Pedir antes de tentar e permitido. Travar isso obrigaria o aluno a
        errar de proposito para receber ajuda, e ele aprenderia a chutar em
        vez do conteudo. O que importa e que o pedido fique REGISTRADO.
        """
        self._so_o_proprio(aluno, requester)
        item = _por_chave(item_key)
        if item is None:
            raise SemItemGuiado(item_key)
        linha = await self._abrir_linha(aluno, item)

        total = len(item["ajudas"])
        if linha.hints_used < total:
            linha.hints_used += 1
            linha.max_hint_level = max(linha.max_hint_level, linha.hints_used)
            linha.updated_at = _agora()
            await self._session.commit()
            linha = await self._fresca(linha)
        return self._visao(item, linha)

    async def responder(self, aluno: str, item_key: str, escolha: str, *,
                        requester: Requester) -> dict:
        """Uma tentativa. A conferencia e AQUI, nunca no cliente."""
        self._so_o_proprio(aluno, requester)
        item = _por_chave(item_key)
        if item is None:
            raise SemItemGuiado(item_key)
        linha = await self._abrir_linha(aluno, item)

        acertou = conferir(item, escolha)
        linha.attempts += 1
        linha.updated_at = _agora()
        if acertou and not linha.completed:
            linha.completed = True
            linha.completed_at = _agora()
            # SOZINHO so quando nenhuma ajuda foi usada ate aqui. Esta e a
            # linha que impede "conseguiu com ajuda" de virar "domina" -
            # e ha CheckConstraint no banco repetindo a mesma regra, porque
            # uma regra desta importancia nao pode depender so do codigo.
            linha.solved_unaided = (linha.hints_used == 0)
        await self._session.commit()
        linha = await self._fresca(linha)

        saida = self._visao(item, linha)
        saida["correct"] = acertou
        if not acertou:
            # O que vai para a tela quando erra: nada sobre qual e a certa.
            saida["correct_option"] = None
            saida.pop("correct_option")
        return saida
