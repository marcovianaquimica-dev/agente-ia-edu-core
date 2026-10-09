"""Segmentacao do caderno em itens (C1). PROTOTIPO EXPERIMENTAL.

O corpo de um item vai do fim do seu rotulo ate o inicio do rotulo seguinte.
Identico a V1 nisso. A diferenca esta em ``text_layer.SEPARADOR_PAGINA``:
unindo as paginas com ``"\\n\\f"`` o ``^`` volta a funcionar no topo de
pagina, que e exatamente a causa C1.

A V2 **nao** descarta silenciosamente o item de enunciado vazio, como a V1 faz
em ``if not statement: continue``. Ele e devolvido com o problema nomeado, para
aparecer na contagem.
"""

from __future__ import annotations

from dataclasses import dataclass

from .text_layer import PADRAO_CABECALHO, CamadaDeTexto


@dataclass(frozen=True)
class Segmento:
    numero: int
    corpo: str
    inicio: int
    fim: int
    pagina_inicio: int
    pagina_fim: int
    ocorrencia: int

    @property
    def atravessa_pagina(self) -> bool:
        return self.pagina_fim > self.pagina_inicio


def segmentar(camada: CamadaDeTexto) -> list[Segmento]:
    cabecalhos = list(PADRAO_CABECALHO.finditer(camada.documento))
    saida: list[Segmento] = []
    vistos: dict[int, int] = {}
    for indice, cabecalho in enumerate(cabecalhos):
        fim = (cabecalhos[indice + 1].start() if indice + 1 < len(cabecalhos)
               else len(camada.documento))
        numero = int(cabecalho.group(1))
        vistos[numero] = vistos.get(numero, 0) + 1
        corpo = camada.documento[cabecalho.end():fim]
        saida.append(Segmento(
            numero=numero,
            corpo=corpo.strip("\n"),
            inicio=cabecalho.start(),
            fim=fim,
            pagina_inicio=camada.pagina_de(cabecalho.start()),
            pagina_fim=camada.pagina_de(max(cabecalho.start(), fim - 1)),
            ocorrencia=vistos[numero],
        ))
    return saida
