"""Classificacao de desempenho por faixa de nota - deterministico, sem IA."""
from __future__ import annotations

FAIXAS = [
    (0, 399, "Muito baixo"),
    (400, 599, "Baixo"),
    (600, 799, "Adequado"),
    (800, 899, "Alto"),
    (900, 1000, "Muito alto"),
]


def classificar(nota: int) -> str:
    for minimo, maximo, rotulo in FAIXAS:
        if minimo <= nota <= maximo:
            return rotulo
    raise ValueError(f"nota fora do intervalo esperado (0-1000): {nota}")
