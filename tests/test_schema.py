"""Тести схеми бази й міграцій.

Перевіряється не «чи створились таблиці» (це видно й так), а поведінка, на яку
спиратиметься бот: що один товар спільний для всіх підписників, що повторна
підписка неможлива, що видалення людини не лишає сиріт і що ідентифікатор
Telegram не обрізається.

Кожен тест працює на власній тимчасовій базі, піднятій справжніми міграціями —
тими самими, що поїдуть на робочу базу.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.migrate import BASELINE_REVISION, upgrade_database
from app.db.models import Price, Product, Shop, Subscription, TelegramUser


@pytest.fixture
def db_url(tmp_path, monkeypatch):
    """Тимчасова база, доведена до останньої ревізії справжніми міграціями."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    get_settings.cache_clear()
    upgrade_database()
    yield f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    get_settings.cache_clear()


@pytest.fixture
def session(db_url):
    engine = create_engine(db_url, future=True)

    # без цього SQLite мовчки ігнорує ON DELETE CASCADE
    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    with Session(engine) as session:
        yield session
    engine.dispose()


def зробити_товар(session: Session, *, url: str = "https://shop.ua/p/1") -> Product:
    shop = Shop(slug="shop", title="Крамниця", base_url="https://shop.ua")
    session.add(shop)
    session.flush()
    product = Product(shop_id=shop.id, name="Товар", url=url)
    session.add(product)
    session.flush()
    return product


def зробити_людину(session: Session, *, telegram_id: int, username: str = "hto") -> TelegramUser:
    user = TelegramUser(telegram_id=telegram_id, chat_id=telegram_id, username=username)
    session.add(user)
    session.flush()
    return user


# ------------------------------------------------------------------ міграції


def test_міграції_піднімають_усі_таблиці(db_url) -> None:
    engine = create_engine(db_url, future=True)
    tables = set(inspect(engine).get_table_names())
    engine.dispose()
    assert {"shops", "products", "prices", "telegram_users", "subscriptions"} <= tables
    assert "alembic_version" in tables


def test_база_зі_спадщини_не_втрачає_дані(tmp_path, monkeypatch) -> None:
    """Головний ризик усієї затії: база, створена ще create_all.

    У неї є таблиці, але немає версії alembic. Міграція має розпізнати це,
    проставити baseline і накотити лише нове — не чіпаючи історію цін.
    """
    from sqlalchemy import text

    db_path = tmp_path / "legacy.db"
    legacy = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    with legacy.begin() as conn:
        # спрощена подоба старої схеми: важливо, що є `prices` і немає alembic_version
        conn.execute(text("CREATE TABLE shops (id INTEGER PRIMARY KEY, slug TEXT)"))
        conn.execute(text("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT)"))
        conn.execute(text("CREATE TABLE prices (id INTEGER PRIMARY KEY, price NUMERIC)"))
        conn.execute(text("INSERT INTO prices (price) VALUES (74.00), (39199.00)"))
    legacy.dispose()

    monkeypatch.setenv("DB_PATH", str(db_path))
    get_settings.cache_clear()
    action = upgrade_database()
    get_settings.cache_clear()

    assert action == "stamped"
    check = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    with check.begin() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM prices")).scalar_one() == 2
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() != (
            BASELINE_REVISION
        )
        assert "telegram_users" in set(inspect(check).get_table_names())
    check.dispose()


def test_повторний_запуск_нічого_не_ламає(db_url) -> None:
    assert upgrade_database() == "upgraded"


# ------------------------------------------------------- користувач і підписка


def test_великий_telegram_id_не_обрізається(session: Session) -> None:
    """Ідентифікатори Telegram давно за межами 32 біт."""
    величезний = 8_123_456_789_012
    зробити_людину(session, telegram_id=величезний)
    session.commit()

    found = session.scalar(select(TelegramUser).where(TelegramUser.telegram_id == величезний))
    assert found is not None
    assert found.telegram_id == величезний


def test_двоє_людей_стежать_за_одним_товаром(session: Session) -> None:
    """Товар у базі один, історія спільна — персональна лише підписка."""
    product = зробити_товар(session)
    перший = зробити_людину(session, telegram_id=111, username="перший")
    другий = зробити_людину(session, telegram_id=222, username="другий")
    session.add_all(
        [
            Subscription(user_id=перший.id, product_id=product.id),
            Subscription(user_id=другий.id, product_id=product.id),
        ]
    )
    session.commit()

    assert session.scalar(select(Product).where(Product.id == product.id)) is not None
    assert len(session.scalars(select(Subscription)).all()) == 2
    assert len(session.scalars(select(Product)).all()) == 1


