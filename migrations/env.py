from logging.config import fileConfig
import os

from alembic import context
from sqlalchemy import engine_from_config, pool

from argus.persistence.search_ledger import LedgerBase

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
if (
    config.get_main_option("sqlalchemy.url") == "sqlite:///argus.db"
    and os.environ.get("ARGUS_DB_URL")
):
    # ConfigParser treats percent signs as interpolation syntax. SQLAlchemy
    # URLs commonly contain percent-encoded credentials, so escape them only
    # for storage in Alembic's Config object.
    config.set_main_option(
        "sqlalchemy.url",
        os.environ["ARGUS_DB_URL"].replace("%", "%%"),
    )

target_metadata = LedgerBase.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
