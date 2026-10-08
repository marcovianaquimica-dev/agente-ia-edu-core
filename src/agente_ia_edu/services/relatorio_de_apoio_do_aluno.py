"""O RELATORIO DE APOIO, montado com os fatos do banco.

O que `relatorio_de_apoio` faz e escrever o documento a partir de fatos. Este
modulo e o outro lado: ele BUSCA os fatos, e nao decide nada.

DE ONDE SAEM
=============
    consolidacao_do_aluno    o que ele fez SOZINHO, e em quantas ocasioes
    guided_practice_items    o que precisou de AJUDA, e de quanta

As duas fontes continuam separadas aqui como ja sao no banco - acertar com
dica vive numa tabela que o mapa de dominio nao le, e por isso nao entra em
"fez sozinho" sem que este modulo precise de um `if`.

NADA E GRAVADO
===============
Pedir o relatorio nao muda o historico do aluno. Se este modulo for apagado,
nenhum dado se perde: ele e uma LEITURA de duas tabelas que ja existiam.

E NADA E ENVIADO
=================
Este modulo nao conhece destinatario, nao importa transporte e nao sabe
enviar nada - o §17 proibe, e ha teste varrendo a fonte e as rotas do app.
Ele devolve um dicionario para quem pediu, e quem pediu e o proprio aluno.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.db.models import CatalogNode
from agente_ia_edu.db.models.guided_practice import GuidedPracticeItem
from agente_ia_edu.services.consolidacao_do_aluno import situacao_por_habilidade
from agente_ia_edu.services.relatorio_de_apoio import (
    montar_relatorio,
    rotulo_legivel,
)

# Quando nenhum conteudo e pedido, o documento cobre o percurso inteiro. O
# subtitulo diz isso em vez de nomear um conteudo que o aluno nao escolheu.
TUDO = "Seus estudos"


async def apoios_do_aluno(session: AsyncSession, *, aluno: str,
                          conteudo: str | None = None) -> list[dict]:
    """As linhas de pratica assistida daquele aluno, como dicionarios.

    Sem `completed`, nada aconteceu ainda - uma etapa aberta nao e apoio
    usado, e list-la seria relatar o que o aluno ainda esta fazendo.
    """
    consulta = (
        select(GuidedPracticeItem.skill,
               GuidedPracticeItem.content_code,
               GuidedPracticeItem.hints_used,
               GuidedPracticeItem.help_requests,
               GuidedPracticeItem.solved_unaided,
               GuidedPracticeItem.completed,
               GuidedPracticeItem.completed_at)
        .where(GuidedPracticeItem.student_external_id == aluno,
               GuidedPracticeItem.completed.is_(True))
        .order_by(GuidedPracticeItem.completed_at)
    )
    if conteudo:
        consulta = consulta.where(GuidedPracticeItem.content_code == conteudo)
    return [
        {"skill": skill, "content_code": codigo, "hints_used": dicas,
         "help_requests": pedidos, "solved_unaided": bool(sozinho),
         "completed": bool(feito), "completed_at": quando}
        for skill, codigo, dicas, pedidos, sozinho, feito, quando
        in (await session.execute(consulta)).all()
        if skill  # habilidade nula nao nomeia nada; omitir e melhor que inventar
    ]


async def _nome_do_conteudo(session: AsyncSession, codigo: str) -> str:
    """O nome que o aluno ve, e nao o codigo interno - quando existe.

    Sem no no catalogo, o codigo volta como veio. Inventar um nome bonito
    para um codigo desconhecido seria escrever no papel algo que ninguem
    cadastrou.
    """
    achado = (await session.execute(
        select(CatalogNode.name).where(CatalogNode.code == codigo)
        .limit(1))).scalar_one_or_none()
    return achado or codigo


async def nome_do_aluno(session: AsyncSession, *, aluno: str,
                        escola: str | None = None) -> str | None:
    """O nome dele, quando a identidade esta cadastrada. Nunca inventado.

    Em ambiente de desenvolvimento a identidade e so um texto no cabecalho, e
    nao ha `Person` por tras - entao isto devolve None, e o documento usa o
    identificador. Um nome de mentira num papel que o aluno leva a alguem
    seria pior que um codigo.
    """
    from agente_ia_edu.db.models.academic import Person, User

    consulta = (
        select(Person.full_name)
        .join(User, User.person_id == Person.id)
        .where(User.external_user_id == aluno)
        .limit(1)
    )
    if escola:
        try:
            consulta = consulta.where(User.school_id == _uuid.UUID(str(escola)))
        except (ValueError, AttributeError, TypeError):
            # Escola nao-UUID: o filtro cai, e o nome vem sem ele. Nao e
            # vazamento - `external_user_id` ja identifica uma pessoa so.
            pass
    try:
        return (await session.execute(consulta)).scalar_one_or_none()
    except Exception:  # noqa: BLE001 - nome e enfeite; o relatorio nao cai por ele
        return None


async def montar_do_banco(session: AsyncSession, *, aluno: str,
                          aluno_nome: str | None = None,
                          conteudo: str | None = None,
                          escola: str | None = None,
                          agora: datetime | None = None) -> dict:
    """O relatorio pronto, com os fatos daquele aluno.

    `aluno` e o identificador externo de quem pediu - e so dele. Nao ha
    parametro para pedir o de outra pessoa, e e de proposito: autorizacao que
    depende de uma checagem e autorizacao que alguem pode esquecer de fazer.
    """
    habilidades = await situacao_por_habilidade(session, aluno=aluno,
                                                conteudo=conteudo, agora=agora)
    apoios = await apoios_do_aluno(session, aluno=aluno, conteudo=conteudo)

    titulo_do_conteudo = (await _nome_do_conteudo(session, conteudo)
                          if conteudo else TUDO)

    # OS ROTULOS SAO FORMATACAO, nao traducao inventada: `MASSA_MOLAR` vira
    # "Massa molar", e nada mais. O codigo que nao for legivel aparece como
    # esta - melhor um codigo cru que um nome que ninguem escreveu.
    codigos = set(habilidades) | {a["skill"] for a in apoios if a.get("skill")}
    rotulos = {c: rotulo_legivel(c) for c in codigos}

    if not aluno_nome:
        aluno_nome = await nome_do_aluno(session, aluno=aluno, escola=escola)

    return montar_relatorio(aluno_nome=aluno_nome or aluno,
                            conteudo=titulo_do_conteudo,
                            habilidades=habilidades, apoios=apoios,
                            agora=agora, rotulos=rotulos)


__all__ = ["TUDO", "apoios_do_aluno", "montar_do_banco", "nome_do_aluno"]
