from __future__ import annotations

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool, text

from alembic import context
from local_drama.config import Settings

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

settings = Settings.from_env()
database_url = os.environ.get("LOCAL_DRAMA_DATABASE_URL", f"sqlite:///{settings.database_path.as_posix()}")
config.set_main_option("sqlalchemy.url", database_url)
target_metadata = None


def run_migrations_offline() -> None:
    context.configure(url=database_url, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        if connection.dialect.name == "sqlite":
            connection.execute(text("PRAGMA foreign_keys=ON"))
            connection.execute(text("PRAGMA journal_mode=WAL"))
            connection.execute(text("PRAGMA busy_timeout=10000"))
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
        # SQLite reports non-transactional DDL; commit the Alembic version row explicitly.
        connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
