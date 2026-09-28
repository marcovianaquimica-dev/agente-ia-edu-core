"""Add FILE as an allowed essay-prompt material type.

Revision ID: 054_prompt_material_file_type
Revises: 053_essay_token_usage

Teachers can now attach any document (not just an IMAGE storage_uri) as a
"texto motivador" for an essay prompt, via a real upload endpoint (see
api/routes/essay_prompts.py's new /materials/upload route and
services/material_storage.py's existing content-addressed local storage,
already used by student essay-submission uploads). FILE behaves exactly
like IMAGE at the database level - it requires storage_uri, never content -
so this only widens the two existing CHECK constraints on prompt_materials,
it does not add a column or touch any existing row.
"""

from alembic import op

revision = "054_prompt_material_file_type"
down_revision = "053_essay_token_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_prompt_materials_type", "prompt_materials", type_="check")
    op.create_check_constraint(
        "ck_prompt_materials_type", "prompt_materials",
        "material_type IN ('TEXT', 'IMAGE', 'FILE')",
    )
    op.drop_constraint("ck_prompt_materials_image_has_storage_uri", "prompt_materials", type_="check")
    op.create_check_constraint(
        "ck_prompt_materials_image_has_storage_uri", "prompt_materials",
        "(material_type IN ('IMAGE', 'FILE')) = (storage_uri IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_prompt_materials_image_has_storage_uri", "prompt_materials", type_="check")
    op.create_check_constraint(
        "ck_prompt_materials_image_has_storage_uri", "prompt_materials",
        "(material_type = 'IMAGE') = (storage_uri IS NOT NULL)",
    )
    op.drop_constraint("ck_prompt_materials_type", "prompt_materials", type_="check")
    op.create_check_constraint(
        "ck_prompt_materials_type", "prompt_materials", "material_type IN ('TEXT', 'IMAGE')",
    )
