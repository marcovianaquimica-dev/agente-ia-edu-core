"""Fix resource_access_grants.grantee_type CHECK constraint schema drift.

Found live: 2026-09-22, during an HTTP-layer test coverage audit of
catalog.py (POST /api/v1/catalog/resources/{id}/grants) - a real request
against the real dev Postgres (port 5433) crashed with a 500
(psycopg.errors.CheckViolation on ck_resource_access_grants_grantee_type)
granting a resource to "SCHOOL", which EducationalResourceService.
grant_access's own Python-level ``allowed`` set (services/catalog.py)
explicitly treats as valid - and so does ResourceAccessGrant's SQLAlchemy
model CheckConstraint (db/models/catalog.py), which already lists
('INSTITUTION', 'SCHOOL', 'UNIT', 'SEGMENT', 'GRADE_LEVEL', 'CLASSROOM',
'SCHOOL_UNIT', 'EXTERNAL_IDENTITY').

Root cause: migration 005_pedagogical_catalog created the constraint with
only ('INSTITUTION', 'SCHOOL_UNIT', 'CLASSROOM', 'EXTERNAL_IDENTITY'). The
ORM model was later widened (commit 034db8551920, "adiciona e integra
modelos ORM para as migrations 015-038") to add SCHOOL/UNIT/SEGMENT/
GRADE_LEVEL - but no migration between 015 and 049 ever ALTERs this
constraint, so every real Postgres database following the migration chain
(this dev DB included, confirmed at `alembic current` == 049_essay_
correction before this revision) still enforces the OLD, narrower list.
sqlite-backed tests never caught this because sqlite has no CHECK-
constraint-name enforcement wired through SQLAlchemy's Base.metadata.
create_all() the same way - the model's own (wider) constraint is what
gets created there, masking the drift entirely.

Purely additive/corrective: touches the CHECK constraint only, zero rows
touched, zero risk to existing grants (none of the four originally-allowed
values are removed).
"""

from alembic import op

revision = "050_resource_grantee_fix"
down_revision = "049_essay_correction"
branch_labels = None
depends_on = None

_OLD_VALUES = "'INSTITUTION', 'SCHOOL_UNIT', 'CLASSROOM', 'EXTERNAL_IDENTITY'"
_NEW_VALUES = (
    "'INSTITUTION', 'SCHOOL', 'UNIT', 'SEGMENT', 'GRADE_LEVEL', "
    "'CLASSROOM', 'SCHOOL_UNIT', 'EXTERNAL_IDENTITY'"
)


def upgrade() -> None:
    op.drop_constraint(
        "ck_resource_access_grants_grantee_type", "resource_access_grants", type_="check"
    )
    op.create_check_constraint(
        "ck_resource_access_grants_grantee_type",
        "resource_access_grants",
        f"grantee_type IN ({_NEW_VALUES})",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_resource_access_grants_grantee_type", "resource_access_grants", type_="check"
    )
    op.create_check_constraint(
        "ck_resource_access_grants_grantee_type",
        "resource_access_grants",
        f"grantee_type IN ({_OLD_VALUES})",
    )
