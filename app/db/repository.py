"""Шар доступу до даних: CRUD для магазинів, товарів і цін.

Використовується і парсером, і веб-інтерфейсом — щоб логіка запису була одна.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Select, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.db.models import (
    Notification,
    Price,
    Product,
    Shop,
    Subscription,
    TelegramUser,
    utcnow,
)

# ---------------------------------------------------------------- магазини


async def get_shop(session: AsyncSession, shop_id: int) -> Shop | None:
    return await session.get(Shop, shop_id)


async def get_shop_by_slug(session: AsyncSession, slug: str) -> Shop | None:
    result = await session.execute(select(Shop).where(Shop.slug == slug))
    return result.scalar_one_or_none()


async def list_shops(session: AsyncSession, *, only_active: bool = False) -> list[Shop]:
    stmt = select(Shop).order_by(Shop.title)
    if only_active:
        stmt = stmt.where(Shop.is_active.is_(True))
    return list((await session.execute(stmt)).scalars().all())


async def get_or_create_shop(
    session: AsyncSession,
    *,
    slug: str,
    title: str | None = None,
    base_url: str = "",
) -> Shop:
    shop = await get_shop_by_slug(session, slug)
    if shop is not None:
        return shop
    shop = Shop(slug=slug, title=title or slug, base_url=base_url)
    session.add(shop)
    await session.flush()
    return shop


async def create_shop(
    session: AsyncSession, *, slug: str, title: str, base_url: str = "", is_active: bool = True
) -> Shop:
    shop = Shop(slug=slug, title=title, base_url=base_url, is_active=is_active)
    session.add(shop)
    await session.flush()
    return shop


async def update_shop(session: AsyncSession, shop_id: int, **fields) -> Shop | None:
    shop = await session.get(Shop, shop_id)
    if shop is None:
        return None
    for key, value in fields.items():
        if value is not None and hasattr(shop, key):
            setattr(shop, key, value)
    await session.flush()
    return shop


async def delete_shop(session: AsyncSession, shop_id: int) -> bool:
    shop = await session.get(Shop, shop_id)
    if shop is None:
        return False
    await session.delete(shop)
    return True


# ------------------------------------------------------------------ товари


@dataclass(slots=True)
class ProductFilter:
    search: str | None = None
    shop_id: int | None = None
    only_active: bool = False
    limit: int = 50
    offset: int = 0
    order_by: str = "updated_at"
    desc: bool = True


def _apply_filter(stmt: Select, flt: ProductFilter) -> Select:
    if flt.shop_id:
        stmt = stmt.where(Product.shop_id == flt.shop_id)
    if flt.only_active:
        stmt = stmt.where(Product.is_active.is_(True))
    if flt.search:
        pattern = f"%{flt.search.strip()}%"
        stmt = stmt.where(
            or_(
                Product.name.ilike(pattern),
                Product.external_id.ilike(pattern),
                Product.url.ilike(pattern),
            )
        )
    return stmt


async def list_products(session: AsyncSession, flt: ProductFilter) -> list[Product]:
    column = getattr(Product, flt.order_by, Product.updated_at)
    stmt = (
        _apply_filter(select(Product), flt)
        .options(selectinload(Product.prices))
        .order_by(column.desc() if flt.desc else column.asc())
        .limit(flt.limit)
        .offset(flt.offset)
    )
    return list((await session.execute(stmt)).unique().scalars().all())


async def count_products(session: AsyncSession, flt: ProductFilter) -> int:
    stmt = _apply_filter(select(func.count(Product.id)), flt)
    return int((await session.execute(stmt)).scalar_one())


async def get_product(session: AsyncSession, product_id: int) -> Product | None:
    stmt = (
        select(Product)
        .where(Product.id == product_id)
        .options(selectinload(Product.prices))
    )
    return (await session.execute(stmt)).unique().scalar_one_or_none()


async def get_product_by_url(
    session: AsyncSession, shop_id: int, url: str
) -> Product | None:
    stmt = select(Product).where(Product.shop_id == shop_id, Product.url == url)
    return (await session.execute(stmt)).unique().scalar_one_or_none()


async def create_product(session: AsyncSession, **fields) -> Product:
    product = Product(**fields)
    session.add(product)
    await session.flush()
    return product


async def update_product(session: AsyncSession, product_id: int, **fields) -> Product | None:
    product = await session.get(Product, product_id)
    if product is None:
        return None
    for key, value in fields.items():
        if hasattr(product, key):
            setattr(product, key, value)
    product.updated_at = utcnow()
    await session.flush()
    return product


async def delete_product(session: AsyncSession, product_id: int) -> bool:
    product = await session.get(Product, product_id)
    if product is None:
        return False
    await session.delete(product)
    return True


async def upsert_product(
    session: AsyncSession,
    *,
    shop_id: int,
    url: str,
    name: str,
    external_id: str | None = None,
    image_url: str | None = None,
    category: str | None = None,
) -> Product:
    """Знаходить товар за (магазин, url) або створює новий; оновлює змінені поля."""
    product = await get_product_by_url(session, shop_id, url)
    if product is None:
        return await create_product(
            session,
            shop_id=shop_id,
            url=url,
            name=name,
            external_id=external_id,
            image_url=image_url,
            category=category,
        )

    changed = False
    for key, value in (
        ("name", name),
        ("external_id", external_id),
        ("image_url", image_url),
        ("category", category),
    ):
        if value and getattr(product, key) != value:
            setattr(product, key, value)
            changed = True
    if changed:
        product.updated_at = utcnow()
        await session.flush()
    return product


# -------------------------------------------------------------------- ціни


async def get_last_price(session: AsyncSession, product_id: int) -> Price | None:
    stmt = (
        select(Price)
        .where(Price.product_id == product_id)
        .order_by(Price.parsed_at.desc(), Price.id.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def add_price(
    session: AsyncSession,
    *,
    product_id: int,
    price: Decimal,
    old_price: Decimal | None = None,
    currency: str = "UAH",
    in_stock: bool = True,
    parsed_at: datetime | None = None,
    only_if_changed: bool = False,
) -> Price | None:
    """Додає новий зріз ціни. Повертає None, якщо запис пропущено."""
    if only_if_changed:
        last = await get_last_price(session, product_id)
        if last is not None and last.price == price and last.in_stock == in_stock:
            return None

    row = Price(
        product_id=product_id,
        price=price,
        old_price=old_price,
        currency=currency,
        in_stock=in_stock,
        parsed_at=parsed_at or utcnow(),
    )
    session.add(row)
    await session.flush()
    return row


async def list_prices(
    session: AsyncSession, product_id: int, *, limit: int = 100
) -> list[Price]:
    stmt = (
        select(Price)
        .where(Price.product_id == product_id)
        .order_by(Price.parsed_at.desc(), Price.id.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


async def delete_price(session: AsyncSession, price_id: int) -> bool:
    result = await session.execute(delete(Price).where(Price.id == price_id))
    return bool(result.rowcount)


# ------------------------------------------------------------ сповіщення


async def get_last_notification(
    session: AsyncSession, user_id: int, product_id: int
) -> Notification | None:
    """Останнє, що казали цій людині про цей товар."""
    stmt = (
        select(Notification)
        .where(Notification.user_id == user_id, Notification.product_id == product_id)
        .order_by(Notification.sent_at.desc(), Notification.id.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def add_notification(
    session: AsyncSession,
    *,
    user_id: int,
    product_id: int,
    price: Decimal,
    mode_price: Decimal,
    drop_percent: Decimal,
    sent_at: datetime | None = None,
) -> Notification:
    """Записує факт відправки — саме за цим журналом працює дедуплікація."""
    notification = Notification(
        user_id=user_id,
        product_id=product_id,
        price=price,
        mode_price=mode_price,
        drop_percent=drop_percent,
    )
    if sent_at is not None:
        notification.sent_at = sent_at
    session.add(notification)
    await session.flush()
    return notification


async def list_notifications(
    session: AsyncSession, *, user_id: int | None = None, limit: int = 50
) -> list[Notification]:
    stmt = select(Notification).order_by(Notification.sent_at.desc()).limit(limit)
    if user_id is not None:
        stmt = stmt.where(Notification.user_id == user_id)
    return list((await session.execute(stmt)).scalars().all())


# ------------------------------------------------- користувачі телеграму


async def get_user_by_telegram_id(
    session: AsyncSession, telegram_id: int
) -> TelegramUser | None:
    stmt = select(TelegramUser).where(TelegramUser.telegram_id == telegram_id)
    return (await session.execute(stmt)).scalars().first()


async def get_or_create_user(
    session: AsyncSession,
    *,
    telegram_id: int,
    chat_id: int,
    username: str | None = None,
    first_name: str | None = None,
) -> tuple[TelegramUser, bool]:
    """Знаходить або заводить людину. Повертає (користувач, чи новий).

    Ім'я та username оновлюються щоразу: людина могла їх змінити, а бот має
    звертатись правильно. Повторний `/start` після «стоп» знову вмикає
    сповіщення — це найочевидніший спосіб повернутись.
    """
    user = await get_user_by_telegram_id(session, telegram_id)
    if user is None:
        user = TelegramUser(
            telegram_id=telegram_id,
            chat_id=chat_id,
            username=username,
            first_name=first_name,
        )
        session.add(user)
        await session.flush()
        return user, True

    user.chat_id = chat_id
    user.username = username
    user.first_name = first_name
    user.is_active = True
    await session.flush()
    return user, False


async def deactivate_user(session: AsyncSession, telegram_id: int) -> bool:
    """М'яке вимкнення: підписки лишаються, сповіщення припиняються."""
    user = await get_user_by_telegram_id(session, telegram_id)
    if user is None:
        return False
    user.is_active = False
    await session.flush()
    return True


