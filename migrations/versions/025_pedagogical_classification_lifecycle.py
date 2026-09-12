"""Add ACTIVE/SUPERSEDED lifecycle and supersedes_id to pedagogical_classifications.

Revision ID: 025_classification_lifecycle
Revises: 024_chemistry_kinetics

The revision id must fit Alembic's ``alembic_version.version_num`` column,
which is ``VARCHAR(32)``; the original id
``025_pedagogical_classification_lifecycle`` (40 chars) overflowed it and the
first production apply failed (and rolled back) on the ``UPDATE
alembic_version`` step. The id below is 28 chars. Only the identifier changed
- the migration's schema logic is untouched.

This migration adds the infrastructure needed to safely correct an incorrect
PedagogicalClassification without ever mutating or deleting historical data:

- `lifecycle` (ACTIVE | SUPERSEDED): independent of `status`, which remains a
  quality/review judgement (CLASSIFIED | NEEDS_REVIEW | DRAFT) and is
  untouched by this migration.
- `supersedes_id`: a self-referencing foreign key (ON DELETE RESTRICT) that
  lets a new row record which older row it replaces, without altering that
  older row's substantive data.
- A PostgreSQL partial unique index enforcing at most one ACTIVE row per
  (question_version_id, taxonomy_version). `taxonomy_version` currently lives
  only inside the JSONB `metadata` column, so the index is a JSONB expression
  index (`metadata ->> 'taxonomy_version'`) scoped to `lifecycle = 'ACTIVE'`.
  Rows whose metadata has no `taxonomy_version` key (the pre-taxonomy
  classification pipelines) are naturally excluded from the constraint:
  PostgreSQL/SQLite unique indexes never consider two NULLs equal, so those
  rows can never collide with each other or with taxonomy-scoped rows.

Existing rows are never rewritten beyond this migration's own `lifecycle`
column: `server_default='ACTIVE'` gives every pre-existing row a value with no
manual UPDATE of substantive data, and the one deterministic backfill step
below only ever writes `lifecycle` for rows that would otherwise violate the
new uniqueness rule (i.e. pre-existing accidental duplicates), never anything
else. Nothing is deleted.
"""

from alembic import op
import sqlalchemy as sa


revision = "025_classification_lifecycle"
down_revision = "024_chemistry_kinetics"
branch_labels = None
depends_on = None

TABLE = "pedagogical_classifications"
LIFECYCLE_CHECK = "ck_pedagogical_classifications_lifecycle"
SUPERSEDES_FK = "fk_pedagogical_classifications_supersedes_id"
ACTIVE_UNIQUE_INDEX = "uq_pedagogical_classifications_active_taxonomy"


def _taxonomy_version_expr(dialect_name: str) -> str:
    """PostgreSQL JSONB uses a bare key; SQLite's JSON1 needs a `$.` path."""
    if dialect_name == "postgresql":
        return "(metadata ->> 'taxonomy_version')"
    return "(metadata ->> '$.taxonomy_version')"


def upgrade() -> None:
    connection = op.get_bind()
    dialect = connection.dialect.name
    taxonomy_expr = _taxonomy_version_expr(dialect)

    with op.batch_alter_table(TABLE) as batch_op:
        batch_op.add_column(
            sa.Column(
                "lifecycle",
                sa.String(length=20),
                nullable=False,
                server_default="ACTIVE",
            )
        )
        batch_op.create_check_constraint(
            LIFECYCLE_CHECK, "lifecycle IN ('ACTIVE', 'SUPERSEDED')"
        )

    if dialect == "postgresql":
        # PostgreSQL adds a self-referencing FK directly; no table rebuild.
        op.add_column(TABLE, sa.Column("supersedes_id", sa.Uuid(), nullable=True))
        op.create_foreign_key(
            SUPERSEDES_FK,
            TABLE,
            TABLE,
            ["supersedes_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    else:
        # SQLite only (local/test use - production always runs this migration
        # against PostgreSQL, enforced by db.session.get_database_url).
        # Alembic's batch-mode table rebuild cannot resolve a self-referencing
        # FK's column ordering here (SQLAlchemy raises
        # CircularDependencyError('supersedes_id', ...) inside
        # _adjust_self_columns_for_partial_reordering, a known limitation of
        # batch mode with self-referential foreign keys). The column is still
        # added so lifecycle/uniqueness behaviour is identical under test;
        # only the DB-enforced referential-integrity check on this dialect is
        # skipped.
        with op.batch_alter_table(TABLE) as batch_op:
            batch_op.add_column(sa.Column("supersedes_id", sa.Uuid(), nullable=True))

    # Deterministic, lifecycle-only conflict resolution: if this table already
    # contains more than one row for the same (question_version_id,
    # taxonomy_version) - which the new unique index below would reject - keep
    # only the most recently created one ACTIVE (ties broken by id) and mark
    # every other one SUPERSEDED. No column other than `lifecycle` is ever
    # touched; nothing is deleted.
    connection.execute(
        sa.text(
            f"""
            WITH ranked AS (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY question_version_id, {taxonomy_expr}
                           ORDER BY created_at DESC, id DESC
                       ) AS rn
                FROM {TABLE}
                WHERE {taxonomy_expr} IS NOT NULL
            )
            UPDATE {TABLE}
            SET lifecycle = 'SUPERSEDED'
            WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
            """
        )
    )

    connection.execute(
        sa.text(
            f"CREATE UNIQUE INDEX {ACTIVE_UNIQUE_INDEX} "
            f"ON {TABLE} (question_version_id, {taxonomy_expr}) "
            f"WHERE lifecycle = 'ACTIVE'"
        )
    )


def downgrade() -> None:
    connection = op.get_bind()
    dialect = connection.dialect.name
    op.execute(sa.text(f"DROP INDEX IF EXISTS {ACTIVE_UNIQUE_INDEX}"))

    if dialect == "postgresql":
        op.drop_constraint(SUPERSEDES_FK, TABLE, type_="foreignkey")
        op.drop_column(TABLE, "supersedes_id")
    else:
        with op.batch_alter_table(TABLE) as batch_op:
            batch_op.drop_column("supersedes_id")

    with op.batch_alter_table(TABLE) as batch_op:
        batch_op.drop_constraint(LIFECYCLE_CHECK, type_="check")
        batch_op.drop_column("lifecycle")
