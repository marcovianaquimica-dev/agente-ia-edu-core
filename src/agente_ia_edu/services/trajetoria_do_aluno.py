"""A TRAJETORIA do aluno naquele conteudo - o que a media acumulada esconde.

O PROBLEMA MEDIDO
=================
Em 2026-10-05, pelo caminho real da API no banco de desenvolvimento:

    diagnostico 0 de 3      acumulado 0/3   = 0,00
    pratica     1 de 5      acumulado 1/8   = 0,125
    pratica     4 de 5      acumulado 5/13  = 0,385
    pratica     5 de 5      acumulado 10/18 = 0,556

O aluno acertou NOVE das ultimas DEZ e continuou vendo a mesma explicacao, com
`escalate` ligado. Pela media acumulada, quem comeca mal precisa de onze
acertos seguidos so para sair da faixa de melhoria, e de vinte e sete para
chegar a faixa forte. Na pratica, o sistema nao conseguia reconhecer que
alguem aprendeu.

A media responde "como foi ate aqui". A pergunta do assessor e outra: "ele
aprendeu?". Este modulo responde a segunda, e so ela.

O QUE ELE NAO FAZ
=================
Nao libera ninguem e nao apaga historico. Devolve uma TENDENCIA; quem decide o
proximo passo e `assessor_pedagogico`. E uma tentativa forte sozinha devolve
RECUPERANDO - que leva a VERIFICAR, nao a avancar. Esta separacao e o que
impede "acertei 4 de 5 uma vez" de virar "dominei".

POR QUE DUAS TENTATIVAS, E NAO UMA
===================================
Uma tentativa forte depois de um historico ruim pode ser sorte, pode ser um
lote facil, pode ser o aluno tendo decorado aquelas questoes. Duas seguidas,
cada uma com amostra suficiente, e um padrao - e a segunda e justamente a
verificacao curta que o assessor pede. Nao ha numero novo aqui: "forte" e
"fraca" sao os cortes que a `PerformanceThresholdPolicy` ja usa em todo o
resto do sistema.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from agente_ia_edu.services.pedagogical_analysis import (
    PerformanceThresholdPolicy,
)

# Duas tentativas fortes seguidas: o aluno mostrou que aprendeu.
TENDENCIA_CONFIRMADA = "CONFIRMADA"
# Uma tentativa forte depois de dificuldade: promissor, ainda nao confirmado.
TENDENCIA_RECUPERANDO = "RECUPERANDO"
# A ultima tentativa com amostra suficiente ficou na faixa de lacuna.
TENDENCIA_PERSISTENTE = "PERSISTENTE"
# Nem uma coisa nem outra - inclusive "ainda nao houve tentativa nenhuma".
TENDENCIA_INDEFINIDA = "INDEFINIDA"

# Duas tentativas seguidas. Nao e um parametro pedagogico escondido: e o
# tamanho da confirmacao, e esta aqui com nome para quem vier depois saber
# que mexer nele muda o significado de "confirmado".
TENTATIVAS_PARA_CONFIRMAR = 2


def tentativas_de_resultados(linhas: Iterable[dict]) -> list[dict]:
    """De itens de resultado gravados para tentativas, em ordem cronologica.

    Cada `ActivityResult` e UMA tentativa; seus `ActivityResultItem` sao as
    questoes dela. A consulta que alimenta isto ja existe e ja e feita por
    outro motivo (`_habilidades_de`): a unica coisa que faltava era nao jogar
    fora a ordem.
    """
    por_tentativa: dict = {}
    for linha in linhas or []:
        chave = linha.get("result_id")
        atual = por_tentativa.setdefault(
            chave, {"quando": linha.get("corrected_at"),
                    "answered": 0, "correct": 0})
        atual["answered"] += 1
        if linha.get("is_correct"):
            atual["correct"] += 1

    ordenadas = sorted(por_tentativa.values(),
                       key=lambda t: (str(t["quando"] or ""),))
    return [{"answered": t["answered"], "correct": t["correct"]}
            for t in ordenadas]


def _acerto(tentativa: dict) -> float | None:
    respondidas = int((tentativa or {}).get("answered") or 0)
    if respondidas <= 0:
        return None
    return int((tentativa or {}).get("correct") or 0) / respondidas


def _avaliaveis(tentativas: Sequence[dict],
                thresholds: PerformanceThresholdPolicy) -> list[dict]:
    """So as tentativas com amostra suficiente para concluir alguma coisa.

    Uma tentativa de duas questoes nao e "fraca" nem "forte": a propria
    politica diz que abaixo do minimo nao ha conclusao. Ignora-las e o
    contrario de escondê-las - e recusar-se a concluir a partir delas, nos dois
    sentidos. Por isso um 2 de 2 nao confirma nada, e um 0 de 2 tambem nao
    derruba uma sequencia ja estabelecida.
    """
    return [t for t in (tentativas or [])
            if int((t or {}).get("answered") or 0) >= thresholds.min_sample_size]


def tendencia(tentativas: Sequence[dict] | None, *,
              thresholds: PerformanceThresholdPolicy | None = None) -> str:
    """Como o aluno esta indo AGORA naquele conteudo.

    `tentativas` vem em ordem cronologica, cada uma com `answered` e `correct`.
    """
    thresholds = thresholds or PerformanceThresholdPolicy.default()
    avaliaveis = _avaliaveis(tentativas or [], thresholds)
    if not avaliaveis:
        return TENDENCIA_INDEFINIDA

    fortes_no_fim = 0
    for t in reversed(avaliaveis):
        acerto = _acerto(t)
        if acerto is not None and acerto >= thresholds.strong_accuracy:
            fortes_no_fim += 1
            continue
        break

    if fortes_no_fim >= TENTATIVAS_PARA_CONFIRMAR:
        return TENDENCIA_CONFIRMADA
    if fortes_no_fim == 1:
        return TENDENCIA_RECUPERANDO

    ultima = _acerto(avaliaveis[-1])
    if ultima is not None and ultima < thresholds.improvement_accuracy:
        return TENDENCIA_PERSISTENTE
    # Faixa intermediaria: a politica ja se recusa a chamar isso de lacuna, e
    # tambem nao e dominio. Dizer INDEFINIDA e a unica resposta honesta.
    return TENDENCIA_INDEFINIDA


__all__ = [
    "TENDENCIA_CONFIRMADA",
    "TENDENCIA_INDEFINIDA",
    "TENDENCIA_PERSISTENTE",
    "TENDENCIA_RECUPERANDO",
    "TENTATIVAS_PARA_CONFIRMAR",
    "tendencia",
    "tentativas_de_resultados",
]
