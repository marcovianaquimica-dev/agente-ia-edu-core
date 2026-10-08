"""Database type definitions compatible with PostgreSQL and SQLite.

This module provides custom SQLAlchemy types that allow seamless compatibility
between PostgreSQL (JSONB) and SQLite (JSON) without requiring model changes.

UUID columns across the codebase use SQLAlchemy's native `Uuid` type directly
(native UUID on PostgreSQL, CHAR(32) on SQLite) - no custom wrapper is needed
or used for UUIDs; all models share this single representation.
"""

from sqlalchemy import JSON, Text, TypeDecorator
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import UserDefinedType


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



class _PgVector(UserDefinedType):
    """pgvector's ``vector`` column type, declared WITHOUT a dimension.

    Deliberately hand-rolled instead of depending on the ``pgvector`` package:
    that package pulls in numpy, and everything this project needs from it is
    the two lines below. The same reasoning ``JSONBCompatible`` follows - the
    smallest thing that works, no new dependency.

    No dimension in the DDL is the point, not an oversight: the dimension
    belongs to the embedding SPACE (``knowledge_embedding_spaces.dimensions``),
    so introducing a 1024-dimension model later is a row plus a partial index,
    never a table migration. The trade-off is that the database will accept a
    vector of the wrong length - that validation lives in the service and is
    covered by its own test.
    """

    cache_ok = True

    def get_col_spec(self, **kw):  # noqa: ARG002 - SQLAlchemy calls it this way
        return "vector"


class VectorCompatible(TypeDecorator):
    """Embedding vector that is ``vector`` on PostgreSQL and ``JSON`` on SQLite.

    Mirrors ``JSONBCompatible`` so a model never learns which dialect it is
    running on: the column is ``vector`` on PostgreSQL and ``TEXT`` on SQLite,
    and either way the model sees a ``list[float]``.

    DELIBERATE DEVIATION from the design doc, which said "JSON on SQLite".
    ``impl`` must NOT be ``JSON``: ``TypeDecorator`` runs the impl's bind
    processor AFTER ``process_bind_param``, so a JSON impl would re-serialize
    pgvector's own literal into ``"[0.5,-1.25]"`` (quoted) and corrupt every
    vector written to PostgreSQL. ``Text`` has no bind processor, so the value
    passes through untouched.

    The side benefit is worth more than the JSON column would have been: the
    stored representation is now byte-identical on both dialects, so there is
    one serialisation path to reason about instead of two. Nothing ever queries
    the vector through SQLite's JSON functions, so nothing is lost.
    """

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(_PgVector())
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        floats = [float(component) for component in value]
        return "[" + ",".join(repr(component) for component in floats) + "]"

    def process_result_value(self, value, dialect):  # noqa: ARG002
        if value is None:
            return None
        if isinstance(value, str):
            body = value.strip().removeprefix("[").removesuffix("]").strip()
            if not body:
                return []
            return [float(component) for component in body.split(",")]
        return [float(component) for component in value]
