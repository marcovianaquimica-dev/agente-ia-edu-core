"""Sinal de confiabilidade de ENTRADA - deliberadamente separado de
julgamento pedagogico (spec Fase B). Nunca decide isolado: ver
combine_input_reliability_signals."""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from importlib import resources

# Larga o suficiente para nao reagir a nomes proprios, estrangeirismos,
# abreviacoes e erros ortograficos reais do aluno - so pega corrupcao
# grosseira (spec Fase B, Global Constraints).
_HEURISTIC_UNRELIABLE_THRESHOLD = 0.4
_OCR_UNRELIABLE_THRESHOLD = 0.6  # mesmo piso de essay_submission.py::_MIN_AVERAGE_CONFIDENCE

_TOKEN_PATTERN = re.compile(r"[a-zA-ZàáâãéêíóôõúçÀÁÂÃÉÊÍÓÔÕÚÇ]+")


def _normalize(word: str) -> str:
    decomposed = unicodedata.normalize("NFKD", word.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


@lru_cache(maxsize=1)
def _common_words() -> frozenset[str]:
    text = resources.files("agente_ia_edu.data").joinpath("pt_common_words.txt").read_text(encoding="utf-8")
    return frozenset(_normalize(line.strip()) for line in text.splitlines() if line.strip())


def estimate_text_reliability_heuristic(text: str) -> float:
    """Proporcao de tokens reconheciveis como portugues comum - UM sinal de
    entrada entre varios, nunca usado isolado (ver combine_input_reliability_
    signals) e nunca lido pelo julgamento de C1 ou qualquer competencia."""
    tokens = _TOKEN_PATTERN.findall(text)
    if not tokens:
        return 1.0  # sem tokens alfabeticos (ex. so numeros/pontuacao) - nao e questao desta heuristica
    words = _common_words()
    recognizable = sum(1 for token in tokens if _normalize(token) in words)
    return recognizable / len(tokens)


def combine_input_reliability_signals(
    *, heuristic_ratio: float | None, model_status: str, ocr_average_confidence: float | None,
) -> str:
    """Combina os sinais disponiveis - nunca decide por uma fonte isolada
    (spec Fase B). model_status vem da autoavaliacao do modelo na mesma
    chamada de fase 1 (campo input_reliability do contrato v6); e a UNICA
    fonte que pode, por si so, levar a UNRELIABLE_NEEDS_REVIEW - um sinal
    tecnico discordando de um modelo confiante so rebaixa RELIABLE para
    USABLE_WITH_WARNING, nunca forca o nivel mais severo isolado."""
    if model_status == "UNRELIABLE_NEEDS_REVIEW":
        return "UNRELIABLE_NEEDS_REVIEW"
    if model_status == "USABLE_WITH_WARNING":
        return "USABLE_WITH_WARNING"

    technical_signal_bad = (
        (heuristic_ratio is not None and heuristic_ratio < _HEURISTIC_UNRELIABLE_THRESHOLD)
        or (ocr_average_confidence is not None and ocr_average_confidence < _OCR_UNRELIABLE_THRESHOLD)
    )
    return "USABLE_WITH_WARNING" if technical_signal_bad else "RELIABLE"
