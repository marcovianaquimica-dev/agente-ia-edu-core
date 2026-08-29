"""Database type definitions compatible with PostgreSQL and SQLite.

This module provides custom SQLAlchemy types that allow seamless compatibility
between PostgreSQL (JSONB) and SQLite (JSON) without requiring model changes.

UUID columns across the codebase use SQLAlchemy's native `Uuid` type directly
(native UUID on PostgreSQL, CHAR(32) on SQLite) - no custom wrapper is needed
or used for UUIDs; all models share this single representation.
"""

from sqlalchemy import JSON, TypeDecorator
from sqlalchemy.dialects.postgresql import JSONB


class JSONBCompatible(TypeDecorator):
    """JSONB type that gracefully falls back to JSON for SQLite.

    This custom type uses PostgreSQL's JSONB in production and falls back
    to SQLAlchemy's JSON type for testing with SQLite. It ensures:

    1. No changes to model definitions
    2. PostgreSQL continues using native JSONB
    3. SQLite tests use native JSON (full compatibility)
    4. Migration files remain unchanged
    5. Production behavior is identical

    Usage in models:
        from agente_ia_edu.db.types import JSONBCompatible
        metadata_: Mapped[dict[str, Any] | None] = mapped_column(
            "metadata", JSONBCompatible
        )

    Behind the scenes:
    - PostgreSQL: Compiles to JSONB
    - SQLite: Compiles to JSON (SQLAlchemy native)
    - The type is seamless to the model layer
    """

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        """Load appropriate type for the current dialect.

        Args:
            dialect: SQLAlchemy dialect (postgresql, sqlite, etc.)

        Returns:
            JSONB for PostgreSQL, JSON for others.
        """
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())

