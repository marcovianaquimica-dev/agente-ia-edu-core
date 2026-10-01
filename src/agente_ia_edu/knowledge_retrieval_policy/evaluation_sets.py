"""Consultas de CALIBRACAO e de AVALIACAO - conjuntos separados e congelados.

Por que isto e um modulo versionado, e nao uma lista num script
=============================================================

Ajustar ``heading_weight`` olhando as mesmas cinco consultas com que depois
declaramos a qualidade do sistema e treinar no conjunto de teste. O numero
sobe, a medicao perde sentido, e ninguem percebe - porque o relatorio mostra
exatamente as consultas que foram otimizadas.

``EVALUATION_SET_V1`` e CONGELADO. Sera reusado, sem ajuste dirigido, quando
compararmos Lexical v1 x Vector v1 x Hybrid v1 - e uma comparacao so vale se
a regua nao mudar entre as medicoes. Alterar esta tupla quebra
``tests/test_knowledge_retrieval_policy.py`` de proposito: um conjunto de
avaliacao que pode ser editado silenciosamente nao e conjunto de avaliacao.

``CALIBRATION_SET_V1`` existe para os A/B de constante. Os dois conjuntos sao
disjuntos APOS NORMALIZACAO, nao apenas como texto - "diluicao" e "diluicoes"
sao a mesma consulta para o indice, e um teste verifica isso.
"""

from __future__ import annotations

#: As cinco consultas aprovadas para a Fase 5. Congeladas.
EVALUATION_SET_V1: tuple[str, ...] = (
    "estequiometria",
    "reagente limitante",
    "mol",
    "diluição",
    "concentração das soluções",
)

#: Consultas de calibracao: cobrem os mesmos FORMATOS do Evaluation Set
#: (termo unico, expressao de duas palavras, expressao com palavra de funcao,
#: termo muito comum, termo raro) sem repetir conceito algum dele.
CALIBRATION_SET_V1: tuple[str, ...] = (
    "massa molar",
    "tabela periódica",
    "ligação iônica",
    "número de Avogadro",
    "balanceamento de equações",
    "ácidos e bases",
    "entalpia",
    "átomo",
)
