"""A LINHA DO TEMPO DE CADA MICRO-HABILIDADE - lida do que ja e gravado.

O QUE ESTE MODULO FAZ
======================
Uma coisa: monta, para um aluno e um conteudo, a sequencia de tentativas
INDEPENDENTES de cada micro-habilidade, e entrega a `consolidacao.situacao`.

E uma PROJECAO, nao uma segunda fonte de verdade - o §12 e explicito sobre
isso. Nada aqui e gravado, nada e recalculado por fora, e apagar este modulo
nao perderia nenhum dado: ele le `activity_result_items`, que ja e o
historico imutavel que o mapa de dominio tambem le.

POR QUE SO `activity_result_items`
===================================
Porque e o unico lugar onde mora resposta INDEPENDENTE. A interacao assistida
vive em `guided_practice_items`, que o mapa de dominio nao le por projeto
desde a PHASE 22 - e por isso acertar com ajuda nao consolida, sem que este
modulo precise filtrar nada. A garantia e a tabela, nao um `if`.

A OCASIAO E O RESULTADO
========================
Um lote corrigido de uma vez e UMA ocasiao, por mais questoes que tenha -
`activity_results.id` e justamente isso. Era o que faltava para responder a
pergunta do §12: cinco acertos numa tarde nao sao cinco oportunidades.

O DIAGNOSTICO CONTA
====================
Ele e resposta sem ajuda, e ja alimenta o mapa de dominio pelo mesmo caminho.
Exclui-lo aqui criaria duas contagens da mesma evidencia.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.db.models import (
    ActivityResult,
    ActivityResultItem,
    PedagogicalClassification,
)
from agente_ia_edu.services.consolidacao import situacao


async def linha_do_tempo(session: AsyncSession, *, aluno: str,
                         conteudo: str | None = None
                         ) -> dict[str, list[dict]]:
    """{micro-habilidade: [tentativas]}, na ordem em que aconteceram.

    `conteudo` filtra; sem ele, vem o aluno inteiro. Micro-habilidade nula -
    questao sem classificacao ativa - fica de fora: ela nao mede habilidade
    nenhuma, e inventar uma seria pior que omitir.
    """
    consulta = (
        select(ActivityResultItem.question_version_id,
               ActivityResultItem.is_correct,
               ActivityResult.id,
               ActivityResult.corrected_at)
        .join(ActivityResult, ActivityResult.id == ActivityResultItem.result_id)
        .where(ActivityResult.student_external_id == aluno)
        .order_by(ActivityResult.corrected_at)
    )
    linhas = (await session.execute(consulta)).all()
    if not linhas:
        return {}

    # OS IDS COMO VIERAM, nao como texto: `question_version_id` e uma coluna
    # `Uuid`, e um `in_()` com strings estoura no dialeto que valida o tipo.
    # A chave do dicionario, essa sim, e texto - para comparar sem depender
    # de como cada driver devolve o valor.
    vids = {v for v, _c, _r, _q in linhas}
    classificacoes = dict(
        ((str(v), (conteudo_, sub))
         for v, conteudo_, sub in (await session.execute(
             select(PedagogicalClassification.question_version_id,
                    PedagogicalClassification.content,
                    PedagogicalClassification.subcontent)
             .where(PedagogicalClassification.question_version_id.in_(vids),
                    PedagogicalClassification.lifecycle == "ACTIVE"))).all()))

    saida: dict[str, list[dict]] = {}
    for vid, certa, resultado_id, quando in linhas:
        cls = classificacoes.get(str(vid))
        if not cls:
            continue
        conteudo_da_questao, habilidade = cls
        if not habilidade:
            continue
        if conteudo and conteudo_da_questao != conteudo:
            continue
        saida.setdefault(habilidade, []).append({
            "quando": quando,
            "correta": bool(certa),
            # A OCASIAO E O RESULTADO: um lote corrigido de uma vez conta
            # como uma, por mais questoes que tenha.
            "ocasiao": str(resultado_id),
        })
    return saida


async def situacao_por_habilidade(session: AsyncSession, *, aluno: str,
                                  conteudo: str | None = None,
                                  agora=None) -> dict[str, dict]:
    """O estado de consolidacao de cada micro-habilidade daquele aluno."""
    tempos = await linha_do_tempo(session, aluno=aluno, conteudo=conteudo)
    return {habilidade: situacao(tentativas, agora=agora)
            for habilidade, tentativas in tempos.items()}


__all__ = ["linha_do_tempo", "situacao_por_habilidade"]
