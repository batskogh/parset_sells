"""Тести журналів.

Лог — це те, до чого звертаються, коли все вже сталося й терміналу давно
немає. Тому перевіряється не формат рядків, а властивості, від яких залежить,
чи буде що читати: що файл справді з'явився, що канали не змішались, що
повторний виклик не подвоїв вивід і що недоступний каталог не зупинив роботу.
"""

from __future__ import annotations

import logging

import pytest

from app.config import get_settings
from app.core.logs import log_path, setup_logging


@pytest.fixture(autouse=True)
def чиста_конфігурація(tmp_path, monkeypatch):
    """Кожен тест зі своїм каталогом логів і без слідів попереднього."""
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("LOG_TO_FILE", "1")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    get_settings.cache_clear()

    root = logging.getLogger()
    збережені = list(root.handlers)
    рівень = root.level
    root.handlers = []
    yield
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    root.handlers = збережені
    root.setLevel(рівень)
    get_settings.cache_clear()


def test_рядок_доходить_до_файлу() -> None:
    шлях = setup_logging("parser")
    logging.getLogger("parser").info("прогін почався")

    assert шлях is not None
    assert "прогін почався" in шлях.read_text(encoding="utf-8")


def test_кирилиця_в_логу_читабельна() -> None:
    """Файл відкривають очима, і «\\u043f\\u0440\\u043e\\u0433\\u0456\\u043d» там нікому не потрібен."""
    шлях = setup_logging("parser")
    logging.getLogger("parser").info("Цитрус: товарів 12")

    assert "Цитрус: товарів 12" in шлях.read_text(encoding="utf-8")


def test_канали_живуть_в_окремих_файлах() -> None:
    """Бот пише постійно, парсер — чотири рази на добу; разом нечитабельно."""
    setup_logging("parser")
    logging.getLogger("parser").info("рядок парсера")
    setup_logging("bot")
    logging.getLogger("bot").info("рядок бота")

    assert "рядок парсера" in log_path("parser").read_text(encoding="utf-8")
    текст_бота = log_path("bot").read_text(encoding="utf-8")
    assert "рядок бота" in текст_бота
    assert "рядок парсера" not in текст_бота


def test_повторне_налаштування_не_подвоює_рядки() -> None:
    setup_logging("parser")
    шлях = setup_logging("parser")
    logging.getLogger("parser").info("одного разу")

    assert шлях.read_text(encoding="utf-8").count("одного разу") == 1


def test_рівень_нижче_порогу_у_файл_не_потрапляє() -> None:
    шлях = setup_logging("parser", level="WARNING")
    logging.getLogger("parser").info("дрібниця")
    logging.getLogger("parser").warning("а це важливо")

    текст = шлях.read_text(encoding="utf-8")
    assert "дрібниця" not in текст
    assert "а це важливо" in текст


def test_службовий_шум_бібліотек_приглушено() -> None:
    """httpx на INFO друкує рядок на кожен запит — у журналі це сміття."""
    шлях = setup_logging("parser")
    logging.getLogger("parser").info("прогін почався")
    logging.getLogger("httpx").info("HTTP Request: GET https://example.com")

    текст = шлях.read_text(encoding="utf-8")
    assert "прогін почався" in текст
    assert "HTTP Request" not in текст


def test_вимкнений_файл_лишає_саму_консоль() -> None:
    шлях = setup_logging("parser", to_file=False)
    logging.getLogger("parser").info("лише в консоль")

    assert шлях is None
    assert not log_path("parser").exists()


def test_недоступний_каталог_не_валить_роботу(tmp_path, monkeypatch) -> None:
    """Журнал не має права зупиняти парсинг: не вийшло — пишемо в консоль."""
    зайнято = tmp_path / "зайнято"
    зайнято.write_text("це файл, а не каталог", encoding="utf-8")
    monkeypatch.setenv("LOG_DIR", str(зайнято))
    get_settings.cache_clear()

    шлях = setup_logging("parser")
    logging.getLogger("parser").info("робота триває")

    assert шлях is None, "падати через журнал не можна"


def test_ротація_налаштована_на_добу_і_на_збереження() -> None:
    """Файл має різатись опівночі, інакше за рік він стане нечитабельним."""
    setup_logging("parser")
    файлові = [
        h for h in logging.getLogger().handlers if hasattr(h, "backupCount")
    ]

    assert len(файлові) == 1
    assert файлові[0].when == "MIDNIGHT"
    assert файлові[0].backupCount == get_settings().log_retention_days
