"""Reconcilia as duas linhagens que bifurcaram apos 064_essay_batch_extracted_text.

Revision ID: 069_merge_essay_vetorial
Revises: 066_essay_zero_gate, 068_dialogue_continuity

SEM DDL. Esta revisao nao cria, altera nem apaga nada - ela existe apenas
para reunir dois ramos de historico que correram em paralelo, seguindo
exatamente o mesmo raciocinio e o mesmo precedente de
``066_merge_063_lineages`` (que reconciliou a bifurcacao anterior, em
``055_essay_prompt_soft_delete``).

O QUE ACONTECEU
===============

O branch ``fase6/vetorial`` (Knowledge Engine/CEREBRO) reconciliou sua
propria bifurcacao interna em ``066_merge_063_lineages``, ancorando esse
merge em ``063_platform_material_target`` - que era, naquele momento, o
head real do lado "plataforma". Mas o lado plataforma avancou mais um
passo depois disso, com ``064_essay_batch_extracted_text``, que nunca foi
incorporado por ``fase6/vetorial``. Este branch (motor de correcao de
redacao - Quality Gate/Zero Gate/calibracao, PR #19) encadeou normalmente a
partir desse ``064``, com ``065_essay_quality_gate`` e
``066_essay_zero_gate``.

Resultado: uma SEGUNDA bifurcacao, desta vez em
``064_essay_batch_extracted_text``, com dois heads finais:

    063_platform_material_target
     |
     +-- 064_essay_batch_extracted_text   -- este branch (motor de redacao)
     |   065_essay_quality_gate
     |   066_essay_zero_gate
     |
     +-- [066_merge_063_lineages, que por sua vez reune 063_platform_material_target
     |    com 065_classification_provenance - ver aquela revisao para o
     |    detalhe completo da bifurcacao ANTERIOR, em 055]
         067_guided_practice
         068_dialogue_continuity              -- fase6/vetorial
     |
    069_merge_essay_vetorial   (esta revisao)

POR QUE UM MERGE E A FORMA CERTA AQUI
======================================

As duas cadeias tocam conjuntos de tabelas DISJUNTOS. Verificado tabela a
tabela, incluindo as migrations JA incorporadas por cada lado (nao so as
mais recentes):

    ramo motor de redacao   essay_corrections (colunas
                            quality_gate_status/quality_gate_version em
                            065, zero_gate_decision/zero_gate_version em
                            066) - nenhuma tabela nova, so colunas
                            adicionadas numa tabela que o outro ramo nunca
                            toca.

    ramo fase6/vetorial    study_sessions (064), pedagogical_classifications
                            (065), guided_practice_items (067, nova tabela),
                            guided_practice_items de novo (068, ja
                            coberto acima pelo merge 066_merge_063_lineages).

    intersecao              NENHUMA

Nenhuma das duas desfaz o que a outra fez, e nenhuma depende da outra.
Confirmado tambem no nivel de codigo de aplicacao: o merge dos dois branches
teve 4 conflitos reais (todos em arquivos de frontend compartilhados -
``providers/adapters/openai.py``, ``web/coordination.html``,
``web/essay-review.js``, ``web/teacher.css`` - nunca em migrations nem em
modelos de banco), resolvidos preservando as funcionalidades dos dois lados
(ver mensagem do commit de merge para o detalhe de cada um).

O que NAO foi feito, de proposito: renumerar migrations, reescrever
``down_revision`` das revisoes existentes (nem as deste branch, nem as de
``fase6/vetorial``), ou escolher um dos dois heads como "o certo". Qualquer
uma dessas reescreveria historico que outras sessoes/ambientes podem ja ter
aplicado.

O QUE ACONTECE COM CADA INSTALACAO
===================================

    banco vazio                       aplica os dois ramos, em qualquer
                                      ordem que o Alembic escolher, e chega
                                      aqui
    banco no ramo motor de redacao    aplica os migrations de
                                      fase6/vetorial a partir de
                                      063_platform_material_target e chega
                                      aqui
    banco no ramo fase6/vetorial      aplica 064_essay_batch_extracted_text,
                                      065_essay_quality_gate e
                                      066_essay_zero_gate e chega aqui

Os tres convergem para o mesmo schema. Validado em
``agente_ia_edu_integration_redacao_vetorial`` (banco Postgres descartavel,
nunca o banco compartilhado), aplicando a cadeia completa desde uma base
limpa.

SOBRE O DOWNGRADE
=================

Descer desta revisao devolve o grafo a dois heads, que e exatamente o
estado anterior. Nao ha DDL para desfazer.
"""

from typing import Sequence, Union

revision: str = "069_merge_essay_vetorial"
down_revision: Union[str, Sequence[str], None] = (
    "066_essay_zero_gate",
    "068_dialogue_continuity",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Sem DDL: esta revisao so reune os dois ramos."""


def downgrade() -> None:
    """Sem DDL: descer daqui apenas devolve o grafo a dois heads."""
