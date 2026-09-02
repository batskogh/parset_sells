"""Тести перевірки здоров'я магазинів.

Магазин ламається тихо: парсер чесно повертає нуль товарів, прогін іде далі,
у журналі все зелене. Тут перевіряється, що це мовчання стає подією — і що
воно не перетворюється на щоденне буркотіння, коли магазин зламаний давно й
надовго.

Окремо перевіряється зворотний бік: коли магазин ожив, про це теж кажуть.
Без цього людина не знає, коли можна перестати хвилюватись, і мусить
перевіряти сама — тобто робить рівно те, від чого її звільняли.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.bot.texts import shop_silent_alert, доби
from app.config import get_settings
from app.core.health import (
    RECOVERED,
    SILENT,
    HealthRules,
    ShopState,
    check_shops,
    collect_states,
    mark_told,
    record_runs,
)
from app.core.runner import RunSummary
from app.db import repository as repo
from app.db.migrate import upgrade_database
from app.db.models import Price, Product, Shop
from app.parsers.models import ParsedProduct, ParseResult

ЗАРАЗ = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
ПРАВИЛА = HealthRules(silent_days=2, repeat_hours=24)


def магазин(
    *,
    slug="citrus",
    products=5,
    останній_успіх=None,
    сказали=None,
    відомий_з=None,
    помилка=None,
) -> ShopState:
    return ShopState(
        shop_id=1,
        slug=slug,
        title=slug.title(),
        products=products,
        known_since=відомий_з or (ЗАРАЗ - timedelta(days=30)),
        last_success=останній_успіх,
        alerted_at=сказали,
        last_error=помилка,
    )


def перевірити(*states, коли=ЗАРАЗ):
    return check_shops(states, rules=ПРАВИЛА, now=коли)


# ------------------------------------------------------------ мовчання помічене


def test_свіжий_магазин_нікого_не_турбує() -> None:
    assert перевірити(магазин(останній_успіх=ЗАРАЗ - timedelta(hours=6))) == []


def test_магазин_що_мовчить_довше_порогу_породжує_подію() -> None:
    вердикти = перевірити(магазин(останній_успіх=ЗАРАЗ - timedelta(days=3)))

    assert len(вердикти) == 1
    assert вердикти[0].kind == SILENT
    assert вердикти[0].silent_days == 3


def test_поріг_рахується_від_діб_а_не_від_прогонів() -> None:
    """Обхід іде чотири рази на добу — але терпіння міряється в добах."""
    майже = перевірити(магазин(останній_успіх=ЗАРАЗ - timedelta(days=1, hours=23)))
    вже = перевірити(магазин(останній_успіх=ЗАРАЗ - timedelta(days=2, hours=1)))

    assert майже == []
    assert len(вже) == 1


def test_магазин_що_ніколи_нічого_не_дав_рахується_від_дня_появи() -> None:
    """Інакше про зламаний з першого дня не сказали б ніколи."""
    вердикти = перевірити(
        магазин(останній_успіх=None, відомий_з=ЗАРАЗ - timedelta(days=5))
    )

    assert len(вердикти) == 1
    assert вердикти[0].silent_days == 5


def test_щойно_доданий_магазин_має_час_на_перший_прогін() -> None:
    assert перевірити(магазин(останній_успіх=None, відомий_з=ЗАРАЗ - timedelta(hours=2))) == []


def test_магазин_без_товарів_мовчить_законно() -> None:
    """Стежити нема за чим — мовчання порожнього магазину це норма."""
    assert перевірити(магазин(products=0, останній_успіх=ЗАРАЗ - timedelta(days=10))) == []


# --------------------------------------------------- про те саме не кажуть щодня


def test_після_сказаного_мовчимо_до_кінця_паузи() -> None:
    вердикти = перевірити(
        магазин(
            останній_успіх=ЗАРАЗ - timedelta(days=5),
            сказали=ЗАРАЗ - timedelta(hours=3),
        )
    )

    assert вердикти == [], "розсилка йде чотири рази на добу — нагадувати щоразу зайве"


def test_після_паузи_нагадуємо_знову() -> None:
    вердикти = перевірити(
        магазин(
            останній_успіх=ЗАРАЗ - timedelta(days=5),
            сказали=ЗАРАЗ - timedelta(hours=25),
        )
    )

    assert len(вердикти) == 1
    assert вердикти[0].kind == SILENT


# ------------------------------------------------------------------- одужання


def test_магазин_що_ожив_після_тривоги_дає_подію_про_одужання() -> None:
    вердикти = перевірити(
        магазин(
            останній_успіх=ЗАРАЗ - timedelta(hours=2),
            сказали=ЗАРАЗ - timedelta(days=3),
        )
    )

    assert len(вердикти) == 1
    assert вердикти[0].kind == RECOVERED


def test_здоровий_магазин_про_який_не_тривожили_мовчить() -> None:
    """Про одужання кажуть лише тому, кого перед тим лякали."""
    assert перевірити(магазин(останній_успіх=ЗАРАЗ - timedelta(hours=2), сказали=None)) == []


# ------------------------------------------------------------- кілька магазинів


def test_кожен_магазин_оцінюється_окремо() -> None:
    вердикти = перевірити(
        магазин(slug="citrus", останній_успіх=ЗАРАЗ - timedelta(hours=1)),
        магазин(slug="maudau", останній_успіх=ЗАРАЗ - timedelta(days=4)),
        магазин(slug="demo", останній_успіх=ЗАРАЗ - timedelta(days=9), сказали=ЗАРАЗ),
    )

    assert [(v.shop.slug, v.kind) for v in вердикти] == [("maudau", SILENT)]


def test_текст_помилки_доїжджає_до_вердикту() -> None:
    """Щоб у повідомленні було видно причину, а не лише факт."""
    вердикти = перевірити(
        магазин(останній_успіх=ЗАРАЗ - timedelta(days=3), помилка="403 cf-mitigated: challenge")
    )

    assert вердикти[0].shop.last_error == "403 cf-mitigated: challenge"


def test_наївний_час_із_бази_не_ламає_порівняння() -> None:
    """SQLite віддає datetime без часового поясу — це не привід падати."""
    вердикти = перевірити(
        магазин(останній_успіх=(ЗАРАЗ - timedelta(days=3)).replace(tzinfo=None))
    )

    assert len(вердикти) == 1


# ------------------------------------------------ журнал прогонів у справжній базі


@pytest.fixture
def factory(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "health.db"))
    get_settings.cache_clear()
    upgrade_database()
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'health.db').as_posix()}", future=True
    )
    yield async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    asyncio.run(engine.dispose())
    get_settings.cache_clear()


def _підсумок(*, slug: str, товарів: int, помилка: str | None = None) -> RunSummary:
    items = [
        ParsedProduct(name=f"Товар {і}", url=f"https://x/{і}", price=Decimal("100"))
        for і in range(товарів)
    ]
    summary = RunSummary()
    summary.results.append(
        ParseResult(shop_slug=slug, items=items, errors=[помилка] if помилка else [])
    )
    return summary


def test_журнал_прогонів_пам_ятає_останній_успіх(factory) -> None:
    async def _run():
        async with factory() as session:
            await record_runs(
                session,
                _підсумок(slug="citrus", товарів=3),
                finished_at=ЗАРАЗ - timedelta(days=1),
            )
            await record_runs(
                session,
                _підсумок(slug="citrus", товарів=0, помилка="403"),
                finished_at=ЗАРАЗ,
            )
            await session.commit()
        async with factory() as session:
            return (
                await repo.last_successful_runs(session),
                await repo.last_runs(session),
            )

    успіхи, останні = asyncio.run(_run())

    assert успіхи["citrus"].replace(tzinfo=timezone.utc) == ЗАРАЗ - timedelta(days=1)
    assert останні["citrus"].items == 0
    assert останні["citrus"].error_text == "403"


def test_магазин_якого_не_вдалося_підготувати_теж_у_журналі(factory) -> None:
    """Для стеження за мовчанням байдуже, чому саме магазин не читався."""

    async def _run():
        async with factory() as session:
            await record_runs(
                session, RunSummary(), failed_to_build=[("maudau", "база не відповіла")]
            )
            await session.commit()
        async with factory() as session:
            return await repo.list_shop_runs(session)

    прогони = asyncio.run(_run())
    assert [(p.shop_slug, p.items, p.error_text) for p in прогони] == [
        ("maudau", 0, "база не відповіла")
    ]


def test_зліпок_стану_збирає_все_потрібне(factory) -> None:
    async def _run():
        async with factory() as session:
            shop = Shop(slug="citrus", title="Цитрус", base_url="https://citrus.ua")
            session.add(shop)
            await session.flush()
            product = Product(shop_id=shop.id, name="Товар", url="https://citrus.ua/p")
            session.add(product)
            await session.flush()
            session.add(Price(product_id=product.id, price=Decimal("100")))
            await record_runs(
                session,
                _підсумок(slug="citrus", товарів=1),
                finished_at=ЗАРАЗ - timedelta(days=4),
            )
            await session.commit()
        async with factory() as session:
            return await collect_states(session)

    стани = asyncio.run(_run())

    assert len(стани) == 1
    стан = стани[0]
    assert стан.slug == "citrus"
    assert стан.products == 1
    assert стан.alerted_at is None
    assert check_shops([стан], rules=ПРАВИЛА, now=ЗАРАЗ)[0].kind == SILENT


def test_мітка_ставиться_і_знімається(factory) -> None:
    async def _run():
        async with factory() as session:
            shop = Shop(slug="citrus", title="Цитрус")
            session.add(shop)
            await session.flush()
            product = Product(shop_id=shop.id, name="Товар", url="https://citrus.ua/p")
            session.add(product)
            await session.commit()
            shop_id = shop.id

        async with factory() as session:
            стан = ShopState(
                shop_id=shop_id,
                slug="citrus",
                title="Цитрус",
                products=1,
                known_since=ЗАРАЗ - timedelta(days=30),
                last_success=None,
                alerted_at=None,
            )
            вердикт = check_shops([стан], rules=ПРАВИЛА, now=ЗАРАЗ)[0]
            await mark_told(session, вердикт, now=ЗАРАЗ)
            await session.commit()

        async with factory() as session:
            після_тривоги = (await collect_states(session))[0].alerted_at
            одужання = check_shops(
                [
                    ShopState(
                        shop_id=shop_id,
                        slug="citrus",
                        title="Цитрус",
                        products=1,
                        known_since=ЗАРАЗ - timedelta(days=30),
                        last_success=ЗАРАЗ,
                        alerted_at=ЗАРАЗ,
                    )
                ],
                rules=ПРАВИЛА,
                now=ЗАРАЗ + timedelta(hours=1),
            )[0]
            await mark_told(session, одужання)
            await session.commit()

        async with factory() as session:
            return після_тривоги, (await collect_states(session))[0].alerted_at

    після_тривоги, після_одужання = asyncio.run(_run())

    assert після_тривоги is not None
    assert після_одужання is None, "після одужання мітка має зникнути"


# ------------------------------------------------------------------ формулювання


@pytest.mark.parametrize(
    "кількість,очікуване",
    [(1, "1 доба"), (2, "2 доби"), (5, "5 діб"), (11, "11 діб"), (21, "21 доба")],
)
def test_доби_відмінюються_як_українською(кількість, очікуване) -> None:
    """Бот, який пише «3 доб(и)», виглядає як бланк, а не як співрозмовник."""
    assert доби(кількість) == очікуване


def test_у_тривозі_видно_і_причину_і_втрату() -> None:
    текст = shop_silent_alert(
        title="Цитрус", slug="citrus", days=3, products=12, error="403 Forbidden"
    )

    assert "3 доби" in текст
    assert "12" in текст, "кількість товарів робить втрату зрозумілою"
    assert "403 Forbidden" in текст
    assert "--site citrus" in текст, "команда для перевірки має бути готовою до вставки"
