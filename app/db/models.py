"""Моделі БД: магазин -> товар -> історія цін."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Shop(Base):
    """Магазин (сайт), з якого беруться дані."""

    __tablename__ = "shops"

    id: Mapped[int] = mapped_column(primary_key=True)
    # slug збігається з `name` парсера — саме за ним прив'язується парсер до магазину
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    base_url: Mapped[str] = mapped_column(String(512), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # коли власнику сказали, що магазин мовчить. Порожньо — або все гаразд,
    # або про мовчання ще не казали; заповнено — щоб не повторювати щодня
    silence_alert_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    products: Mapped[list["Product"]] = relationship(
        back_populates="shop",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Shop {self.slug}>"


class Product(Base):
    """Товар у конкретному магазині."""

    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("shop_id", "url", name="uq_product_shop_url"),
        Index("ix_product_shop_external", "shop_id", "external_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(
        ForeignKey("shops.id", ondelete="CASCADE"), index=True
    )
    # ідентифікатор товару в межах магазину (артикул/SKU), якщо сайт його віддає
    external_id: Mapped[str | None] = mapped_column(String(128), default=None)
    name: Mapped[str] = mapped_column(String(512))
    url: Mapped[str] = mapped_column(String(1024))
    image_url: Mapped[str | None] = mapped_column(String(1024), default=None)
    category: Mapped[str | None] = mapped_column(String(255), default=None)
    notes: Mapped[str | None] = mapped_column(Text, default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    shop: Mapped["Shop"] = relationship(back_populates="products", lazy="joined")
    prices: Mapped[list["Price"]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Price.parsed_at.desc()",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Product {self.id} {self.name[:40]!r}>"


class Price(Base):
    """Зріз ціни на конкретний момент часу."""

    __tablename__ = "prices"
    __table_args__ = (Index("ix_price_product_time", "product_id", "parsed_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    old_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), default=None)
    currency: Mapped[str] = mapped_column(String(8), default="UAH")
    in_stock: Mapped[bool] = mapped_column(Boolean, default=True)
    parsed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), index=True
    )

    product: Mapped["Product"] = relationship(back_populates="prices")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Price {self.price} {self.currency} @ {self.parsed_at}>"


class TelegramUser(Base):
    """Людина, що користується ботом.

    `telegram_id` — це BigInteger навмисно: ідентифікатори Telegram давно
    вийшли за межі 32 біт, і на звичайному Integer частина акаунтів просто не
    збережеться. `chat_id` тримаємо окремо від `telegram_id`, хоч у приватних
    чатах вони збігаються: у групі це різні числа, і сповіщення йде саме в чат.
    """

    __tablename__ = "telegram_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    username: Mapped[str | None] = mapped_column(String(64), default=None)
    first_name: Mapped[str | None] = mapped_column(String(128), default=None)
    # вимкнено — коли людина натиснула «стоп» або заблокувала бота
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    subscriptions: Mapped[list["Subscription"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TelegramUser {self.telegram_id} {self.username or ''}>"


class Subscription(Base):
    """Хто за яким товаром стежить.

    Один товар у базі — один рядок у `products`, скільки б людей за ним не
    стежило: історія цін спільна, а персональна тут лише підписка. Тому
    парсер ходить на сторінку один раз, а сповіщення отримують усі підписники.

    `min_drop_percent` дозволяє власний поріг: комусь цікаво від 5%, комусь
    від 30%. Порожнє поле означає «як налаштовано глобально».
    """

    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint("user_id", "product_id", name="uq_subscription_user_product"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_users.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    min_drop_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), default=None)
    # тимчасово не турбувати, не втрачаючи сам товар зі списку
    is_muted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped["TelegramUser"] = relationship(back_populates="subscriptions")
    product: Mapped["Product"] = relationship(lazy="joined")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Subscription user={self.user_id} product={self.product_id}>"


class Notification(Base):
    """Журнал надісланих сповіщень: кому, про що і за якою ціною.

    Потрібен не для звітності, а щоб не турбувати людину двічі тим самим.
    Знижка триває днями, а перевірка йде щодня — без журналу одне падіння
    ціни перетворилось би на щоденне нагадування, і бота б вимкнули.

    Зберігаємо не лише ціну, а й моду на момент відправки: поріг рахувався
    саме від неї, і без цього неможливо зрозуміти, чому сповіщення пішло.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notification_user_product_time", "user_id", "product_id", "sent_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_users.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    # ціна, про яку сповістили, і звична ціна на той момент
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    mode_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    drop_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    user: Mapped["TelegramUser"] = relationship()
    product: Mapped["Product"] = relationship()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Notification user={self.user_id} product={self.product_id} {self.price}>"


class ShopRequest(Base):
    """Посилання на магазин, якого парсер ще не знає.

    Кожна така спроба — готова заявка на розширення: людина показала, за чим
    хоче стежити, і зробила це раніше, ніж встигла б написати про це окремо.
    Без запису вони губляться в чаті, і черга нових магазинів вибудовується
    навмання замість того, щоб іти за попитом.

    Рядок на кожну спробу, а не лічильник на домен: так видно і скільки разів
    просили, і скільки різних людей — а це різні речі. Одна людина, що десять
    разів кинула те саме посилання, і десятеро різних людей — це два різні
    сигнали, і зводити їх в одне число означало б втратити той, що важливіший.

    `notified_at` ставиться лише на тому рядку, який спричинив повідомлення
    власнику: за ним видно, коли про домен казали востаннє, і саме він не дає
    писати про той самий магазин після кожного посилання.
    """

    __tablename__ = "shop_requests"
    __table_args__ = (Index("ix_shop_request_host_time", "host", "requested_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # домен без www — саме він, а не повна адреса, визначає «той самий магазин»
    host: Mapped[str] = mapped_column(String(255), index=True)
    url: Mapped[str] = mapped_column(Text)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("telegram_users.id", ondelete="SET NULL"), index=True, default=None
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    user: Mapped["TelegramUser | None"] = relationship()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ShopRequest {self.host} user={self.user_id}>"


class ShopRun(Base):
    """Один прогін одного магазину: коли, скільки товарів, скільки помилок.

    Потрібен, щоб відповісти на питання «коли цей магазин востаннє справді
    читався». Побічними ознаками це не визначається надійно: `prices.parsed_at`
    перестає оновлюватись, щойно хтось увімкне `STORE_PRICE_ONLY_ON_CHANGE`,
    а `products.updated_at` не рухається, поки в картці нічого не змінилось.
    Обидва мовчали б однаково і про здоровий магазин зі стабільною ціною, і
    про мертвий.

    Ключ — `shop_slug`, а не посилання на `shops`: магазин, який жодного разу
    не віддав товару, у таблиці магазинів може й не з'явитись, а знати про
    його мовчання треба саме тоді.
    """

    __tablename__ = "shop_runs"
    __table_args__ = (Index("ix_shop_run_slug_time", "shop_slug", "finished_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_slug: Mapped[str] = mapped_column(String(64), index=True)
    items: Mapped[int] = mapped_column(default=0)
    errors: Mapped[int] = mapped_column(default=0)
    # перша помилка прогону: повний список у журналі, тут — щоб було що показати
    error_text: Mapped[str | None] = mapped_column(Text, default=None)
    duration: Mapped[float] = mapped_column(default=0.0)
    finished_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    @property
    def productive(self) -> bool:
        """Прогін вважається вдалим, якщо магазин щось віддав."""
        return self.items > 0

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ShopRun {self.shop_slug} товарів={self.items} помилок={self.errors}>"
