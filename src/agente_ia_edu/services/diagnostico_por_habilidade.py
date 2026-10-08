"""Dizer algo mais util que "Estequiometria: precisa de atencao" - QUANDO der.

Cada item do banco diagnostico declara a micro-habilidade que mede
(`diagnostic_skill`). Agregando as respostas por habilidade, as vezes da para
dizer algo melhor:

    "Voce entende a proporcao entre os coeficientes, mas ainda precisa
     praticar a relacao entre mol e massa."

AS VEZES E O ASSUNTO DESTE MODULO
==================================
Tres perguntas por sessao, quatro habilidades: o normal e cada habilidade
receber UMA resposta. Uma resposta nao distingue quem sabe de quem chutou - a
chance de acertar no chute e 1 em 5 - e afirmar "voce entende proporcao" a
partir disso e inventar sobre a pessoa que confiou no diagnostico.

INSUFFICIENT_EVIDENCE continua sendo uma resposta valida. Calar e melhor que
chutar sobre alguem.

NENHUM CORTE NOVO
=================
A amostra minima e `PerformanceThresholdPolicy.min_sample_size`, e as faixas
sao as mesmas. Ha teste lendo a AST deste arquivo que falha se alguem escrever
um numero de corte aqui.

O QUE ELE DEVOLVE
=================
    por_habilidade   o que foi medido, sempre - o professor pode olhar
    suficiente       se da para concluir
    texto            a frase, ou None

`texto` so existe quando ha CONTRASTE: uma habilidade forte e uma fraca, as
duas com amostra suficiente. Sem contraste nao ha o que dizer de especifico -
"voce vai bem em tudo" ja e o feedback geral, e nao precisa desta camada.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from agente_ia_edu.services.pedagogical_analysis import (
    BAND_IMPROVEMENT,
    BAND_INSUFFICIENT,
    BAND_NO_DATA,
    BAND_STRONG,
    PerformanceThresholdPolicy,
)

# Como cada habilidade e dita ao ALUNO. O codigo interno
# (`RELACAO_MASSA_MOL`) nao serve numa frase que ele vai ler.
NOMES = {
    "PROPORCAO_ESTEQUIOMETRICA": "a proporção entre os coeficientes",
    "RELACAO_MOL_MOL": "a conversão entre quantidades de matéria",
    "RELACAO_MASSA_MOL": "a relação entre mol e massa",
    "RELACAO_MASSA_MASSA": "o cálculo completo de massa a massa",
    # Balanceamento, para quando o diagnostico for daquele conteudo.
    "CONSERVACAO_DE_ATOMOS": "a conservação dos átomos",
    "RECONHECER_BALANCEADA": "reconhecer uma equação balanceada",
    "COEFICIENTE_AUSENTE": "encontrar um coeficiente que falta",
    "BALANCEAR_SIMPLES": "balancear uma equação simples",
    "BALANCEAR_MULTIPLAS_ESPECIES": "balancear com várias espécies",
}


def _nome(skill: str) -> str:
    return NOMES.get(skill, skill.replace("_", " ").lower())


def diagnostico_por_habilidade(
    respostas: Sequence[dict] | Iterable[dict],
    *,
    thresholds: PerformanceThresholdPolicy | None = None,
) -> dict:
    """Agrega por micro-habilidade e decide se da para dizer algo.

    ``respostas`` sao dicionarios com ``diagnostic_skill`` e ``is_correct`` -
    o formato que sai dos itens do resultado corrigido.
    """
    politica = thresholds or PerformanceThresholdPolicy.default()

    bruto: dict[str, dict] = {}
    for r in respostas or []:
        skill = (r or {}).get("diagnostic_skill")
        if not skill:
            # Item sem habilidade declarada nao entra na agregacao: contaria
            # como evidencia sobre algo que nao sabemos o que e.
            continue
        d = bruto.setdefault(skill, {"answered": 0, "correct": 0})
        d["answered"] += 1
        d["correct"] += int(bool((r or {}).get("is_correct")))

    por_habilidade: dict[str, dict] = {}
    for skill, d in bruto.items():
        acerto = (d["correct"] / d["answered"]) if d["answered"] else None
        banda = politica.band(answered=d["answered"], accuracy=acerto)
        por_habilidade[skill] = {
            "answered": d["answered"],
            "correct": d["correct"],
            "accuracy": acerto,
            "band": banda,
            "name": _nome(skill),
        }

    conclusivas = {s: v for s, v in por_habilidade.items()
                   if v["band"] not in (BAND_INSUFFICIENT, BAND_NO_DATA)}
    suficiente = bool(conclusivas)

    fortes = [s for s, v in conclusivas.items() if v["band"] == BAND_STRONG]
    fracas = [s for s, v in conclusivas.items() if v["band"] == BAND_IMPROVEMENT]

    texto = None
    if fortes and fracas:
        # Ordem deliberada: o que ele SABE vem primeiro. E ajuda, nao boletim.
        texto = (f"Você entende {_nome(sorted(fortes)[0])}, "
                 f"mas ainda precisa praticar {_nome(sorted(fracas)[0])}.")

    return {
        "por_habilidade": por_habilidade,
        "suficiente": suficiente,
        "texto": texto,
        "policy": politica.as_dict(),
    }


__all__ = ["diagnostico_por_habilidade", "NOMES"]
