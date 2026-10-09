"""HIPOTESE NAO E DIAGNOSTICO.

O QUE ESTE MODULO IMPEDE
=========================
O aluno responde 15 para "qual e a massa molar do NH3?". E plausivel que
ele tenha feito 14 + 1, ignorando o indice. E tentador dizer:

    "Voce esqueceu de multiplicar o hidrogenio por 3."

O sistema nao tem como saber isso. Ele viu um numero. Um aluno que chutou,
um que errou a soma e um que ignorou o indice escrevem o mesmo 15 - e dizer
ao primeiro que ele ignorou o indice e inventar sobre ele.

Entao o distrator SUGERE, e a micropergunta DECIDE:

    observacao -> hipotese ABERTA
                   -> pergunta discriminante
                      -> APOIADA  ou  ENFRAQUECIDA

A hipotese so existe para escolher a PROXIMA PERGUNTA. Ela nunca vira
conclusao sobre o aluno, nunca entra no mapa de dominio e nunca aparece na
tela como afirmacao - `como_dizer` carrega a frase com hedge, e ha teste
varrendo as formas acusatorias.

POR QUE O ESTADO E TRANSITORIO
===============================
Hipotese nao e persistida. Ela e derivada das respostas que JA estao
gravadas: a observacao que a abriu, e a observacao da discriminante que a
resolveu. Guardar o status seria uma segunda fonte de verdade para algo
recalculavel - e, no primeiro desencontro, a gravada venceria a real.

Isto tambem e o que mantem ZERO MIGRATION.

NAO HA PSEUDOESTATISTICA
=========================
Nao ha "confianca 0,73". O que existe e um estado de quatro valores, e cada
transicao vem de uma observacao real. Inventar um numero daria ao palpite a
aparencia de medida.
"""

from __future__ import annotations

from dataclasses import dataclass

ABERTA = "OPEN"
APOIADA = "SUPPORTED"
ENFRAQUECIDA = "WEAKENED"
REJEITADA = "REJECTED"

ESTADOS = (ABERTA, APOIADA, ENFRAQUECIDA, REJEITADA)


@dataclass(frozen=True)
class Hipotese:
    """Uma explicacao POSSIVEL para o que se observou.

    `habilidade_suspeita` e onde ela aponta - pode ser um pre-requisito da
    habilidade que estava sendo medida, e e justamente isso que faz a
    investigacao descer no grafo quando a hipotese se confirma.

    `como_dizer` e a frase que chega ao aluno. Ela precisa soar como
    suposicao, porque e o que ela e.

    `discriminante` e a ORDEM da etapa que separa esta hipotese das outras.
    Sem ela a hipotese nao serve para nada: seria um palpite sem como ser
    testado, e um palpite sem teste vira afirmacao.
    """

    codigo: str
    habilidade_suspeita: str
    como_dizer: str
    discriminante: int


def atualizar(estado: str, *, discriminante_correta: bool | None) -> str:
    """O estado da hipotese depois da pergunta que a discrimina.

    `discriminante_correta=None` quer dizer que a discriminante ainda nao
    foi respondida, ou que a resposta foi ambigua - e ambiguidade nao move
    hipotese. Ficar ABERTA e a resposta honesta.

    ACERTAR A DISCRIMINANTE ENFRAQUECE, NAO REJEITA. O aluno que conta os
    tres hidrogenios corretamente quando perguntado diretamente pode ainda
    assim nao te-los contado ao fazer a conta sozinho. O que se aprendeu e
    que a leitura da formula nao e o problema BASICO - nao que a hipotese
    esteja morta.
    """
    if estado not in ESTADOS:
        return ABERTA
    if estado in (APOIADA, ENFRAQUECIDA, REJEITADA):
        return estado
    if discriminante_correta is None:
        return ABERTA
    return ENFRAQUECIDA if discriminante_correta else APOIADA


def aponta_para_prerequisito(hipotese: Hipotese, *, alvo: str) -> bool:
    """Esta hipotese acusa um degrau ABAIXO do que estava sendo medido?

    Quando sim e ela fica APOIADA, o percurso desce no grafo antes de
    voltar ao alvo original - que e exatamente o caminho B do caso NH3.
    """
    return bool(hipotese.habilidade_suspeita
                and hipotese.habilidade_suspeita != alvo)


__all__ = [
    "ABERTA",
    "APOIADA",
    "ENFRAQUECIDA",
    "ESTADOS",
    "Hipotese",
    "REJEITADA",
    "aponta_para_prerequisito",
    "atualizar",
]
