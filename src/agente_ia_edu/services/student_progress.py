"""MEU PROGRESSO - traducao do mapa de dominio para o que o aluno ve.

Esta camada nao DECIDE nada. Ela traduz.

O motor sabe accuracy, tamanho de amostra, evidencia definitiva x provisoria,
fechamento forcado, dependencia visual e a origem de cada resposta. O aluno ve
tres palavras, mais uma quarta para quando ainda nao sabemos.

POR QUE NENHUM CORTE MORA AQUI
===============================
``PerformanceThresholdPolicy`` se declara "single source of truth for the
strong/improvement bands" e ja tem ``band()``. Reimplementar 0.60 / 0.80 /
min_sample_size nesta camada criaria uma segunda definicao do que significa
saber alguma coisa - e as duas divergiriam no dia em que alguem ajustasse uma
delas, sem que nada avisasse. Entao aqui so existe um dicionario de traducao.

A REGRA PEDAGOGICA
===================
FALTA DE EVIDENCIA NAO E EVIDENCIA DE DIFICULDADE.

``INSUFFICIENT_SAMPLE`` tem texto proprio e NAO colapsa em "Precisa de
atenção". Um aluno que nunca respondeu nada sobre Soluções nao esta com
dificuldade em Soluções - nos e que nao sabemos. Dizer o contrario e fazer um
adolescente se achar ruim numa materia que ele nunca tentou.
"""

from __future__ import annotations

from agente_ia_edu.services.pedagogical_analysis import (
    BAND_IMPROVEMENT,
    BAND_INSUFFICIENT,
    BAND_INTERMEDIATE,
    BAND_NO_DATA,
    BAND_STRONG,
    PerformanceThresholdPolicy,
)

FAIXA_PRECISA_ATENCAO = "Precisa de atenção"
FAIXA_EM_DESENVOLVIMENTO = "Em desenvolvimento"

# DESEMPENHO ELEVADO - e so isso.
#
# Esta faixa se chamava "Consolidado" ate 2026-10-08, e a palavra estava
# errada: ela traduz BAND_STRONG, que e acerto forte numa unica ocasiao. O
# §12 reserva "consolidacao" para consistencia em OCASIOES DIFERENTES, e
# `services/consolidacao` passou a calcular isso de verdade.
#
# Chamar as duas coisas pelo mesmo nome fazia o aluno ler "consolidado"
# depois de uma tarde boa - e ler de novo a mesma palavra depois de repetir
# duas semanas depois, sem nada distinguir as duas situacoes.
FAIXA_INDO_BEM = "Indo bem"

FAIXA_CONHECENDO = "Ainda estamos conhecendo seu aprendizado"

# A ordem em que as faixas aparecem na tela. O que ainda nao sabemos vem por
# ULTIMO, nao no meio das faixas de desempenho: nao e um degrau pior nem
# melhor que os outros, e de outra natureza.
_ORDEM = {
    FAIXA_PRECISA_ATENCAO: 1,
    FAIXA_EM_DESENVOLVIMENTO: 2,
    FAIXA_INDO_BEM: 3,
    FAIXA_CONHECENDO: 4,
}

# Toda banda que a politica sabe produzir tem traducao. Se ela ganhar uma
# banda nova e ninguem traduzir, ha teste que falha - o aluno nunca pode ver
# o nome cru de um estado interno.
FAIXA_POR_BANDA = {
    BAND_STRONG: FAIXA_INDO_BEM,
    BAND_INTERMEDIATE: FAIXA_EM_DESENVOLVIMENTO,
    BAND_IMPROVEMENT: FAIXA_PRECISA_ATENCAO,
    BAND_INSUFFICIENT: FAIXA_CONHECENDO,
    BAND_NO_DATA: FAIXA_CONHECENDO,
}


def faixa_do_aluno(*, answered: int, accuracy: float | None,
                   thresholds: PerformanceThresholdPolicy | None = None) -> dict:
    """A faixa de UM conteudo, em palavras de aluno.

    Devolve apenas `faixa` e `ordem`. Nada de accuracy, amostra ou nome de
    banda: o que nao sai daqui nao vaza para a tela por descuido depois.
    """
    politica = thresholds or PerformanceThresholdPolicy.default()
    banda = politica.band(answered=answered, accuracy=accuracy)
    faixa = FAIXA_POR_BANDA.get(banda, FAIXA_CONHECENDO)
    return {"faixa": faixa, "ordem": _ORDEM[faixa]}


def panorama_do_aluno(mapa: dict,
                      thresholds: PerformanceThresholdPolicy | None = None) -> dict:
    """Agrupa os conteudos do mapa de dominio nas faixas.

    Entra o payload de CurriculumDomainMapService.get_map(); sai uma lista de
    faixas com os NOMES dos conteudos. O codigo curricular nao atravessa: ele
    e vocabulario do motor, nao do aluno.

    Faixa sem nenhum conteudo nao aparece - uma linha "Precisa de atenção:
    nenhum" chama atencao justamente para o que nao ha.
    """
    por_faixa: dict[str, list[str]] = {}
    for disciplina in (mapa or {}).get("disciplines", []) or []:
        for conteudo in disciplina.get("contents", []) or []:
            f = faixa_do_aluno(
                answered=conteudo.get("questions_answered") or 0,
                accuracy=conteudo.get("accuracy"),
                thresholds=thresholds,
            )
            nome = conteudo.get("content_name") or conteudo.get("content_code") or "?"
            por_faixa.setdefault(f["faixa"], []).append(nome)

    faixas = [{"faixa": nome, "itens": itens, "ordem": _ORDEM[nome]}
              for nome, itens in por_faixa.items()]
    faixas.sort(key=lambda f: f["ordem"])
    return {"faixas": faixas}


__all__ = [
    "faixa_do_aluno",
    "panorama_do_aluno",
    "FAIXA_POR_BANDA",
    "FAIXA_PRECISA_ATENCAO",
    "FAIXA_EM_DESENVOLVIMENTO",
    "FAIXA_INDO_BEM",
    "FAIXA_CONHECENDO",
]
