from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

target_metadata = None


def _database_url() -> str:
    configured = os.getenv("DATABASE_URL")
    if configured:
        if configured.startswith("postgres://"):
            return "postgresql+psycopg://" + configured.removeprefix("postgres://")
        if configured.startswith("postgresql://"):
            return "postgresql+psycopg://" + configured.removeprefix("postgresql://")
        return configured
    db_path = Path(os.getenv("GUARDIAN_DB_PATH", Path(__file__).resolve().parents[1] / "data" / "guardianai.db"))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{db_path.as_posix()}"


def run_migrations_offline() -> None:
    context.configure(url=_database_url(), target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def _configure_and_run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata,
                      render_as_batch=connection.dialect.name == "sqlite")
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _configure_and_run(connection)
        return
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        _configure_and_run(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
