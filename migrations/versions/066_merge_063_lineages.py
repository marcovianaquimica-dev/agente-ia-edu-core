"""Reconcilia as duas linhagens que bifurcaram em 055.

Revision ID: 066_merge_063_lineages
Revises: 063_platform_material_target, 065_classification_provenance

SEM DDL. Esta revisao nao cria, altera nem apaga nada - ela existe apenas
para reunir dois ramos de historico que correram em paralelo.

O QUE ACONTECEU
===============

O sintoma aparecia como "duas migrations com o numero 063". A forense mostrou
que a bifurcacao e bem mais antiga: os dois ramos se separam em
``055_essay_prompt_soft_delete`` e seguem SETE e NOVE migrations em paralelo,
reusando os numeros 057 a 063.

    055_essay_prompt_soft_delete
     |
     +-- 057_essay_batch_upload          -- ramo "plataforma/redacao" (main)
     |   058_platform_essay_prompts
     |   059_mass_correction_runs
     |   060_mass_correction_cancelling
     |   061_essay_batch_scope
     |   062_prompt_assignment_target
     |   063_platform_material_target
     |
     +-- 057_knowledge_engine_corpus     -- ramo "CEREBRO/aluno" (fase6/vetorial)
         058_knowledge_engine_embeddings
         059_partial_extraction_status
         060_curriculum_bncc_links
         061_lexical_index
         062_editorial_role
         063_embedding_activation
         064_study_session_readiness
         065_classification_provenance
     |
    066_merge_063_lineages   (esta revisao)

Nao existe 056 em nenhum dos lados: o numero foi pulado.

POR QUE UM MERGE E A FORMA CERTA AQUI
======================================

As duas cadeias tocam conjuntos de tabelas DISJUNTOS. Verificado tabela a
tabela:

    ramo plataforma   essay_batch_uploads, essay_batch_pages,
                      platform_essay_prompts, essay_prompts,
                      mass_correction_runs, prompt_assignments,
                      prompt_assignment_logs, prompt_materials, schools

    ramo CEREBRO      knowledge_sources, knowledge_documents,
                      knowledge_chunks, knowledge_chunk_terms,
                      knowledge_chunk_embeddings, knowledge_embedding_spaces,
                      knowledge_chunk_lexical_index,
                      knowledge_lexical_index_state,
                      knowledge_embedding_activations,
                      curriculum_bncc_links, curriculum_bncc_link_reviews,
                      study_sessions, pedagogical_classifications

    intersecao        NENHUMA

Nenhuma das duas desfaz o que a outra fez, e nenhuma depende da outra. Sao
dois trabalhos independentes que por acaso compartilharam numeros. Por isso o
merge do Alembic - uma revisao com dois ``down_revision`` - descreve a
historia REAL, em vez de inventar uma ordem linear que nunca existiu.

O que NAO foi feito, de proposito: renumerar migrations, reescrever
``down_revision`` das revisoes existentes, ou escolher uma das 063 como "a
certa". Qualquer uma dessas reescreveria historico que ja rodou em banco de
verdade.

O QUE ACONTECE COM CADA INSTALACAO
===================================

    banco vazio                 aplica os dois ramos, em qualquer ordem que o
                                Alembic escolher, e chega aqui
    banco no ramo plataforma    aplica os nove do ramo CEREBRO e chega aqui
    banco no ramo CEREBRO       aplica os sete do ramo plataforma e chega aqui

Os tres convergem para o mesmo schema. Ha teste provando isso em PostgreSQL
descartavel, comparando as tabelas e colunas finais dos tres caminhos.

SOBRE O DOWNGRADE
=================

Descer desta revisao devolve o grafo a dois heads, que e exatamente o estado
anterior. Nao ha DDL para desfazer.
"""

from typing import Sequence, Union

revision: str = "066_merge_063_lineages"
down_revision: Union[str, Sequence[str], None] = (
    "063_platform_material_target",
    "065_classification_provenance",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Sem DDL: esta revisao so reune os dois ramos."""


def downgrade() -> None:
    """Sem DDL: descer daqui apenas devolve o grafo a dois heads."""
