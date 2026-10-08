"""Consultas de SANIDADE - nunca de avaliacao. Fase 6, passo 5.

PARA QUE SERVEM, E PARA QUE NAO SERVEM
======================================

Servem para provar que a infraestrutura REAL funciona depois do primeiro
backfill pago: que o provider responde, que o vetor da consulta vive no mesmo
espaco dos chunks, que o pgvector ordena, que os filtros e os direitos valem
no corpus de verdade e que a latencia e aceitavel.

NAO servem para medir qualidade. Nenhum numero daqui autoriza ajustar
constante alguma, e nenhuma destas consultas pode migrar para Calibration ou
Evaluation depois.

POR QUE A EXCLUSAO E ESTRUTURAL
===============================

Porque a tentacao e real e silenciosa: uma consulta sanitaria que "deu bom
resultado" vira, meses depois, uma consulta de benchmark - e o benchmark
passa a medir a concordancia do sistema com o que ja se sabia que ele
achava. ``tests/test_sanity_queries.py`` quebra se qualquer destas aparecer
em ``EVALUATION_SET_V1``, ``CALIBRATION_SET_V1``,
``VECTOR_EVALUATION_SET_V1`` ou ``VECTOR_CALIBRATION_SET_V1``.

ESCOLHA DOS ASSUNTOS
====================

Sete temas de quimica do ensino medio que NAO tocam nenhum conceito dos
conjuntos congelados - nem por sinonimo, nem por parafrase. Fora de cogitacao
ficaram estequiometria, reagente limitante, mol e massa molar, diluicao e
concentracao, numero de Avogadro, tabela periodica, ligacao ionica,
balanceamento, acidos e bases, entalpia, atomo, cinetica, eletrolitos,
oxirreducao, conducao em metais, mudanca de estado, equilibrio quimico e
fotossintese.

O que sobrou - separacao de misturas, radioatividade, organica, gases,
atmosfera, polimeros e isotopos - e materia presente nas tres obras do
piloto, e portanto capaz de mostrar que a recuperacao funciona de ponta a
ponta.
"""

from __future__ import annotations

#: Marcador explicito. Quem ler uma destas numa lista sabe o que ela e.
SANITY_ONLY = "SANITY_ONLY"

#: Nunca entram em Calibration nem em Evaluation. O teste garante.
SANITY_ONLY_QUERIES: tuple[str, ...] = (
    "como separar os componentes de uma mistura",
    "radioatividade e decaimento nuclear",
    "cadeias carbônicas e funções orgânicas",
    "comportamento dos gases e suas leis",
    "efeito estufa e gases poluentes",
    "polímeros e materiais plásticos",
    "isótopos de um mesmo elemento químico",
)
