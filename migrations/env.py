from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import official  # noqa: F401
from agente_ia_edu.db.session import get_database_url

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers defaults to True, which would silently disable
    # every logger already registered in this process (e.g. application
    # loggers created by importing agente_ia_edu modules before a migration
    # runs) just because alembic.ini's [loggers] section doesn't happen to
    # list them. That's a process-wide side effect with no relation to what
    # this migration run is trying to configure, so opt out of it: only set
    # up/adjust the loggers alembic.ini actually names (root, sqlalchemy,
    # alembic), and leave every other logger alone.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _get_database_url() -> str:
    configured_url = config.get_main_option("sqlalchemy.url")
    if configured_url:
        return configured_url
    return get_database_url()


def run_migrations_offline() -> None:
    url = _get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _get_database_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
