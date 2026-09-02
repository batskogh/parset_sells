"""Налаштування логів: одна консоль, один файл на канал.

Раніше кожна точка входу кликала `logging.basicConfig` по-своєму, і лог жив
рівно доки відкрите вікно терміналу. На сервері це майже не проблема —
systemd складає stdout у journald, — але journald має свої ліміти й свою
ротацію, а копію логу зручно віддати або переглянути звичайним `tail`. Тому
той самий потік пишеться ще й у файл.

Канал — це ім'я файлу: `parser`, `notify`, `bot`, `discounts`. Розділені
навмисно: бот пише постійно, парсер — чотири рази на добу, і мішанина з обох
робить обидва нечитабельними.

Файл не має права зламати роботу. Якщо каталог недоступний (права, повний
диск), логування лишається консольним, а про причину повідомляється один
раз — гальмувати парсинг через журнал безглуздо.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from app.config import get_settings

CONSOLE_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
CONSOLE_DATEFMT = "%H:%M:%S"

# у файлі потрібна повна дата: його читають через тиждень, а не зараз
FILE_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
FILE_DATEFMT = "%Y-%m-%d %H:%M:%S"

# Службовий шум, який не потрібен у жодному з журналів: httpx друкує рядок на
# кожен запит, aiogram.event — на кожен апдейт.
#
# Alembic тут навмисно немає, хоч він і додає два рядки на кожен старт. Той
# самий логер (`alembic.runtime.migration`) друкує і застосування міграцій —
# заглушивши шум, ми заглушили б і єдине свідчення того, що схема змінилась.
# Раніше в коді глушився `alembic.runtime.plugins`, якого не існує: рядок був,
# ефекту не було.
QUIET_LOGGERS = ("httpx", "httpcore", "aiogram.event")


def log_path(channel: str) -> Path:
    """Куди ляже лог цього каналу."""
    settings = get_settings()
    return settings.log_dir / f"{channel}.log"


def setup_logging(
    channel: str,
    *,
    level: str | None = None,
    to_file: bool | None = None,
    quiet: tuple[str, ...] = QUIET_LOGGERS,
) -> Path | None:
    """Піднімає консольний і файловий журнали. Повертає шлях до файлу або None.

    Викликається двічі — не подвоює вивід: хендлери, поставлені цією ж
    функцією, спершу знімаються. Без цього кожен повторний виклик у тестах
    (та й у довгому процесі) множив би рядки.
    """
    settings = get_settings()
    рівень = getattr(logging, (level or settings.log_level).upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(рівень)

    for handler in list(root.handlers):
        if getattr(handler, "_pricebot_channel", None) is not None:
            root.removeHandler(handler)
            handler.close()

    console = logging.StreamHandler(stream=sys.stdout)
    console.setFormatter(logging.Formatter(CONSOLE_FORMAT, datefmt=CONSOLE_DATEFMT))
    console._pricebot_channel = channel        # мітка «наш», щоб зняти при повторі
    root.addHandler(console)

    for name in quiet:
        logging.getLogger(name).setLevel(logging.WARNING)

    if to_file is None:
        to_file = settings.log_to_file
    if not to_file:
        return None

    шлях = log_path(channel)
    try:
        шлях.parent.mkdir(parents=True, exist_ok=True)
        file_handler = TimedRotatingFileHandler(
            шлях,
            when="midnight",
            backupCount=settings.log_retention_days,
            encoding="utf-8",
            delay=True,          # файл створюється першим записом, а не порожнім
        )
    except OSError as помилка:
        # журнал не має права зупиняти роботу — скаржимось у консоль і живемо далі
        root.warning("Не вдалося відкрити файл логу %s: %s", шлях, помилка)
        return None

    file_handler.setFormatter(logging.Formatter(FILE_FORMAT, datefmt=FILE_DATEFMT))
    file_handler._pricebot_channel = channel
    root.addHandler(file_handler)
    return шлях