# ------------------------------------------------------------------ підписки


async def get_subscription(
    session: AsyncSession, user_id: int, product_id: int
) -> Subscription | None:
    stmt = select(Subscription).where(
        Subscription.user_id == user_id, Subscription.product_id == product_id
    )
    return (await session.execute(stmt)).scalars().first()


async def subscribe(
    session: AsyncSession, *, user_id: int, product_id: int
) -> tuple[Subscription, bool]:
    """Підписує людину на товар. Повертає (підписка, чи нова).

    Повторна підписка не помилка: людина могла надіслати те саме посилання
    вдруге, і правильна реакція — сказати, що вже стежимо, а не лаятись.
    """
    existing = await get_subscription(session, user_id, product_id)
    if existing is not None:
        was_muted = existing.is_muted
        existing.is_muted = False
        await session.flush()
        return existing, was_muted

    subscription = Subscription(user_id=user_id, product_id=product_id)
    session.add(subscription)
    await session.flush()
    return subscription, True


async def list_subscriptions(
    session: AsyncSession, user_id: int, *, limit: int = 100
) -> list[Subscription]:
    """Підписки разом із товарами, їхніми цінами й магазином.

    Усе підвантажується одразу: в асинхронній сесії дотягнути зв'язок пізніше
    не можна — SQLAlchemy кине MissingGreenlet замість тихого запиту.
    """
    stmt = (
        select(Subscription)
        .where(Subscription.user_id == user_id)
        .options(
            selectinload(Subscription.product).selectinload(Product.prices),
            selectinload(Subscription.product).joinedload(Product.shop),
        )
        .order_by(Subscription.created_at.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).unique().scalars().all())


