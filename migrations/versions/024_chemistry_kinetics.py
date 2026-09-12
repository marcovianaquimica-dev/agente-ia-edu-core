"""Add the approved Chemistry kinetics content node.

Revision ID: 024_chemistry_kinetics
Revises: 023_curriculum_taxonomy
"""

import re
import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "024_chemistry_kinetics"
down_revision = "023_curriculum_taxonomy"
branch_labels = None
depends_on = None

TAXONOMY_VERSION = revision
PARENT_CODE = "CHEMISTRY-PHYSICAL"
NODE_CODE = "CHEMISTRY-PHYSICAL-KINETICS"
NODE_NAME = "Cinética química"
NODE_DESCRIPTION = "Estudo da velocidade das reações químicas."
NODE_TYPE = "CONTENT"
NODE_POSITION = 2


def _row(connection, code):
    return connection.execute(
        sa.text(
            "SELECT id, parent_id, root_id, code, name, description, node_type, "
            "position, active FROM catalog_nodes WHERE code = :code"
        ),
        {"code": code},
    ).mappings().first()


def _uuid_value(value):
    return value.hex if isinstance(value, uuid.UUID) else str(value)


def _validate_parent(parent):
    if parent is None:
        raise RuntimeError(f"Required taxonomy parent is missing: {PARENT_CODE}")
    if parent["node_type"] != "AREA":
        raise RuntimeError(f"Taxonomy parent has incompatible type: {PARENT_CODE}")
    if not parent["active"]:
        raise RuntimeError(f"Taxonomy parent is inactive: {PARENT_CODE}")


def _validate_existing(existing, parent):
    expected = {
        "parent_id": parent["id"],
        "root_id": parent["root_id"] or parent["id"],
        "name": NODE_NAME,
        "description": NODE_DESCRIPTION,
        "node_type": NODE_TYPE,
        "position": NODE_POSITION,
        "active": True,
    }
    if any(existing[key] != value for key, value in expected.items()):
        raise RuntimeError(f"Existing taxonomy node is incompatible: {NODE_CODE}")


def upgrade() -> None:
    connection = op.get_bind()
    parent = _row(connection, PARENT_CODE)
    _validate_parent(parent)
    existing = _row(connection, NODE_CODE)
    if existing is not None:
        _validate_existing(existing, parent)
        return

    connection.execute(
        sa.text(
            "INSERT INTO catalog_nodes "
            "(id, parent_id, root_id, node_type, code, name, description, position, active, metadata, created_at, updated_at) "
            "VALUES (:id, :parent_id, :root_id, :node_type, :code, :name, :description, :position, :active, :metadata, :created_at, :updated_at)"
        ),
        {
            "id": uuid.uuid4().hex,
            "parent_id": _uuid_value(parent["id"]),
            "root_id": _uuid_value(parent["root_id"] or parent["id"]),
            "node_type": NODE_TYPE,
            "code": NODE_CODE,
            "name": NODE_NAME,
            "description": NODE_DESCRIPTION,
            "position": NODE_POSITION,
            "active": True,
            "metadata": None,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        },
    )


def _quote(identifier):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier):
        raise RuntimeError("Unsafe database identifier in dependency check")
    return f'"{identifier}"'


def _has_dependencies(connection, node_id):
    inspector = sa.inspect(connection)
    for table_name in inspector.get_table_names():
        for foreign_key in inspector.get_foreign_keys(table_name):
            if foreign_key.get("referred_table") != "catalog_nodes":
                continue
            for column in foreign_key.get("constrained_columns") or []:
                count = connection.execute(
                    sa.text(
                        f"SELECT COUNT(*) FROM {_quote(table_name)} "
                        f"WHERE {_quote(column)} = :node_id"
                    ),
                    {"node_id": _uuid_value(node_id)},
                ).scalar_one()
                if count:
                    return True
    return False


def downgrade() -> None:
    connection = op.get_bind()
    existing = _row(connection, NODE_CODE)
    if existing is None:
        return
    if _has_dependencies(connection, existing["id"]):
        raise RuntimeError(f"Cannot downgrade taxonomy node with dependencies: {NODE_CODE}")
    connection.execute(
        sa.text("DELETE FROM catalog_nodes WHERE id = :node_id"),
        {"node_id": _uuid_value(existing["id"])},
    )