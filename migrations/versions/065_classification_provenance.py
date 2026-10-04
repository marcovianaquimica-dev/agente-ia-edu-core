"""Proveniencia da classificacao curricular: quem aprovou, e com que direito.

Revision ID: 065_classification_provenance
Revises: 064_study_session_readiness

ADITIVA. Duas colunas e uma trava em ``pedagogical_classifications``.

O QUE FALTAVA
=============

A tabela ja guardava proveniencia tecnica rica - model_name, model_version,
prompt_version, provider_name, tokens - e ``pedagogical_classification_reviews``
ja distingue ``actor_type IN ('AI','TEACHER','COORDINATOR',...)``.

O que nao existia era o STATUS dizer quem aprovou. ``status='CLASSIFIED'``
significava "utilizavel" tanto para uma linha que um professor conferiu
quanto para uma que uma regra escreveu sozinha. Para saber a diferenca era
preciso caminhar a trilha de auditoria - ou seja, na pratica ninguem sabia.

Com a decisao de produto de 2026-10-04 (IA classifica, IA verifica, o sistema
aprova sozinho quando ha evidencia suficiente), essa indistincao deixa de ser
um detalhe: passa a existir um volume grande de material aprovado sem humano
nenhum, e misturar isso com o que um professor conferiu apagaria a unica
informacao que permite auditar a decisao depois.

AS TRES PROVENIENCIAS
=====================

    AI_SUGGESTED     a IA propos. Nao e utilizavel sozinha.
    AI_VERIFIED      a IA propos e uma verificacao INDEPENDENTE concordou,
                     sob um contrato estrutural. Utilizavel.
    HUMAN_VALIDATED  uma pessoa conferiu. Utilizavel.

``REQUIRES_REVIEW`` nao entra aqui: ele e um estado de FILA, nao de
proveniencia, e o campo ``status`` ja o representa com ``NEEDS_REVIEW``.
Criar um quarto valor so para repeti-lo daria duas fontes para a mesma
pergunta.

A TRAVA, E POR QUE ELA E O PONTO DESTA MIGRATION
=================================================

    CHECK (provenance <> 'HUMAN_VALIDATED'
           OR validated_by_external_identity IS NOT NULL)

Mesma forma que ``curriculum_bncc_links`` ja usa para ``status='VALIDATED'``.
Sem ela, nada impediria um backfill distraido - ou um servico com pressa -
de carimbar HUMAN_VALIDATED em material que nenhuma pessoa olhou. O banco
passa a tornar esse estado irrepresentavel, em vez de confiar que todo
codigo futuro vai lembrar da regra.

Repare no que a trava NAO faz: ela nao exige identidade para AI_VERIFIED.
Verificacao automatica e legitima e tem proveniencia propria - o que ela nao
pode e se disfarcar de validacao humana.

BACKFILL
========

As linhas existentes recebem AI_SUGGESTED, que e o que elas de fato sao: o
classificador antigo escrevia ``source='ai'`` e nenhuma passou por
verificacao independente. Nao promovo nada a AI_VERIFIED nesta migration -
promover sem ter verificado seria exatamente a mentira que a coluna existe
para impedir.
"""

import sqlalchemy as sa
from alembic import op

revision = "065_classification_provenance"
down_revision = "064_study_session_readiness"
branch_labels = None
depends_on = None

PROVENIENCIAS = ("AI_SUGGESTED", "AI_VERIFIED", "HUMAN_VALIDATED")


def upgrade() -> None:
    op.add_column(
        "pedagogical_classifications",
        sa.Column("provenance", sa.String(20), nullable=False,
                  server_default="AI_SUGGESTED"),
    )
    op.add_column(
        "pedagogical_classifications",
        # String, nao FK: a identidade e externa (mesmo padrao de
        # curriculum_bncc_links.validated_by_external_identity). Apagar um
        # usuario nao pode apagar o fato de que ele validou.
        sa.Column("validated_by_external_identity", sa.String(255), nullable=True),
    )
    op.create_check_constraint(
        "ck_pedagogical_classifications_provenance",
        "pedagogical_classifications",
        sa.text(
            "provenance IN ('AI_SUGGESTED', 'AI_VERIFIED', 'HUMAN_VALIDATED')"
        ),
    )
    op.create_check_constraint(
        # A trava que importa.
        "ck_pedagogical_classifications_human_needs_identity",
        "pedagogical_classifications",
        sa.text(
            "provenance <> 'HUMAN_VALIDATED' "
            "OR validated_by_external_identity IS NOT NULL"
        ),
    )
    op.create_index(
        # "o que esta utilizavel e como foi aprovado" - a consulta de auditoria
        # e a da selecao de questoes.
        "ix_pedagogical_classifications_provenance_status",
        "pedagogical_classifications",
        ["provenance", "status", "lifecycle"],
    )


def downgrade() -> None:
    op.drop_index("ix_pedagogical_classifications_provenance_status",
                  table_name="pedagogical_classifications")
    op.drop_constraint("ck_pedagogical_classifications_human_needs_identity",
                       "pedagogical_classifications", type_="check")
    op.drop_constraint("ck_pedagogical_classifications_provenance",
                       "pedagogical_classifications", type_="check")
    op.drop_column("pedagogical_classifications", "validated_by_external_identity")
    op.drop_column("pedagogical_classifications", "provenance")
