"""Тести тихих годин.

Ціна помилки тут несиметрична. Зайве мовчання коштує кількох годин затримки —
знижка триває днями, найближчий денний прогін надішле те саме. Зайве
повідомлення коштує сну, і бота після цього вимикають назовсім.

Головна пастка — проміжок через північ: 22:00–08:00 не є звичайним «від
меншого до більшого», і наївне порівняння `start <= година < end` тут дає
рівно навпаки.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config import get_settings
from app.core.quiet import QuietHours

ТИША = QuietHours(start=22, end=8)


def момент(година: int) -> datetime:
    return datetime(2026, 9, 10, година, 30)


# --------------------------------------------------------- проміжок через північ


@pytest.mark.parametrize("година", [22, 23, 0, 3, 7])
def test_вночі_мовчимо(година: int) -> None:
    assert ТИША.contains(момент(година)) is True


@pytest.mark.parametrize("година", [8, 9, 13, 19, 21])
def test_удень_пишемо(година: int) -> None:
    assert ТИША.contains(момент(година)) is False


def test_межі_проміжку_не_зсунуті() -> None:
    """О 22:00 уже тихо, о 08:00 уже можна — саме так, як написано в .env."""
    assert ТИША.contains(datetime(2026, 9, 10, 22, 0)) is True
    assert ТИША.contains(datetime(2026, 9, 10, 21, 59)) is False
    assert ТИША.contains(datetime(2026, 9, 10, 8, 0)) is False
    assert ТИША.contains(datetime(2026, 9, 10, 7, 59)) is True


# ------------------------------------------------------------- звичайний проміжок


def test_проміжок_без_переходу_через_північ() -> None:
    """Комусь може бути зручно мовчати вдень — правило має працювати й так."""
    денна_тиша = QuietHours(start=9, end=18)

    assert денна_тиша.contains(момент(12)) is True
    assert денна_тиша.contains(момент(20)) is False
    assert денна_тиша.contains(момент(3)) is False


def test_однакові_межі_вимикають_тишу() -> None:
    вимкнено = QuietHours(start=0, end=0)

    assert вимкнено.enabled is False
    assert all(вимкнено.contains(момент(година)) is False for година in range(24))


# ------------------------------------------------------------------ часові пояси


def test_момент_із_поясом_переводиться_в_місцевий() -> None:
    """Час із бази приходить у UTC, а тиша міряється місцевим часом машини."""
    місцева_північ = datetime(2026, 9, 10, 0, 30).astimezone()
    в_utc = місцева_північ.astimezone(timezone.utc)

    assert ТИША.contains(в_utc) is True, "переведення в місцевий час загубилось"


def test_наївний_час_вважається_місцевим() -> None:
    """`datetime.now()` без поясу — це саме місцевий час, не UTC."""
    assert ТИША.contains(datetime(2026, 9, 10, 3, 0)) is True


# --------------------------------------------------------------- налаштування


def test_межі_читаються_з_налаштувань(monkeypatch) -> None:
    monkeypatch.setenv("NOTIFY_QUIET_FROM", "23")
    monkeypatch.setenv("NOTIFY_QUIET_TO", "6")
    get_settings.cache_clear()

    тиша = QuietHours.from_settings()

    assert (тиша.start, тиша.end) == (23, 6)
    assert тиша.describe() == "23:00–06:00"
    get_settings.cache_clear()


def test_година_поза_добою_не_ламає_налаштування(monkeypatch) -> None:
    """24 — типова описка замість 0; краще звести до доби, ніж мовчати вічно."""
    monkeypatch.setenv("NOTIFY_QUIET_FROM", "24")
    monkeypatch.setenv("NOTIFY_QUIET_TO", "8")
    get_settings.cache_clear()

    тиша = QuietHours.from_settings()

    assert тиша.start == 0
    assert тиша.contains(момент(3)) is True
    assert тиша.contains(момент(12)) is False
    get_settings.cache_clear()


def test_наступна_дозволена_година_це_кінець_тиші() -> None:
    assert ТИША.next_allowed().hour == 8


def test_за_добу_тиша_не_довша_за_проміжок() -> None:
    """Груба перевірка на випадок, якщо логіку колись перепишуть."""
    початок = datetime(2026, 9, 10, 0, 0)
    тихих = sum(
        1 for крок in range(24 * 4) if ТИША.contains(початок + timedelta(minutes=15 * крок))
    )

    assert тихих == 10 * 4, "22:00–08:00 це рівно десять годин"
