"""Тести звіту про оновлення коду на сервері.

Машина стоїть на горищі, і звіт у телеграмі — єдине, що про неї відомо.
Тому перевіряється не краса тексту, а те, чи можна за ним зрозуміти стан
машини, не встаючи з дивана: оновилось воно чи відкотилось, і на якій
версії зараз працює.
"""

from __future__ import annotations

from app.bot.texts import update_report


def test_успішне_оновлення_показує_версію_і_за_що_оновились() -> None:
    text = update_report(
        status="updated",
        branch="main",
        revision="9016dd7",
        commits=["Обхід кожні 6 годин", "Заявки на магазини"],
    )

    assert "Сервер оновився" in text
    assert "main" in text
    assert "9016dd7" in text
    assert "Обхід кожні 6 годин" in text
    assert "Заявки на магазини" in text


def test_відкат_каже_прямо_що_служби_на_старому_коді() -> None:
    """Найважливіший зі звітів: людина має зрозуміти, що втручання не потрібне."""
    text = update_report(status="rolled_back", branch="main", revision="34d1790")

    assert "Оновлення відкочено" in text
    assert "повернуто на попередній коміт" in text
    assert "на старій версії" in text
    assert "34d1790" in text


def test_збій_оновлення_кличе_людину() -> None:
    text = update_report(status="failed", branch="main", details="fatal: could not read")

    assert "не вдалося" in text.lower()
    assert "подивитись очима" in text
    assert "fatal: could not read" in text


def test_розмітка_не_ламається_від_тексту_помилки() -> None:
    """У хвості логу трапляється що завгодно — кутові дужки Telegram не подарує."""
    text = update_report(status="failed", details="assert <Product 1> == <Product 2>")

    assert "<Product" not in text
    assert "&lt;Product 1&gt;" in text


def test_довгий_лог_обрізається() -> None:
    text = update_report(status="rolled_back", details="х" * 5000)

    assert len(text) < 2500, "Telegram не прийме повідомлення довше 4096 символів"


def test_без_подробиць_звіт_лишається_читабельним() -> None:
    """Мінімальний виклик: нічого, крім результату, невідомо."""
    text = update_report(status="updated")

    assert text.startswith("<b>Сервер оновився</b>")
    assert "Гілка" not in text
