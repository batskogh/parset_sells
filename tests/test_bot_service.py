"""Тести поведінки бота.

Telegram тут не потрібен: хендлери тонкі, а вся робота живе в
`app.bot.service` і працює зі звичайною сесією БД. Перевіряємо те, що людина
помітить: що її запам'ятали, що повторний /start не плодить дублікатів і що
після /stop можна повернутись.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.bot import service
from app.bot.texts import BOT_COMMANDS, help_text, stopped, welcome
from app.config import get_settings
from app.db.migrate import upgrade_database
from app.db.models import TelegramUser


@pytest.fixture
def session_factory(tmp_path, monkeypatch):
    """Тимчасова база, піднята справжніми міграціями."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "bot.db"))
    get_settings.cache_clear()
    upgrade_database()

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'bot.db').as_posix()}", future=True
    )
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    asyncio.run(engine.dispose())
    get_settings.cache_clear()


async def _greet(factory, **kwargs) -> str:
    async with factory() as session:
        text = await service.greet(session, **kwargs)
        await session.commit()
    return text


def погукати(factory, **kwargs) -> str:
    return asyncio.run(_greet(factory, **kwargs))


def порахувати_людей(factory) -> int:
    async def _count():
        async with factory() as session:
            return len((await session.execute(select(TelegramUser))).scalars().all())

    return asyncio.run(_count())


# --------------------------------------------------------------- реєстрація


def test_старт_запамятовує_людину(session_factory) -> None:
    text = погукати(
        session_factory, telegram_id=555_000_111, chat_id=555_000_111,
        username="hto", first_name="Богдан",
    )
    assert "Богдан" in text
    assert порахувати_людей(session_factory) == 1


def test_повторний_старт_не_плодить_дублікатів(session_factory) -> None:
    for _ in range(3):
        погукати(session_factory, telegram_id=42, chat_id=42, first_name="Той самий")
    assert порахувати_людей(session_factory) == 1


def test_знайомого_вітають_інакше(session_factory) -> None:
    перший = погукати(session_factory, telegram_id=7, chat_id=7, first_name="Оля")
    другий = погукати(session_factory, telegram_id=7, chat_id=7, first_name="Оля")
    assert "Вітаю" in перший
    assert "знову" in другий


def test_імя_оновлюється(session_factory) -> None:
    """Людина могла змінити ім'я — бот має звертатись правильно."""
    погукати(session_factory, telegram_id=8, chat_id=8, username="stare", first_name="Старе")
    text = погукати(session_factory, telegram_id=8, chat_id=8, username="nove", first_name="Нове")
    assert "Нове" in text


def test_велике_id_телеграму(session_factory) -> None:
    величезний = 8_478_514_021
    погукати(session_factory, telegram_id=величезний, chat_id=величезний, first_name="Хтось")

    async def _find():
        async with session_factory() as session:
            return (
                await session.execute(
                    select(TelegramUser).where(TelegramUser.telegram_id == величезний)
                )
            ).scalars().first()

    assert asyncio.run(_find()) is not None


def test_chat_id_може_відрізнятись_від_id_людини(session_factory) -> None:
    """У групі це різні числа, а сповіщення йде саме в чат."""
    погукати(session_factory, telegram_id=100, chat_id=-200_500, first_name="У групі")

    async def _find():
        async with session_factory() as session:
            return (
                await session.execute(select(TelegramUser).where(TelegramUser.telegram_id == 100))
            ).scalars().one()

    user = asyncio.run(_find())
    assert user.chat_id == -200_500


# ------------------------------------------------------------------- /stop


def test_стоп_вимикає_сповіщення(session_factory) -> None:
    погукати(session_factory, telegram_id=9, chat_id=9, first_name="Хтось")

    async def _stop():
        async with session_factory() as session:
            ok = await service.stop_notifications(session, telegram_id=9)
            await session.commit()
            return ok

    assert asyncio.run(_stop()) is True

    async def _find():
        async with session_factory() as session:
            return (
                await session.execute(select(TelegramUser).where(TelegramUser.telegram_id == 9))
            ).scalars().one()

    assert asyncio.run(_find()).is_active is False


def test_старт_після_стопу_повертає_сповіщення(session_factory) -> None:
    """Найочевидніший спосіб повернутись — написати /start ще раз."""
    погукати(session_factory, telegram_id=10, chat_id=10, first_name="Хтось")

    async def _stop_then_start():
        async with session_factory() as session:
            await service.stop_notifications(session, telegram_id=10)
            await session.commit()
        async with session_factory() as session:
            await service.greet(session, telegram_id=10, chat_id=10, first_name="Хтось")
            await session.commit()
        async with session_factory() as session:
            return (
                await session.execute(select(TelegramUser).where(TelegramUser.telegram_id == 10))
            ).scalars().one()

    assert asyncio.run(_stop_then_start()).is_active is True


def test_стоп_для_незнайомця_нічого_не_ламає(session_factory) -> None:
    async def _stop():
        async with session_factory() as session:
            return await service.stop_notifications(session, telegram_id=404)

    assert asyncio.run(_stop()) is False


# ------------------------------------------------------------------ тексти


def test_привітання_без_імені() -> None:
    assert "Вітаю!" in welcome(None)


def test_тексти_пояснюють_головне() -> None:
    """Людина має одразу зрозуміти, чим цей бот відрізняється від решти."""
    разом = welcome("Хтось") + help_text()
    assert "звичної" in разом            # знижка рахується від звичної ціни
    assert "накручують" in разом         # і чому не від перекресленої
    assert "один раз" in help_text()     # і що спаму не буде


def test_команди_описані() -> None:
    assert set(BOT_COMMANDS) == {"start", "help", "stop"}
    assert all(опис and опис[0].islower() for опис in BOT_COMMANDS.values())
    assert "лишається" in stopped()      # список товарів не зникає
