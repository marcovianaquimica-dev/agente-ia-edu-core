"""Adiciona 'cancelling' a CHECK constraint de status de
mass_correction_runs.

O SDK real da OpenAI (openai.types.Batch.status) tem um valor real que a
constraint original (migration 059) nao previu: 'cancelling' (estado
transitorio entre alguem cancelar um lote pelo dashboard da OpenAI e ele
virar 'cancelled'). advance_run grava o status vindo da OpenAI sem
mapeamento - sem esta migration, consultar um lote nesse estado quebra o
commit com IntegrityError.

Puramente aditiva: so alarga a lista de valores aceitos, nenhuma linha
existente e afetada (nenhum valor antigo foi removido).

Nota: o id de revision original pedido no brief
("060_mass_correction_runs_cancelling_status", 42 caracteres) estoura
alembic_version.version_num, que e VARCHAR(32) em todo o resto da cadeia
de migrations deste repo (confirmado varrendo migrations/versions/*.py -
nenhuma outra revisao passa de 32 caracteres) - rodar upgrade contra o
Postgres real levanta StringDataRightTruncation. Encurtado aqui para
"060_mass_correction_cancelling" (30 caracteres) para caber.

Revision ID: 060_mass_correction_cancelling
Revises: 059_mass_correction_runs
"""

from alembic import op

revision = "060_mass_correction_cancelling"
down_revision = "059_mass_correction_runs"
branch_labels = None
depends_on = None

_OLD_VALUES = (
    "'PENDING', 'validating', 'in_progress', 'finalizing', "
    "'completed', 'failed', 'expired', 'cancelled'"
)
_NEW_VALUES = (
    "'PENDING', 'validating', 'in_progress', 'finalizing', "
    "'completed', 'failed', 'expired', 'cancelling', 'cancelled'"
)


def upgrade() -> None:
    op.drop_constraint(
        "ck_mass_correction_runs_status", "mass_correction_runs", type_="check"
    )
    op.create_check_constraint(
        "ck_mass_correction_runs_status",
        "mass_correction_runs",
        f"status IN ({_NEW_VALUES})",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_mass_correction_runs_status", "mass_correction_runs", type_="check"
    )
    op.create_check_constraint(
        "ck_mass_correction_runs_status",
        "mass_correction_runs",
        f"status IN ({_OLD_VALUES})",
    )
