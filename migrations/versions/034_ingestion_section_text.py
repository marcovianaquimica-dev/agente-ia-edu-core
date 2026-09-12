"""PHASE 26 - Authorial Material Ingestion Engine.

Revision ID: 034_ingestion_section_text
Revises: 033_ingestion_material_review

ONE additive, nullable column on the EXISTING ``ingestion_sections`` table
(unchanged since PHASE 3): ``content_text``. ``content_preview`` already on
that table is intentionally a short (500-char) summary - the official/ENEM
ingestion pipeline never needed the full body text, only a preview for
traceability. Authorial material publication (PHASE 26) does need the real
extracted text to build MaterialSection/MaterialBlock rows from (spec s9:
"Preservar: conteúdo"). Rather than recreate ``ingestion_sections`` or add a
second sections table, this ESTENDS it with one nullable TEXT column that
the existing/gated ENEM pipeline never writes and is therefore completely
unaffected by. Fully reversible; touches no other table.
"""

from alembic import op
import sqlalchemy as sa

revision = "034_ingestion_section_text"
down_revision = "033_ingestion_material_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ingestion_sections", sa.Column("content_text", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("ingestion_sections", "content_text")
