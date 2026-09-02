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


# ------------------------------------------------- додавання товару за посиланням


@pytest.fixture
def без_мережі(monkeypatch):
    """Підміна походу на сайт: тести не мають залежати від живих магазинів."""
    from decimal import Decimal

    from app.parsers.models import ParsedProduct, ParseResult

    стан = {"items": [], "errors": []}

    async def fake_fetch(parser_class, url):
        result = ParseResult(shop_slug=parser_class.name)
        result.errors = list(стан["errors"])
        for дані in стан["items"]:
            result.items.append(
                ParsedProduct(
                    name=дані.get("name", "Товар"),
                    url=дані.get("url", url),
                    price=Decimal(str(дані.get("price", "100"))),
                    currency=дані.get("currency", "UAH"),
                    in_stock=дані.get("in_stock", True),
                    external_id=дані.get("external_id"),
                )
            )
        return result

    monkeypatch.setattr(service, "_fetch_single", fake_fetch)
    return стан


def додати(factory, текст: str, *, telegram_id: int = 5_093_284_241):
    async def _add():
        async with factory() as session:
            result = await service.add_product_by_url(
                session, telegram_id=telegram_id, chat_id=telegram_id, message_text=текст
            )
            await session.commit()
            return result

    return asyncio.run(_add())


def test_повідомлення_без_посилання(session_factory, без_мережі) -> None:
    result = додати(session_factory, "привіт, як справи?")
    assert result.ok is False
    assert "Не бачу посилання" in result.text


def test_невідомий_магазин_дістає_перелік(session_factory, без_мережі) -> None:
    result = додати(session_factory, "https://hard.rozetka.com.ua/ua/x/p1/")
    assert result.ok is False
    assert "поки не вмію" in result.text
    assert "maudau.com.ua" in result.text        # перелік того, що вміє


def test_вимкнений_магазин_пояснює_причину(session_factory, без_мережі) -> None:
    """Магазин відомий, але за антибот-захистом — це інша відмова, ніж «не знаю»."""
    result = додати(session_factory, "https://www.staff-clothes.com/product/9en0116/")
    assert result.ok is False
    assert "антибот" in result.text


def test_каталожний_магазин(session_factory, без_мережі) -> None:
    result = додати(session_factory, "https://books.toscrape.com/catalogue/page-1.html")
    assert result.ok is False
    assert "каталогом" in result.text


def test_сторінка_без_ціни(session_factory, без_мережі) -> None:
    без_мережі["errors"] = ["Немає розмітки Product у ld+json: https://citrus.ua/about/"]
    result = додати(session_factory, "https://citrus.ua/about/")
    assert result.ok is False
    assert "Не вдалося прочитати ціну" in result.text
    assert "ld+json" in result.text              # причина, а не самі вибачення


def test_товар_додається_і_підписка_створюється(session_factory, без_мережі) -> None:

    без_мережі["items"] = [
        {"name": "Чипси Люкс", "price": "74.00", "url": "https://maudau.com.ua/product/chipsy-872080"}
    ]
    result = додати(session_factory, "https://maudau.com.ua/product/chipsy-872080")

    assert result.ok is True
    assert "Стежу за товаром" in result.text
    assert "74.00 UAH" in result.text

    async def _counts():
        from app.db.models import Product, Subscription

        async with session_factory() as session:
            products = (await session.execute(select(Product))).scalars().all()
            subs = (await session.execute(select(Subscription))).unique().scalars().all()
            return len(products), len(subs)

    assert asyncio.run(_counts()) == (1, 1)


def test_повторне_посилання_не_плодить_дублікатів(session_factory, без_мережі) -> None:
    без_мережі["items"] = [
        {"name": "Чипси Люкс", "price": "74.00", "url": "https://maudau.com.ua/product/chipsy-872080"}
    ]
    додати(session_factory, "https://maudau.com.ua/product/chipsy-872080")
    result = додати(session_factory, "https://maudau.com.ua/product/chipsy-872080")

    assert result.ok is True
    assert "вже стежу" in result.text

    async def _counts():
        from app.db.models import Product, Subscription

        async with session_factory() as session:
            products = (await session.execute(select(Product))).scalars().all()
            subs = (await session.execute(select(Subscription))).unique().scalars().all()
            return len(products), len(subs)

    assert asyncio.run(_counts()) == (1, 1)


def test_двоє_людей_на_один_товар_дають_один_товар(session_factory, без_мережі) -> None:
    """Товар спільний, підписки різні — саме так задумана схема."""
    без_мережі["items"] = [
        {"name": "Чипси Люкс", "price": "74.00", "url": "https://maudau.com.ua/product/chipsy-872080"}
    ]
    додати(session_factory, "https://maudau.com.ua/product/chipsy-872080", telegram_id=111)
    додати(session_factory, "https://maudau.com.ua/product/chipsy-872080", telegram_id=222)

    async def _counts():
        from app.db.models import Product, Subscription

        async with session_factory() as session:
            products = (await session.execute(select(Product))).scalars().all()
            subs = (await session.execute(select(Subscription))).unique().scalars().all()
            return len(products), len(subs)

    assert asyncio.run(_counts()) == (1, 2)


def test_рекламний_хвіст_не_створює_другий_товар(session_factory, без_мережі) -> None:
    """Те саме посилання з реклами й із сайту — один товар, не два."""
    чистий = "https://maudau.com.ua/product/chipsy-872080"
    без_мережі["items"] = [{"name": "Чипси Люкс", "price": "74.00", "url": чистий}]

    додати(session_factory, чистий)
    додати(session_factory, f"{чистий}?utm_source=telegram&gclid=ABC")

    async def _count_products():
        from app.db.models import Product

        async with session_factory() as session:
            return len((await session.execute(select(Product))).scalars().all())

    assert asyncio.run(_count_products()) == 1


def test_товару_немає_в_наявності_про_це_кажуть(session_factory, без_мережі) -> None:
    без_мережі["items"] = [
        {"name": "Відеокарта", "price": "39199.00", "in_stock": False,
         "url": "https://citrus.ua/videokarti/x.html"}
    ]
    result = додати(session_factory, "https://citrus.ua/videokarti/x.html")
    assert result.ok is True
    assert "немає в наявності" in result.text


def test_назва_товару_екранується(session_factory, без_мережі) -> None:
    """Відповідь іде з розміткою HTML — кутові дужки в назві її зламали б."""
    без_мережі["items"] = [
        {"name": 'Товар <b>жирний</b> & "лапки"', "price": "10.00",
         "url": "https://citrus.ua/p/x.html"}
    ]
    result = додати(session_factory, "https://citrus.ua/p/x.html")
    assert "&lt;b&gt;" in result.text
    assert "&amp;" in result.text
