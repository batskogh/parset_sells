"""Оточення alembic.

Адреса бази береться з `app.config`, а не з `alembic.ini`: інакше шлях до
бази жив би у двох місцях і рано чи пізно розійшовся б. Міграції ходять
синхронним драйвером (`sqlite:///`), хоча застосунок працює через
`aiosqlite` — для DDL асинхронність нічого не дає, а env.py спрощує помітно.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import get_settings
from app.db.base import Base
from app.db import models  # noqa: F401  — щоб моделі потрапили в метадані

config = context.config

# Логування налаштовуємо лише коли alembic запущено з командного рядка.
# Коли міграції ганяє сам застосунок (init_db), логування вже налаштоване —
# а `alembic.ini` перевів би кореневий логер на WARN і зробив би застосунок
# німим одразу після старту: саме так бот мовчки «зникав» після міграції.
if config.config_file_name is not None and config.attributes.get("configure_logging", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def database_url() -> str:
    """Синхронний URL тієї самої бази, з якою працює застосунок."""
    return f"sqlite:///{get_settings().db_path.as_posix()}"


def run_migrations_offline() -> None:
    """Генерація SQL без підключення (`alembic upgrade head --sql`)."""
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = database_url()

    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite не вміє більшості ALTER TABLE — batch-режим пересоздає
            # таблицю замість зміни на місці
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
