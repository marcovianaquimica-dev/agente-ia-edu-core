"""Calculo deterministico de ranking com empates - sem IA."""
from __future__ import annotations


def calcular_ranking(notas: list[tuple[str, int]]) -> dict[str, int]:
    """Recebe uma lista de (id, nota) e retorna {id: posicao}.

    Empate = mesma posicao; a proxima posicao pula o numero de
    itens empatados (ex: dois em 1o lugar -> o proximo fica em 3o).
    """
    ordenado = sorted(notas, key=lambda item: item[1], reverse=True)
    posicoes: dict[str, int] = {}
    posicao_atual = 0
    nota_anterior = None
    for indice, (id_item, nota) in enumerate(ordenado, start=1):
        if nota != nota_anterior:
            posicao_atual = indice
            nota_anterior = nota
        posicoes[id_item] = posicao_atual
    return posicoes
