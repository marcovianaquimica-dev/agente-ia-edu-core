"""Add is_correct to extracted_question_options; fix publish_run answer-key bug.

Found live: 2026-09-25, while investigating whether the PHASE 27/28/29
Question Extraction Engine could serve a real book import
(``quimica 1.pdf``, ingested via AuthorialMaterialIngestionService against
the dev Postgres). Full read of the pipeline (``question_extraction_service.
py``, ``question_publication_service.py``, ``db/models/question_extraction.
py``) confirmed ``ExtractedQuestion``/``ExtractedQuestionOption`` have NO
concept of a correct answer anywhere - not in the model, not in the
extraction engine, not in the review/edit endpoint (``PATCH
/questions/{id}``, whose ``OptionInput`` schema only carries
``label``/``text``).

Consequence: ``QuestionPublicationService.publish_run()`` creates each
official ``QuestionOption`` without passing ``is_valid_option`` at all -
and that column's default is ``True`` (``db/models/official.py``), so
EVERY option of EVERY multiple-choice question ever published through this
engine is marked valid/correct, not exactly one. No existing test asserts
on ``is_valid_option`` for a published question, so this was a live,
untested blind spot in already-merged code (PHASE 29, migration 037).

This migration adds the missing column. The application-layer fix (require
exactly one ``is_correct=True`` option before a multiple-choice question
can be approved; copy it to ``is_valid_option`` at publish time) lands in
the same change as this migration.

Purely additive: new nullable-by-default-false column, zero existing rows
affected in meaning (every pre-existing ``ExtractedQuestionOption`` simply
reads as "not yet marked correct", which is honestly what was true before
this column existed).
"""

from alembic import op
import sqlalchemy as sa

revision = "051_extracted_option_correct"
down_revision = "050_resource_grantee_fix"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "extracted_question_options",
        sa.Column("is_correct", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("extracted_question_options", "is_correct")
