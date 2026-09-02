"""Тести заявок на магазини, яких парсер ще не знає.

Перевіряється дві речі, і вони тягнуть у різні боки. Перша: жодне посилання
на чужий магазин не має пропасти — це готова заявка на розширення, і черга
нових магазинів має йти за попитом, а не за пам'яттю власника. Друга: та сама
заявка, кинута вп'яте, не повинна перетворювати бота на дзвінок будильника
для власника.

Telegram тут не потрібен: сервіс лише готує текст для власника, а відправку
робить хендлер.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.bot import service
from app.config import get_settings
from app.db import repository as repo
from app.db.migrate import upgrade_database
from app.db.models import ShopRequest

ЗАРАЗ = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
ЧУЖИЙ = "https://foxtrot.com.ua/uk/shop/noutbuki_asus.html"


@pytest.fixture
def factory(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "requests.db"))
    get_settings.cache_clear()
    upgrade_database()
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'requests.db').as_posix()}", future=True
    )
    yield async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    asyncio.run(engine.dispose())
    get_settings.cache_clear()


def попросити(factory, *, url=ЧУЖИЙ, telegram_id=111, username=None, коли=ЗАРАЗ, cooldown=24):
    """Одна спроба додати товар із невідомого магазину."""

    async def _run():
        async with factory() as session:
            notice = await service.remember_unknown_shop(
                session,
                url=url,
                telegram_id=telegram_id,
                chat_id=telegram_id,
                username=username,
                now=коли,
                cooldown_hours=cooldown,
            )
            await session.commit()
            return notice

    return asyncio.run(_run())


def заявки(factory) -> list[ShopRequest]:
    async def _run():
        async with factory() as session:
            stmt = select(ShopRequest).order_by(ShopRequest.id)
            return (await session.execute(stmt)).scalars().all()

    return list(asyncio.run(_run()))


# ----------------------------------------------------------- заявка не губиться


def test_перше_посилання_записується_і_власник_дізнається(factory) -> None:
    notice = попросити(factory, username="вася")

    assert notice is not None
    assert "foxtrot.com.ua" in notice
    assert "Спроб: 1" in notice
    assert "@вася" in notice

    записи = заявки(factory)
    assert len(записи) == 1
    assert записи[0].host == "foxtrot.com.ua"
    assert записи[0].url == ЧУЖИЙ
    assert записи[0].notified_at is not None


def test_посилання_без_домену_нічого_не_ламає(factory) -> None:
    assert попросити(factory, url="не посилання") is None
    assert заявки(factory) == []


# --------------------------------------------------- про те саме не кажуть двічі


def test_повторна_заявка_записується_але_власника_не_турбує(factory) -> None:
    попросити(factory)
    notice = попросити(factory, telegram_id=222, коли=ЗАРАЗ + timedelta(hours=1))

    assert notice is None, "про той самий магазин за годину казати вдруге не треба"
    записи = заявки(factory)
    assert len(записи) == 2, "але сама заявка має лишитись у базі"
    assert [запис.notified_at is not None for запис in записи] == [True, False]


def test_після_очікування_власник_дізнається_знову_із_загальним_рахунком(factory) -> None:
    попросити(factory)
    попросити(factory, telegram_id=222, коли=ЗАРАЗ + timedelta(hours=1))
    notice = попросити(factory, telegram_id=333, коли=ЗАРАЗ + timedelta(hours=25))

    assert notice is not None
    assert "Спроб: 3" in notice, "рахунок має бути за весь час, а не з моменту мовчання"
    assert "різних людей: 3" in notice


def test_одна_людина_не_виглядає_як_натовп(factory) -> None:
    """Десять спроб однієї людини — це не десятеро охочих."""
    попросити(factory, telegram_id=111)
    for година in range(1, 5):
        попросити(factory, telegram_id=111, коли=ЗАРАЗ + timedelta(hours=година))
    notice = попросити(factory, telegram_id=111, коли=ЗАРАЗ + timedelta(hours=25))

    assert "Спроб: 6" in notice
    assert "різних людей: 1" in notice


def test_різні_магазини_не_заважають_один_одному(factory) -> None:
    попросити(factory)
    notice = попросити(factory, url="https://allo.ua/ua/products/x.html", коли=ЗАРАЗ)

    assert notice is not None
    assert "allo.ua" in notice
    assert "Спроб: 1" in notice


# -------------------------------------------------- через справжнє додавання товару


def test_посилання_з_невідомого_магазину_доходить_до_власника(factory) -> None:
    """Шлях цілком: людина кинула посилання — власник дізнався.

    Мережі тут не буде: невідомий магазин відсіюється до походу на сторінку.
    """

    async def _run():
        async with factory() as session:
            result = await service.add_product_by_url(
                session,
                telegram_id=111,
                chat_id=111,
                message_text=f"глянь, тут дешевше {ЧУЖИЙ}",
                username="вася",
            )
            await session.commit()
            return result

    result = asyncio.run(_run())

    assert result.ok is False
    assert "не вмію читати" in result.text
    assert result.admin_notice is not None
    assert "foxtrot.com.ua" in result.admin_notice
    assert len(заявки(factory)) == 1


def test_знайомий_магазин_заявки_не_створює(factory) -> None:
    """staff-clothes парсер знає, просто не може читати через антибот-захист.

    Про такий магазин власнику писати нічого: він уже знає і про магазин, і
    про причину. Заявка тут була б шумом у списку попиту.
    """

    async def _run():
        async with factory() as session:
            result = await service.add_product_by_url(
                session,
                telegram_id=111,
                chat_id=111,
                message_text="https://www.staff-clothes.com/product/9en0116/",
            )
            await session.commit()
            return result

    result = asyncio.run(_run())

    assert result.ok is False
    assert result.admin_notice is None
    assert заявки(factory) == []


# ------------------------------------------------------------------- звідний список


def test_список_заявок_показує_найзатребуваніші_першими(factory) -> None:
    попросити(factory)
    попросити(factory, telegram_id=222, коли=ЗАРАЗ + timedelta(hours=1))
    попросити(factory, url="https://allo.ua/ua/products/x.html", коли=ЗАРАЗ)

    async def _run():
        async with factory() as session:
            return await repo.list_shop_requests(session)

    попит = asyncio.run(_run())
    assert [(item.host, item.times, item.people) for item in попит] == [
        ("foxtrot.com.ua", 2, 2),
        ("allo.ua", 1, 1),
    ]
