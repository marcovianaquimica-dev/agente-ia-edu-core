"""CEREBRO / Fase 4 - curadoria do vinculo curriculo <-> BNCC.

Revision ID: 060_curriculum_bncc_links
Revises: 059_partial_extraction_status

Duas tabelas novas. Puramente aditiva: nenhuma tabela existente e tocada.

A Fase 0 mediu que similaridade textual NAO serve para criar esta relacao -
"estequiometria", "reagente limitante" e "diluicao" nao aparecem uma unica vez
no texto da area de Ciencias da Natureza, e "solucoes" aparece em *"propor
solucoes"*. Por isso o vinculo e curado, e por isso as travas estao aqui e nao
num service:

    CHECK (status <> 'VALIDATED' OR validated_by_external_identity IS NOT NULL)
    CHECK (status <> 'VALIDATED' OR rationale IS NOT NULL)

Uma sugestao de IA nao consegue virar relacao validada: validar exige
identidade humana e justificativa registradas, e o banco recusa o contrario.

ATENCAO ao tamanho do id de revisao: ``alembic_version.version_num`` e
VARCHAR(32), e "060_curriculum_bncc_links" tem 25. Ver
``tests/test_migration_revision_ids.py``, que existe porque a migracao 059
nasceu com 38 caracteres e derrubou 46 testes.
"""

import sqlalchemy as sa
from alembic import op

from agente_ia_edu.db.types import JSONBCompatible

revision = "060_curriculum_bncc_links"
down_revision = "059_partial_extraction_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "curriculum_bncc_links",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("content_node_id", sa.Uuid(), nullable=False),
        sa.Column("taxonomy_node_id", sa.Uuid(), nullable=False),
        sa.Column("taxonomy_id", sa.Uuid(), nullable=False),
        sa.Column("relation_type", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PROPOSED"),
        sa.Column("origin", sa.String(20), nullable=False, server_default="MANUAL"),
        sa.Column("confidence", sa.Numeric(4, 3)),
        sa.Column("rationale", sa.Text()),
        sa.Column("validated_by_external_identity", sa.String(255)),
        sa.Column("validated_at", sa.DateTime(timezone=True)),
        sa.Column("supersedes_id", sa.Uuid()),
        sa.Column("proposed_by_external_identity", sa.String(255)),
        sa.Column("metadata", JSONBCompatible()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["content_node_id"], ["catalog_nodes.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["taxonomy_node_id"], ["taxonomy_nodes.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["taxonomy_id"], ["taxonomies.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["supersedes_id"], ["curriculum_bncc_links.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            "relation_type IN ('PRIMARY', 'SUPPORTING')",
            name="ck_curriculum_bncc_links_relation_type",
        ),
        sa.CheckConstraint(
            "status IN ('PROPOSED', 'VALIDATED', 'REJECTED', 'SUPERSEDED')",
            name="ck_curriculum_bncc_links_status",
        ),
        sa.CheckConstraint(
            "origin IN ('MANUAL', 'AI_SUGGESTION', 'IMPORT')",
            name="ck_curriculum_bncc_links_origin",
        ),
        sa.CheckConstraint(
            "status <> 'VALIDATED' OR validated_by_external_identity IS NOT NULL",
            name="ck_curriculum_bncc_links_validated_needs_identity",
        ),
        sa.CheckConstraint(
            "status <> 'VALIDATED' OR rationale IS NOT NULL",
            name="ck_curriculum_bncc_links_validated_needs_rationale",
        ),
        sa.CheckConstraint(
            "origin <> 'AI_SUGGESTION' OR confidence IS NOT NULL",
            name="ck_curriculum_bncc_links_suggestion_needs_confidence",
        ),
    )
    op.create_index(
        "uq_curriculum_bncc_links_validated",
        "curriculum_bncc_links",
        ["content_node_id", "taxonomy_node_id", "taxonomy_id"],
        unique=True,
        postgresql_where=sa.text("status = 'VALIDATED'"),
        sqlite_where=sa.text("status = 'VALIDATED'"),
    )
    op.create_index(
        "ix_curriculum_bncc_links_content_node_id",
        "curriculum_bncc_links",
        ["content_node_id"],
    )
    op.create_index(
        "ix_curriculum_bncc_links_taxonomy_node_id",
        "curriculum_bncc_links",
        ["taxonomy_node_id"],
    )
    op.create_index(
        "ix_curriculum_bncc_links_status", "curriculum_bncc_links", ["status"]
    )

    op.create_table(
        "curriculum_bncc_link_reviews",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("link_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("actor_type", sa.String(20), nullable=False),
        sa.Column("previous_value", JSONBCompatible()),
        sa.Column("new_value", JSONBCompatible()),
        sa.Column("reason", sa.Text()),
        sa.Column("suggester_version", sa.String(100)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["link_id"], ["curriculum_bncc_links.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            "action IN ('PROPOSE', 'VALIDATE', 'REJECT', 'SUPERSEDE')",
            name="ck_curriculum_bncc_link_reviews_action",
        ),
        sa.CheckConstraint(
            "actor_type IN ('AI', 'TEACHER', 'COORDINATOR', 'DIRECTOR', "
            "'PLATFORM_ADMIN', 'SYSTEM')",
            name="ck_curriculum_bncc_link_reviews_actor_type",
        ),
        sa.CheckConstraint(
            "actor_type <> 'AI' OR action = 'PROPOSE'",
            name="ck_curriculum_bncc_link_reviews_ai_only_proposes",
        ),
    )
    op.create_index(
        "ix_curriculum_bncc_link_reviews_link_id",
        "curriculum_bncc_link_reviews",
        ["link_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_curriculum_bncc_link_reviews_link_id",
        table_name="curriculum_bncc_link_reviews",
    )
    op.drop_table("curriculum_bncc_link_reviews")
    op.drop_index("ix_curriculum_bncc_links_status", table_name="curriculum_bncc_links")
    op.drop_index(
        "ix_curriculum_bncc_links_taxonomy_node_id", table_name="curriculum_bncc_links"
    )
    op.drop_index(
        "ix_curriculum_bncc_links_content_node_id", table_name="curriculum_bncc_links"
    )
    op.drop_index(
        "uq_curriculum_bncc_links_validated", table_name="curriculum_bncc_links"
    )
    op.drop_table("curriculum_bncc_links")
