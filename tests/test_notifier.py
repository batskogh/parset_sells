"""Тести розсилання: кому і про що.

Помилка тут коштує довіри: лист не тій людині або мовчання про справжню
знижку. Telegram у тестах не потрібен — збір адресатів свідомо відділений
від відправки, і перевіряється саме він.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.core.discount import DiscountRules
from app.core.notifier import collect, mark_sent
from app.core.notify_log import NotifyRules
from app.db.migrate import upgrade_database
from app.db.models import Notification, Price, Product, Shop, Subscription, TelegramUser

D = Decimal
RULES = DiscountRules(min_drop_percent=D("10"), min_samples=5, bucket_percent=D("1"))
NOTIFY = NotifyRules(cooldown_hours=24, deeper_by_percent=D("10"))
ЗАРАЗ = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def factory(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "notify.db"))
    get_settings.cache_clear()
    upgrade_database()
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'notify.db').as_posix()}", future=True
    )
    yield async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    asyncio.run(engine.dispose())
    get_settings.cache_clear()


async def _наповнити(session, *, ціни: list[str], передплатники: list[dict], назва="Товар"):
    shop = Shop(slug="citrus", title="Цитрус", base_url="https://citrus.ua")
    session.add(shop)
    await session.flush()
    product = Product(shop_id=shop.id, name=назва, url=f"https://citrus.ua/p/{назва}.html")
    session.add(product)
    await session.flush()

    for і, ціна in enumerate(ціни):
        session.add(
            Price(
                product_id=product.id,
                price=D(ціна),
                currency="UAH",
                in_stock=True,
                parsed_at=ЗАРАЗ - timedelta(days=len(ціни) - і),
            )
        )

    for опис in передплатники:
        user = TelegramUser(
            telegram_id=опис["id"],
            chat_id=опис.get("chat_id", опис["id"]),
            username=опис.get("username"),
            is_active=опис.get("active", True),
        )
        session.add(user)
        await session.flush()
        if опис.get("subscribed", True):
            session.add(
                Subscription(
                    user_id=user.id,
                    product_id=product.id,
                    min_drop_percent=опис.get("threshold"),
                    is_muted=опис.get("muted", False),
                )
            )
    await session.flush()
    return product


def зібрати(factory, *, наповнення, now=ЗАРАЗ):
    async def _run():
        async with factory() as session:
            await наповнення(session)
            await session.commit()
        async with factory() as session:
            return await collect(session, rules=RULES, notify_rules=NOTIFY, now=now)

    return asyncio.run(_run())


# ------------------------------------------------------------ кому писати


def test_пише_підписнику_про_справжню_знижку(factory) -> None:
    async def наповнення(session):
        await _наповнити(
            session,
            ціни=["100", "100", "100", "100", "100", "70"],
            передплатники=[{"id": 111, "username": "перший"}],
        )

    outgoing = зібрати(factory, наповнення=наповнення)
    assert len(outgoing) == 1
    assert outgoing[0].user.telegram_id == 111
    assert outgoing[0].signal.drop_percent == D("30.00")
    assert "70.00" in outgoing[0].text
    assert "звично 100.00" in outgoing[0].text


def test_не_пише_тому_хто_не_підписаний(factory) -> None:
    """Головна перевірка на переплутування адресатів."""
    async def наповнення(session):
        await _наповнити(
            session,
            ціни=["100", "100", "100", "100", "100", "70"],
            передплатники=[
                {"id": 111, "username": "стежить"},
                {"id": 222, "username": "не стежить", "subscribed": False},
            ],
        )

    outgoing = зібрати(factory, наповнення=наповнення)
    assert [item.user.telegram_id for item in outgoing] == [111]


def test_двоє_підписників_отримують_кожен_своє(factory) -> None:
    async def наповнення(session):
        await _наповнити(
            session,
            ціни=["100", "100", "100", "100", "100", "70"],
            передплатники=[{"id": 111}, {"id": 222}],
            назва="Спільний",
        )

    outgoing = зібрати(factory, наповнення=наповнення)
    assert sorted(item.user.telegram_id for item in outgoing) == [111, 222]
    # кожному пишемо в його власний чат
    assert {item.chat_id for item in outgoing} == {111, 222}


def test_вимкненому_користувачу_не_пишемо(factory) -> None:
    """Той, хто натиснув /stop, не має отримувати нічого."""
    async def наповнення(session):
        await _наповнити(
            session,
            ціни=["100", "100", "100", "100", "100", "70"],
            передплатники=[{"id": 111, "active": False}],
        )

    assert зібрати(factory, наповнення=наповнення) == []


def test_приглушену_підписку_пропускаємо(factory) -> None:
    async def наповнення(session):
        await _наповнити(
            session,
            ціни=["100", "100", "100", "100", "100", "70"],
            передплатники=[{"id": 111, "muted": True}],
        )

    assert зібрати(factory, наповнення=наповнення) == []


# ---------------------------------------------------------- особистий поріг


def test_особистий_поріг_вищий_за_загальний(factory) -> None:
    """Падіння 20%: подія для того, хто просив від 10%, і дрібниця для 30%."""
    async def наповнення(session):
        await _наповнити(
            session,
            ціни=["100", "100", "100", "100", "100", "80"],
            передплатники=[
                {"id": 111, "username": "чутливий"},
                {"id": 222, "username": "терплячий", "threshold": D("30")},
            ],
        )

    outgoing = зібрати(factory, наповнення=наповнення)
    assert [item.user.telegram_id for item in outgoing] == [111]


def test_особистий_поріг_нижчий_за_загальний(factory) -> None:
    """Падіння 7% — загальний поріг мовчить, а той, хто просив від 5%, дізнається."""
    async def наповнення(session):
        await _наповнити(
            session,
            ціни=["100", "100", "100", "100", "100", "93"],
            передплатники=[
                {"id": 111},
                {"id": 222, "threshold": D("5")},
            ],
        )

    outgoing = зібрати(factory, наповнення=наповнення)
    assert [item.user.telegram_id for item in outgoing] == [222]


# --------------------------------------------------------------- мовчання


def test_мовчить_коли_знижки_немає(factory) -> None:
    async def наповнення(session):
        await _наповнити(
            session,
            ціни=["100", "100", "100", "100", "100", "98"],
            передплатники=[{"id": 111}],
        )

    assert зібрати(factory, наповнення=наповнення) == []


def test_мовчить_поки_історія_коротка(factory) -> None:
    async def наповнення(session):
        await _наповнити(
            session, ціни=["100", "100", "50"], передплатники=[{"id": 111}]
        )

    assert зібрати(factory, наповнення=наповнення) == []


def test_товар_без_наявності_пропускається(factory) -> None:
    async def наповнення(session):
        product = await _наповнити(
            session,
            ціни=["100", "100", "100", "100", "100"],
            передплатники=[{"id": 111}],
        )
        session.add(
            Price(
                product_id=product.id,
                price=D("70"),
                currency="UAH",
                in_stock=False,
                parsed_at=ЗАРАЗ,
            )
        )

    assert зібрати(factory, наповнення=наповнення) == []


# ----------------------------------------------------------------- журнал


def test_про_ту_саму_знижку_вдруге_не_пише(factory) -> None:
    """Наскрізна перевірка журналу: перший прогін пише, другий мовчить."""

    async def сценарій():
        async with factory() as session:
            await _наповнити(
                session,
                ціни=["100", "100", "100", "100", "100", "70"],
                передплатники=[{"id": 111}],
            )
            await session.commit()

        async with factory() as session:
            перший = await collect(session, rules=RULES, notify_rules=NOTIFY, now=ЗАРАЗ)
            for item in перший:
                await mark_sent(session, item, sent_at=ЗАРАЗ)
            await session.commit()

        async with factory() as session:
            другий = await collect(
                session, rules=RULES, notify_rules=NOTIFY, now=ЗАРАЗ + timedelta(days=1)
            )
            записів = len((await session.execute(select(Notification))).scalars().all())
        return len(перший), len(другий), записів

    перший, другий, записів = asyncio.run(сценарій())
    assert (перший, другий, записів) == (1, 0, 1)


def test_журнал_окремий_для_кожної_людини(factory) -> None:
    """Сказали одному — другий усе одно має дізнатись."""

    async def сценарій():
        async with factory() as session:
            product = await _наповнити(
                session,
                ціни=["100", "100", "100", "100", "100", "70"],
                передплатники=[{"id": 111}, {"id": 222}],
            )
            await session.commit()
            product_id = product.id

        async with factory() as session:
            перший = await collect(session, rules=RULES, notify_rules=NOTIFY, now=ЗАРАЗ)
            # позначаємо надісланим лише першому
            for item in перший:
                if item.user.telegram_id == 111:
                    await mark_sent(session, item, sent_at=ЗАРАЗ)
            await session.commit()

        async with factory() as session:
            другий = await collect(
                session, rules=RULES, notify_rules=NOTIFY, now=ЗАРАЗ + timedelta(days=1)
            )
        return [item.user.telegram_id for item in другий], product_id

    адресати, _ = asyncio.run(сценарій())
    assert адресати == [222]