async def unsubscribe(session: AsyncSession, *, user_id: int, product_id: int) -> bool:
    result = await session.execute(
        delete(Subscription).where(
            Subscription.user_id == user_id, Subscription.product_id == product_id
        )
    )
    return bool(result.rowcount)


async def subscribers_of(session: AsyncSession, product_id: int) -> list[TelegramUser]:
    """Кому слати про цей товар: активні й не приглушені."""
    stmt = (
        select(TelegramUser)
        .join(Subscription, Subscription.user_id == TelegramUser.id)
        .where(
            Subscription.product_id == product_id,
            Subscription.is_muted.is_(False),
            TelegramUser.is_active.is_(True),
        )
    )
    return list((await session.execute(stmt)).unique().scalars().all())


async def product_urls_for_shop(session: AsyncSession, slug: str) -> list[str]:
    """Адреси активних товарів магазину — те, за чим сьогодні треба стежити.

    Саме цим список для парсера відрізняється від зашитого в код: товари
    приходять від людей через бота, і завтра їх буде більше, ніж учора.
    """
    stmt = (
        select(Product.url)
        .join(Shop, Shop.id == Product.shop_id)
        .where(Shop.slug == slug, Product.is_active.is_(True))
        .order_by(Product.id)
    )
    return list((await session.execute(stmt)).scalars().all())
