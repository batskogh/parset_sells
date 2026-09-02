"""Тести поведінки бота.

Telegram тут не потрібен: хендлери тонкі, а вся робота живе в
`app.bot.service` і працює зі звичайною сесією БД. Перевіряємо те, що людина
помітить: що її запам'ятали, що повторний /start не плодить дублікатів і що
після /stop можна повернутись.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

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
    assert set(BOT_COMMANDS) == {"start", "help", "list", "stop"}
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


# ------------------------------------------------------------- мій список


def список(factory, *, telegram_id: int = 5_093_284_241):
    async def _list():
        async with factory() as session:
            return await service.list_watched(session, telegram_id=telegram_id)

    return asyncio.run(_list())


def прибрати(factory, product_id: int, *, telegram_id: int = 5_093_284_241):
    async def _remove():
        async with factory() as session:
            name = await service.remove_watched(
                session, telegram_id=telegram_id, product_id=product_id
            )
            await session.commit()
            return name

    return asyncio.run(_remove())


def test_список_порожній_у_новачка(session_factory) -> None:
    assert список(session_factory) == []


def test_список_показує_додані_товари(session_factory, без_мережі) -> None:
    без_мережі["items"] = [
        {"name": "Чипси Люкс", "price": "74.00", "url": "https://maudau.com.ua/p/1"}
    ]
    додати(session_factory, "https://maudau.com.ua/p/1")

    items = список(session_factory)
    assert len(items) == 1
    assert items[0].name == "Чипси Люкс"
    assert items[0].price == Decimal("74.00")
    assert items[0].in_stock is True


def test_звичну_ціну_не_показуємо_поки_замірів_мало(session_factory, без_мережі) -> None:
    """На двох замірах «звична ціна» була б вигадкою."""
    без_мережі["items"] = [
        {"name": "Чипси", "price": "74.00", "url": "https://maudau.com.ua/p/1"}
    ]
    додати(session_factory, "https://maudau.com.ua/p/1")

    item = список(session_factory)[0]
    assert item.usual_price is None
    assert item.samples == 1


def test_звична_ціна_зявляється_коли_історії_досить(session_factory, без_мережі) -> None:
    без_мережі["items"] = [
        {"name": "Чипси", "price": "80.00", "url": "https://maudau.com.ua/p/1"}
    ]
    result = додати(session_factory, "https://maudau.com.ua/p/1")

    async def _add_history():
        from datetime import datetime, timedelta, timezone

        from app.db.models import Price

        async with session_factory() as session:
            now = datetime.now(timezone.utc)
            for i in range(5):
                session.add(
                    Price(
                        product_id=result.product_id,
                        price=Decimal("80.00"),
                        currency="UAH",
                        parsed_at=now - timedelta(days=5 - i),
                    )
                )
            await session.commit()

    asyncio.run(_add_history())

    item = список(session_factory)[0]
    assert item.usual_price == Decimal("80.00")
    assert item.samples >= 5


def test_видалення_прибирає_товар_зі_списку(session_factory, без_мережі) -> None:
    без_мережі["items"] = [
        {"name": "Чипси", "price": "74.00", "url": "https://maudau.com.ua/p/1"}
    ]
    result = додати(session_factory, "https://maudau.com.ua/p/1")

    assert прибрати(session_factory, result.product_id) == "Чипси"
    assert список(session_factory) == []


def test_чужий_товар_прибрати_не_можна(session_factory, без_мережі) -> None:
    """Кнопка з чужим id не має видаляти чужу підписку."""
    без_мережі["items"] = [
        {"name": "Чипси", "price": "74.00", "url": "https://maudau.com.ua/p/1"}
    ]
    result = додати(session_factory, "https://maudau.com.ua/p/1", telegram_id=111)
    додати(session_factory, "https://maudau.com.ua/p/1", telegram_id=222)

    # 222 намагається прибрати товар, вказавши той самий id — прибереться лише його підписка
    assert прибрати(session_factory, result.product_id, telegram_id=222) == "Чипси"
    assert список(session_factory, telegram_id=111) != []
    assert список(session_factory, telegram_id=222) == []


def test_видалення_не_чіпає_сам_товар_і_історію(session_factory, без_мережі) -> None:
    """Товар спільний, історія цін цінна — прибирається лише підписка."""
    без_мережі["items"] = [
        {"name": "Чипси", "price": "74.00", "url": "https://maudau.com.ua/p/1"}
    ]
    result = додати(session_factory, "https://maudau.com.ua/p/1")
    прибрати(session_factory, result.product_id)

    async def _counts():
        from app.db.models import Price, Product

        async with session_factory() as session:
            products = (await session.execute(select(Product))).scalars().all()
            prices = (await session.execute(select(Price))).scalars().all()
            return len(products), len(prices)

    assert asyncio.run(_counts()) == (1, 1)


def test_видалення_неіснуючого(session_factory) -> None:
    assert прибрати(session_factory, 999) is None


# --------------------------------------------------------------- клавіатура


def test_кнопки_відповідають_рядкам_списку() -> None:
    from app.bot.keyboards import parse_remove_callback, watch_list_keyboard
    from app.bot.service import WatchedItem

    items = [
        WatchedItem(і, f"Товар {і}", "https://x.ua", "Ш", Decimal("10"), "UAH", True, None, 1)
        for і in (11, 22, 33, 44, 55)
    ]
    keyboard = watch_list_keyboard(items)

    кнопки = [b for row in keyboard.inline_keyboard for b in row]
    assert [b.text for b in кнопки] == ["✕ 1", "✕ 2", "✕ 3", "✕ 4", "✕ 5"]
    assert [parse_remove_callback(b.callback_data) for b in кнопки] == [11, 22, 33, 44, 55]
    assert len(keyboard.inline_keyboard) == 2      # 4 в рядку, далі перенос


def test_чужий_callback_ігнорується() -> None:
    from app.bot.keyboards import parse_remove_callback

    assert parse_remove_callback(None) is None
    assert parse_remove_callback("щось інше") is None
    assert parse_remove_callback("rm:не_число") is None
    assert parse_remove_callback("rm:42") == 42
