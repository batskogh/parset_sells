"""Оновлення схеми бази через alembic.

Чому не `create_all`: він створює те, чого немає, і **не чіпає** те, що вже
є. Поки схема тільки росла новими таблицями, різниці не було; щойно
знадобиться змінити наявну колонку, `create_all` мовчки нічого не зробить, і
база розійдеться з моделями непомітно.

Чому не «знести й створити заново»: історія цін — це те, заради чого
проєкт існує. Мода рахується по всій історії, тож видалити базу означає
осліпнути на стільки тижнів, скільки збиралися дані.

Окремий випадок — бази, створені ще `create_all` до появи alembic. У них є
таблиці, але немає `alembic_version`, і звичайний `upgrade` спробував би
створити наявні таблиці заново. Такій базі спершу проставляється baseline
(`stamp`), і далі вона живе як усі.
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.config import BASE_DIR, get_settings

logger = logging.getLogger(__name__)

# ревізія, що описує схему часів `create_all`
BASELINE_REVISION = "cdc05cc89442"

# таблиця, за якою впізнаємо стару базу зі спадщиною
LEGACY_TABLE = "prices"


def alembic_config() -> Config:
    config = Config(str(BASE_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BASE_DIR / "migrations"))
    return config


def sync_database_url() -> str:
    return f"sqlite:///{get_settings().db_path.as_posix()}"


def inspect_database() -> tuple[bool, bool]:
    """Чи є в базі версія alembic і чи є в ній успадковані таблиці."""
    engine = create_engine(sync_database_url(), future=True)
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    return "alembic_version" in tables, LEGACY_TABLE in tables


def upgrade_database() -> str | None:
    """Доводить базу до останньої ревізії. Повертає дію, яку довелось зробити."""
    config = alembic_config()
    versioned, legacy = inspect_database()
    action = None

    if not versioned and legacy:
        # база зі спадщини: таблиці вже є, тож baseline лише позначаємо
        logger.info("База створена до alembic — проставляю baseline %s", BASELINE_REVISION)
        command.stamp(config, BASELINE_REVISION)
        action = "stamped"

    command.upgrade(config, "head")
    return action or ("created" if not versioned else "upgraded")
