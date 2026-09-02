"""Робота бота без Telegram.

Хендлер має бути тонким: узяв дані з апдейта — віддав сюди. Тоді поведінку
бота можна перевіряти звичайними тестами з базою, не піднімаючи ані мережі,
ані самого Telegram, і не вигадуючи підробки для типів aiogram.
"""

from __future__ import annotations

import logging

from dataclasses import dataclass
from decimal import Decimal
from html import escape

from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts
from app.bot.texts import welcome
from app.core.discount import DiscountRules, price_mode
from app.parsers import resolve
from app.parsers.models import ParseResult
from app.parsers.throttle import DelayPolicy
from app.db import repository as repo

logger = logging.getLogger(__name__)


async def greet(
    session: AsyncSession,
    *,
    telegram_id: int,
    chat_id: int,
    username: str | None = None,
    first_name: str | None = None,
) -> str:
    """Реєструє людину (або вітає знайому) і повертає текст відповіді."""
    user, created = await repo.get_or_create_user(
        session,
        telegram_id=telegram_id,
        chat_id=chat_id,
        username=username,
        first_name=first_name,
    )
    logger.info(
        "%s користувач %s (%s)",
        "Новий" if created else "Повернувся",
        telegram_id,
        username or "без імені",
    )
    return welcome(user.first_name, returning=not created)


async def stop_notifications(session: AsyncSession, *, telegram_id: int) -> bool:
    """Вимикає сповіщення, не чіпаючи список товарів."""
    stopped = await repo.deactivate_user(session, telegram_id)
    if stopped:
        logger.info("Користувач %s вимкнув сповіщення", telegram_id)
    return stopped


@dataclass(frozen=True, slots=True)
class AddResult:
    """Наслідок спроби додати товар за посиланням."""

    ok: bool
    text: str
    product_id: int | None = None


async def add_product_by_url(
    session: AsyncSession,
    *,
    telegram_id: int,
    chat_id: int,
    message_text: str | None,
) -> AddResult:
    """Розбирає повідомлення з посиланням і бере товар під нагляд.

    Порядок навмисно такий: спершу дешеві перевірки (чи є посилання, чи
    знайомий магазин), і лише потім похід у мережу. Людина отримує зрозумілу
    відмову за мить, а не після таймауту.
    """
    url = resolve.extract_url(message_text)
    if url is None:
        return AddResult(False, texts.not_a_link())

    parser_class = resolve.find_parser_for_url(url)
    if parser_class is None:
        # магазин може бути відомий, але свідомо вимкнений — це інша розмова
        disabled = resolve.find_parser_for_url(url, only_enabled=False)
        if disabled is not None:
            return AddResult(False, texts.shop_blocked(disabled.title or disabled.name))
        return AddResult(False, texts.unknown_shop(resolve.known_shops()))

    if not resolve.supports_single_product(parser_class):
        return AddResult(False, texts.catalog_only_shop(parser_class.title or parser_class.name))

    result = await _fetch_single(parser_class, url)
    if not result.items:
        return AddResult(False, texts.cannot_read_page(result.errors))

    item = result.items[0]
    user, _ = await repo.get_or_create_user(session, telegram_id=telegram_id, chat_id=chat_id)
    shop = await repo.get_or_create_shop(
        session,
        slug=parser_class.name,
        title=parser_class.title or parser_class.name,
        base_url=parser_class.base_url,
    )

    existing = await repo.get_product_by_url(session, shop.id, item.url)
    product = await repo.upsert_product(
        session,
        shop_id=shop.id,
        url=item.url,
        name=item.name,
        external_id=item.external_id,
        image_url=item.image_url,
        category=item.category,
    )
    await repo.add_price(
        session,
        product_id=product.id,
        price=item.price,
        old_price=item.old_price,
        currency=item.currency,
        in_stock=item.in_stock,
    )
    _, is_new = await repo.subscribe(session, user_id=user.id, product_id=product.id)

    name = escape(item.name)
    price = f"{item.price:.2f}"
    if existing is not None and not is_new:
        return AddResult(True, texts.already_watching(name, price, item.currency), product.id)

    logger.info("Користувач %s додав товар %s (%s)", telegram_id, product.id, item.url)
    return AddResult(
        True,
        texts.product_added(name, price, item.currency, in_stock=item.in_stock),
        product.id,
    )


async def _fetch_single(parser_class, url: str) -> ParseResult:
    """Один похід на сторінку товару без запису в базу."""
    parser = parser_class(product_urls=[url], delay=DelayPolicy(min_delay=0, max_delay=0))
    async with parser:
        return await parser.run()


@dataclass(frozen=True, slots=True)
class WatchedItem:
    """Рядок списку «за чим я стежу»."""

    product_id: int
    name: str
    url: str
    shop: str
    price: Decimal | None
    currency: str
    in_stock: bool
    usual_price: Decimal | None      # звична ціна, коли замірів уже досить
    samples: int


async def list_watched(session: AsyncSession, *, telegram_id: int) -> list[WatchedItem]:
    """Товари, за якими стежить ця людина.

    Звичну ціну показуємо лише тоді, коли замірів достатньо: на трьох днях
    вона була б вигадкою, а людина сприйняла б її як обіцянку.
    """
    user = await repo.get_user_by_telegram_id(session, telegram_id)
    if user is None:
        return []

    rules = DiscountRules.from_settings()
    items: list[WatchedItem] = []
    for subscription in await repo.list_subscriptions(session, user.id):
        product = subscription.product
        prices = product.prices                      # найновіша перша
        last = prices[0] if prices else None
        history = [price.price for price in reversed(prices)]
        mode = price_mode(history, bucket_percent=rules.bucket_percent) if history else None

        items.append(
            WatchedItem(
                product_id=product.id,
                name=product.name,
                url=product.url,
                shop=product.shop.title if product.shop else "",
                price=last.price if last else None,
                currency=last.currency if last else "UAH",
                in_stock=bool(last.in_stock) if last else True,
                usual_price=mode.value if mode and mode.samples >= rules.min_samples else None,
                samples=len(history),
            )
        )
    return items


async def remove_watched(
    session: AsyncSession, *, telegram_id: int, product_id: int
) -> str | None:
    """Прибирає товар зі списку людини. Повертає назву або None.

    Товар із бази не видаляється: за ним можуть стежити інші, а історія цін
    спільна й цінна сама собою.
    """
    user = await repo.get_user_by_telegram_id(session, telegram_id)
    if user is None:
        return None

    subscription = await repo.get_subscription(session, user.id, product_id)
    if subscription is None:
        return None

    name = subscription.product.name if subscription.product else ""
    await repo.unsubscribe(session, user_id=user.id, product_id=product_id)
    logger.info("Користувач %s прибрав товар %s зі списку", telegram_id, product_id)
    return name
