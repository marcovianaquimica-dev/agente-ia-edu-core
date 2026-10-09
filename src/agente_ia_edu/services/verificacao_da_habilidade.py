"""A PONTE ENTRE A HABILIDADE ENSINADA E O ITEM L0 QUE A VERIFICA.

O QUE ESTE MODULO FAZ, E O QUE ELE NAO FAZ
===========================================
Faz uma coisa: dada a micro-habilidade, devolve os `question_version_id` dos
itens que a verificam sem apoio e que o aluno AINDA NAO RESPONDEU.

Nao decide qual habilidade verificar - quem decide e o assessor, por
`habilidade_que_trava` sobre o grafo, e e de la que o chamador a traz. Nao
decide quantas questoes - quem decide e a politica. Nao cria pratica, nao
corrige e nao escreve evidencia: devolve ids.

POR QUE ELE EXISTE SEPARADO
============================
A selecao por CONTEUDO ja existia e continua: `AdaptivePracticeService`
responde "quais questoes deste conteudo?". A pergunta deste bloco e outra -
"qual item verifica a micro-habilidade que acabei de ensinar, sem repetir o
item ensinado?" -, e a resposta nao e um subconjunto da primeira. E a mesma
razao pela qual o microdiagnostico traz a propria selecao.

Entao `create_practice` continua com UM executor, e a selecao ganha a
terceira estrategia. O resto - montagem da lista, atribuicao, tentativa,
correcao, evidencia - e identico.

O QUE "AINDA NAO RESPONDEU" SIGNIFICA AQUI
===========================================
Toda questao que ja apareceu num `ActivityResult` do aluno, em qualquer
conteudo e em qualquer finalidade. Nao e o recorte "recentes" da pratica, que
despriorize e REPOE quando falta item: repor um item de verificacao ja
respondido devolve ao aluno a resposta que ele ja conhece, e o acerto entra
na evidencia como aprendizagem.

A pratica guiada NAO entra nessa conta, e e deliberado: ela mora em
`guided_practice_items` e usa itens do conteudo curado do Nucleo, que nao tem
`question_version_id`. O CO2 da guiada nao e uma questao do banco - e por
isso que os itens de verificacao nao podem ser CO2, e ha teste disso em
`test_verificacao_autonoma`.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.db.models.assessments import (
    ActivityResult,
    ActivityResultItem,
)
from agente_ia_edu.services.instrumento_de_sondagem import Escolha
from agente_ia_edu.services.seletor_de_sondagem import SeletorDeSondagem


async def respondidas(session: AsyncSession, aluno: str) -> set[str]:
    """Todo `question_version_id` que o aluno ja respondeu, sem teto.

    Sem `limit`: o teto da pratica existe para nao esgotar o acervo de um
    conteudo com dezenas de questoes. Aqui o acervo de uma micro-habilidade
    tem tres itens, e um teto faria o quarto reaparecer.
    """
    linhas = (await session.execute(
        select(ActivityResultItem.question_version_id)
        .join(ActivityResult,
              ActivityResult.id == ActivityResultItem.result_id)
        .where(ActivityResult.student_external_id == aluno)
    )).all()
    return {str(vid) for (vid,) in linhas}


async def itens_para_verificar(session: AsyncSession, *, aluno: str,
                               conteudo: str, habilidade: str | None,
                               quantas: int,
                               escola_do_aluno: str | None = None
                               ) -> list[Escolha]:
    """Os itens L0 desta micro-habilidade que o aluno ainda nao viu.

    Devolve LISTA VAZIA quando nao ha habilidade identificada, quando o
    conteudo nao tem itens de verificacao curados, ou quando o aluno ja
    respondeu todos. Nos tres casos quem chamou segue pela selecao por
    conteudo que ja existia - o comportamento de antes deste bloco.

    Vazio nunca significa "serve qualquer coisa": significa "nao ha com que
    verificar esta habilidade sem repetir o que foi ensinado", e e melhor
    dizer isso do que servir o item da propria sondagem.
    """
    if not habilidade or quantas <= 0:
        return []
    return await SeletorDeSondagem(session).verificacao(
        conteudo=conteudo, habilidade=habilidade, quantas=quantas,
        ja_vistos=await respondidas(session, aluno),
        escola_do_aluno=escola_do_aluno)


def ids_de(escolhas: Sequence[Escolha]) -> list[str]:
    """Os ids, na ordem escolhida - o formato que `create_practice` recebe."""
    return [e.question_version_id for e in escolhas]


async def selecao_para_pratica(session: AsyncSession, *, aluno: str,
                               conteudo: str, quantas: int, requester
                               ) -> dict:
    """A selecao L0 pronta para `create_practice`, ou vazia.

    E A COLA, E ELA FICA AQUI E NAO NA ROTA, por um motivo so: assim ela tem
    teste. A rota que a chamava teria de ser exercitada por HTTP para provar
    que a habilidade viaja, e o que precisa de prova e exatamente esta
    composicao - quem decide a habilidade, e com o que ela e verificada.

    Devolve `{"skill", "question_version_ids", "motivo"}` quando ha itens, e
    `{}` quando nao ha. Vazio nao e falha: quem chamou segue pela selecao por
    conteudo, que e o comportamento anterior a este bloco e o de 36 dos 37
    conteudos do catalogo.

    A HABILIDADE VEM DO SERVIDOR, NUNCA DO CLIENTE
    ===============================================
    `habilidade_que_trava_de` le o grafo e as respostas reais do aluno. A
    alternativa - o navegador mandar a habilidade no POST - deixaria a UI
    decidir pedagogicamente, e uma habilidade vinda de fora poderia ser
    qualquer uma.
    """
    from agente_ia_edu.services.readiness_route import ReadinessRouteService

    try:
        _, alvo = await ReadinessRouteService(session).habilidade_que_trava_de(
            conteudo, aluno, requester=requester)
    except Exception:  # noqa: BLE001 - sem leitura possivel: segue por conteudo
        return {}
    if not alvo:
        return {}
    escolhas = await itens_para_verificar(
        session, aluno=aluno, conteudo=conteudo, habilidade=alvo,
        quantas=quantas)
    if not escolhas:
        return {}
    return {"skill": alvo,
            "question_version_ids": ids_de(escolhas),
            "motivo": escolhas[0].motivo}


__all__ = ["ids_de", "itens_para_verificar", "respondidas",
           "selecao_para_pratica"]