def test_повторна_підписка_неможлива(session: Session) -> None:
    product = зробити_товар(session)
    user = зробити_людину(session, telegram_id=333)
    session.add(Subscription(user_id=user.id, product_id=product.id))
    session.commit()

    session.add(Subscription(user_id=user.id, product_id=product.id))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_видалення_людини_прибирає_її_підписки(session: Session) -> None:
    """Але не сам товар: за ним можуть стежити інші."""
    product = зробити_товар(session)
    user = зробити_людину(session, telegram_id=444)
    session.add(Subscription(user_id=user.id, product_id=product.id))
    session.commit()

    session.delete(user)
    session.commit()

    assert session.scalars(select(Subscription)).all() == []
    assert session.scalar(select(Product).where(Product.id == product.id)) is not None


def test_видалення_товару_прибирає_підписки_й_ціни(session: Session) -> None:
    product = зробити_товар(session)
    user = зробити_людину(session, telegram_id=555)
    session.add_all(
        [
            Subscription(user_id=user.id, product_id=product.id),
            Price(product_id=product.id, price=74, currency="UAH", parsed_at=datetime.now(timezone.utc)),
        ]
    )
    session.commit()

    session.delete(product)
    session.commit()

    assert session.scalars(select(Subscription)).all() == []
    assert session.scalars(select(Price)).all() == []
    assert session.scalar(select(TelegramUser).where(TelegramUser.id == user.id)) is not None


def test_власний_поріг_підписки(session: Session) -> None:
    """Порожнє поле означає «як налаштовано глобально», число — власний поріг."""
    product = зробити_товар(session)
    user = зробити_людину(session, telegram_id=666)
    session.add(Subscription(user_id=user.id, product_id=product.id, min_drop_percent=25))
    session.commit()

    subscription = session.scalars(select(Subscription)).one()
    assert subscription.min_drop_percent == 25
    assert subscription.is_muted is False


# ------------------------------------------------------------ журнал сповіщень


def test_журнал_зберігає_чому_сповістили(session: Session) -> None:
    """Не лише ціну, а й моду: інакше не зрозуміти, звідки взявся відсоток."""
    from decimal import Decimal

    from app.db.models import Notification

    product = зробити_товар(session)
    user = зробити_людину(session, telegram_id=777)
    session.add(
        Notification(
            user_id=user.id,
            product_id=product.id,
            price=Decimal("80"),
            mode_price=Decimal("100"),
            drop_percent=Decimal("20"),
            sent_at=datetime.now(timezone.utc),
        )
    )
    session.commit()

    saved = session.scalars(select(Notification)).one()
    assert saved.price == Decimal("80")
    assert saved.mode_price == Decimal("100")
    assert saved.drop_percent == Decimal("20")


def test_видалення_людини_прибирає_і_журнал(session: Session) -> None:
    from decimal import Decimal

    from app.db.models import Notification

    product = зробити_товар(session)
    user = зробити_людину(session, telegram_id=888)
    session.add(
        Notification(
            user_id=user.id,
            product_id=product.id,
            price=Decimal("80"),
            mode_price=Decimal("100"),
            drop_percent=Decimal("20"),
        )
    )
    session.commit()

    session.delete(user)
    session.commit()
    assert session.scalars(select(Notification)).all() == []


def test_журнал_і_правило_разом_не_дають_повторів(session: Session) -> None:
    """Наскрізна перевірка: записали відправку — наступного дня мовчимо.

    Саме так це працюватиме в боті: детектор знайшов знижку, журнал сказав,
    чи вже про неї говорили.
    """
    from decimal import Decimal

    from app.core.notify_log import LastNotification, NotifyRules, should_notify
    from app.db.models import Notification

    product = зробити_товар(session)
    user = зробити_людину(session, telegram_id=999)
    rules = NotifyRules(cooldown_hours=24, deeper_by_percent=Decimal("10"))
    now = datetime.now(timezone.utc)

    # перший раз — журнал порожній, кажемо
    перше = should_notify(
        current=Decimal("80"), last=None, recovery_price=Decimal("90"), rules=rules, now=now
    )
    assert перше.send is True

    session.add(
        Notification(
            user_id=user.id,
            product_id=product.id,
            price=Decimal("80"),
            mode_price=Decimal("100"),
            drop_percent=Decimal("20"),
            sent_at=now,
        )
    )
    session.commit()

    # наступного дня ціна та сама — журнал каже мовчати
    saved = session.scalars(select(Notification)).one()
    друге = should_notify(
        current=Decimal("80"),
        last=LastNotification(price=saved.price, sent_at=saved.sent_at),
        prices_since_last=[Decimal("80")],
        recovery_price=Decimal("90"),
        rules=rules,
        now=now + timedelta(days=1),
    )
    assert друге.send is False
    assert "вже казали" in друге.reason
