"""Alembic environment: async Postgres via asyncpg.

``models`` is the single source of truth for the schema; ``base`` / ``target``
metadata come straight from it (it registers every table on import). The URL is
read from ``FLUXSWARM_DATABASE_URL`` (required) so environments never embed
credentials in ``alembic.ini``.
"""
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from models import Base  # noqa: F401  (import registers all tables)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _async_url(url: str) -> str:
    """Force the asyncpg driver so Alembic's async engine uses the right adapter."""
    if url.startswith(("postgresql://", "postgres://")):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


config.set_main_option("sqlalchemy.url", _async_url(os.environ["FLUXSWARM_DATABASE_URL"]))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in this application's async engine by default."""
    connectable = config.attributes.get("connection")
    if connectable is not None and not hasattr(connectable, "connect"):
        connectable = None
    if connectable is None:
        connectable = async_engine_from_config(
            config.get_section(config.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )
        _run_async(connectable)
    else:
        # Caller supplied a connection (programmatic invocation).
        context.configure(connection=connectable, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


def _run_async(connectable) -> None:
    import asyncio

    async def _run() -> None:
        async with connectable.connect() as connection:
            await connection.run_sync(_do_run_migrations)
        if hasattr(connectable, "dispose"):
            await connectable.dispose()

    asyncio.run(_run())


def _do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()