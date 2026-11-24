"""Alembic environment. The URL comes from the caller (labeler.infra.db.migrate) or DATABASE_URL."""

from alembic import context
from sqlalchemy import engine_from_config, pool

from labeler.config import get_settings
from labeler.infra.db.tables import metadata

config = context.config
url = config.get_main_option("sqlalchemy.url") or get_settings().db_url
config.set_main_option("sqlalchemy.url", url)


def run_offline() -> None:
    context.configure(url=url, target_metadata=metadata, literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    connectable = config.attributes.get("connection")
    if connectable is not None:
        context.configure(connection=connectable, target_metadata=metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = engine_from_config(
        config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with engine.connect() as connection:
        # render_as_batch: SQLite cannot ALTER most things; batch mode rebuilds the table instead.
        context.configure(connection=connection, target_metadata=metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
