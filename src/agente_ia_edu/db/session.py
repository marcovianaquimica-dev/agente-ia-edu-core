import os
from collections.abc import Mapping
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


class DatabaseConfigurationError(RuntimeError):
    """Raised when the database configuration is missing or invalid."""


def get_database_url(environ: Mapping[str, str] | None = None) -> str:
    """Return the PostgreSQL URL configured for the application."""
    source = os.environ if environ is None else environ
    database_url = source.get("DATABASE_URL")
    if not database_url:
        raise DatabaseConfigurationError(
            "DATABASE_URL is required to configure the database"
        )
    if not database_url.startswith("postgresql+psycopg://"):
        raise DatabaseConfigurationError(
            "DATABASE_URL must use the postgresql+psycopg:// format"
        )
    return database_url


def create_engine(
    database_url: str | None = None,
    **engine_options: Any,
) -> AsyncEngine:
    """Create an async engine without opening a database connection."""
    url = database_url or get_database_url()
    return create_async_engine(url, **engine_options)


def create_session_factory(
    engine: AsyncEngine | None = None,
    **session_options: Any,
) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory without opening a database connection."""
    configured_engine = engine if engine is not None else create_engine()
    return async_sessionmaker(configured_engine, **session_options)
