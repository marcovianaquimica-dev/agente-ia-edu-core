"""Conjuntos de consultas da perna VETORIAL - congelados.

Separados dos lexicais pela mesma disciplina da Fase 5, e pela mesma razao:
ajustar constante olhando as consultas com que depois se declara qualidade e
treinar no conjunto de teste.

``EVALUATION_SET_V1`` lexical permanece **congelado e intocado**. Estes sao
outros conjuntos, para outra pergunta.

O QUE ESTE CONJUNTO MEDE, E QUE O LEXICAL NAO MEDE
==================================================

A Fase 5.1 fechou com media P@10 de 7,4, e as duas consultas abaixo da meta
falharam por motivos **semanticos**, nao editoriais. A mais explicita:
``reagente limitante`` trouxe, no rank 10, um texto sobre *"fator limitante
para a vida de especies aquaticas"* - a mesma palavra, outro sentido. Busca
lexical nao distingue sentido.

Entao este conjunto e feito de **parafrase** e de **ambiguidade**:

- consultas 1 a 6 descrevem um conceito **sem usar o termo tecnico**. Um
  indice invertido nao tem como acerta-las; se o vetor tambem nao acertar, o
  vetor nao esta cumprindo a sua funcao.
- as consultas 7 e 8 sao o **par de ambiguidade**: o mesmo adjetivo em dois
  dominios. O criterio esta em ``AMBIGUITY_PAIR``.
- a 9 so e respondivel pela BNCC, que tem 23 chunks contra ~5.900 - mede se
  ``CURRICULUM_ITEM`` e alcancavel sem boost algum.
- a 10 e **controle negativo**: assunto fora do recorte do corpus. Nao existe
  criterio binario de "pontuar baixo" aqui - a Fase 6 REGISTRA a distribuicao
  de scores e nao inventa limiar de abstencao. Qualquer limiar futuro nascera
  do Calibration Set, nunca deste conjunto.
"""

from __future__ import annotations

#: Congelado. Alterar esta tupla quebra ``tests/test_vector_evaluation_sets.py``
#: de proposito.
VECTOR_EVALUATION_SET_V1: tuple[str, ...] = (
    "o que sobra quando um dos reagentes acaba primeiro",
    "como saber qual substância acaba antes numa reação química",
    "por que adicionar água deixa a solução mais fraca",
    "quantas partículas existem numa amostra de uma substância",
    "relação entre a massa de uma substância e o número de partículas",
    "preparar uma solução partindo de outra mais concentrada",
    "reagente limitante",
    "fator limitante para a vida de espécies aquáticas",
    "habilidade sobre transformações e conservações em sistemas",
    "fotossíntese nas plantas",
)

#: As duas consultas do par de ambiguidade, por indice no conjunto.
AMBIGUITY_PAIR: tuple[str, str] = (
    VECTOR_EVALUATION_SET_V1[6],
    VECTOR_EVALUATION_SET_V1[7],
)

#: Sobreposicao maxima tolerada entre os top-10 das duas consultas do par.
#: Declarado ANTES de medir. Se for excedido, o resultado e registrado como
#: "o embedding nao resolveu a ambiguidade" - nao e motivo para ajustar nada.
AMBIGUITY_MAX_OVERLAP = 2

#: Consulta de controle negativo - assunto fora do recorte do corpus.
NEGATIVE_CONTROL: str = VECTOR_EVALUATION_SET_V1[9]

#: Consultas de calibracao. Mesmos FORMATOS do conjunto de avaliacao
#: (parafrase longa, termo tecnico curto, par de dominios) sem repetir
#: conceito algum dele. E aqui que qualquer constante futura se calibra.
VECTOR_CALIBRATION_SET_V1: tuple[str, ...] = (
    "o que faz uma reação acontecer mais depressa",
    "como os átomos se unem para formar substâncias",
    "por que algumas substâncias conduzem corrente elétrica dissolvidas",
    "energia liberada ou absorvida numa transformação",
    # "tabela periodica" NAO entra: ja e consulta do Calibration Set lexical,
    # e reusar a mesma consulta nos dois lados tornaria as calibracoes
    # dependentes uma da outra.
    "oxidação e redução",
    "corrente elétrica em metais",
    "o que acontece com a matéria quando ela muda de estado",
    "equilíbrio químico",
)
