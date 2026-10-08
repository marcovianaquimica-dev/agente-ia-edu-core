"""A SONDAGEM - primeiro a habilidade, depois o item.

O QUE MUDA
===========
O diagnostico escolhia QUESTOES: as do conteudo, preferindo as marcadas como
faceis. Isso responde a pergunta errada. A pergunta certa vem antes - QUAL
MICRO-HABILIDADE ESTOU MEDINDO AGORA? - e so depois: qual item mede essa
habilidade sem exigir as outras.

A diferenca aparece no resultado. "2 de 5" nao diz onde intervir; um mapa por
habilidade diz.

A ORDEM E DO BASICO PARA O DEPENDENTE
======================================
Sondar o problema completo primeiro faz o aluno errar por travar no primeiro
elo, e o sistema registra a lacuna errada. Comecando pela base, cada resposta
carrega menos ambiguidade: errar a leitura da formula so pode significar uma
coisa.

NAO MEDIR NAO E SABER, E TAMBEM NAO E NAO SABER
================================================
Uma habilidade sem item fica NAO_MEDIDO, e nunca entra nas fracas. Intervir
sobre o que ninguem mediu e inventar sobre a pessoa - a mesma regra que
`diagnostico_por_habilidade` ja aplica quando a amostra nao sustenta.

SEM IA
=======
Nada aqui chama provedor. Ha teste lendo o arquivo: a sondagem precisa
funcionar com o provedor fora do ar, porque e ela que abre a jornada.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from agente_ia_edu.services.grafo_pedagogico import GrafoPedagogico

# O que se pode dizer de uma habilidade depois da sondagem.
ESTADO_CONFIRMADO = "CONFIRMADO"
ESTADO_PRECISA_APOIO = "PRECISA_APOIO"
ESTADO_NAO_MEDIDO = "NAO_MEDIDO"


@dataclass(frozen=True)
class ItemDeSondagem:
    """Um item curado que mede UMA habilidade.

    `correta` e a chave da alternativa, nao o texto: e assim que o resto do
    sistema ja compara resposta com gabarito.

    O item se recusa a existir malformado. Um item de sondagem invalido nao
    falha ruidosamente - ele mede errado, em silencio, e o erro entra no mapa
    do aluno como se fosse evidencia.
    """

    key: str
    habilidade: str
    enunciado: str
    alternativas: Mapping[str, str]
    correta: str
    # Como conferir o gabarito sem confiar em quem escreveu o item. Opcional:
    # nem toda habilidade tem verdade aritmetica.
    conferencia: str | None = None

    def __post_init__(self) -> None:
        if not (self.enunciado or "").strip():
            raise ValueError(f"item {self.key!r} sem enunciado")
        if not self.alternativas:
            raise ValueError(f"item {self.key!r} sem alternativas")
        if self.correta not in self.alternativas:
            raise ValueError(
                f"item {self.key!r}: a correta {self.correta!r} nao esta "
                f"entre as alternativas")


def roteiro_de_sondagem(grafo: GrafoPedagogico,
                        itens: Sequence[ItemDeSondagem],
                        *, tamanho: int | None = None) -> list[ItemDeSondagem]:
    """Os itens na ordem em que devem ser perguntados.

    Do mais basico para o mais dependente, um por habilidade. Item de
    habilidade que nao esta no grafo fica de fora: ele mediria algo que o
    mapa nao sabe interpretar.

    `tamanho` corta o fim, nao o comeco - uma sondagem curta comeca pelo mais
    basico, que e onde a resposta carrega menos ambiguidade.
    """
    por_habilidade: dict[str, ItemDeSondagem] = {}
    for item in itens or ():
        if not grafo.tem(item.habilidade):
            continue
        por_habilidade.setdefault(item.habilidade, item)

    ordenados = sorted(
        por_habilidade.values(),
        key=lambda i: (len(grafo.prerequisitos_em_profundidade(i.habilidade)),
                       i.habilidade))
    return ordenados if tamanho is None else ordenados[:max(0, tamanho)]


def mapa_por_habilidade(grafo: GrafoPedagogico,
                        acertos: Mapping[str, bool]) -> dict[str, str]:
    """O estado de CADA habilidade do grafo depois da sondagem.

    Cobre o grafo inteiro de proposito: o que nao foi perguntado aparece como
    NAO_MEDIDO, e nao some do mapa. Uma habilidade ausente seria lida como
    "tudo bem por aqui" pela proxima camada.
    """
    saida: dict[str, str] = {}
    for code in grafo.codigos():
        if code not in acertos:
            saida[code] = ESTADO_NAO_MEDIDO
        else:
            saida[code] = (ESTADO_CONFIRMADO if acertos[code]
                           else ESTADO_PRECISA_APOIO)
    return saida


def fracas_do_mapa(mapa: Mapping[str, str]) -> tuple[str, ...]:
    """So as que foram medidas E ficaram fracas.

    NAO_MEDIDO nunca entra: intervir sobre o que ninguem mediu e inventar
    sobre a pessoa.
    """
    return tuple(code for code, estado in (mapa or {}).items()
                 if estado == ESTADO_PRECISA_APOIO)


__all__ = [
    "ESTADO_CONFIRMADO",
    "ESTADO_NAO_MEDIDO",
    "ESTADO_PRECISA_APOIO",
    "ItemDeSondagem",
    "fracas_do_mapa",
    "mapa_por_habilidade",
    "roteiro_de_sondagem",
]
