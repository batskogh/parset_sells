"""Кому і про що написати.

Модуль свідомо розділений надвоє: спершу **збір** — чиста робота з базою, яка
вирішує, кому що належить сказати, і лише потім **відправка** в Telegram.
Так найважливішу частину можна перевіряти тестами без мережі: помилка тут
означає або мовчання про справжню знижку, або лист не тій людині.

Три речі, які тут сходяться разом:

* **підписки** — сповіщення йде лише тим, хто стежить саме за цим товаром;
* **особистий поріг** — комусь цікаво від 5%, комусь від 30%, тож знижка може
  бути подією для одного підписника й дрібницею для іншого;
* **журнал** — про один епізод знижки кажуть один раз.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.texts import discount_alert
from app.core.discount import DiscountRules, DiscountSignal, price_mode, to_cents
from app.core.discount_scan import MAX_PRODUCTS
from app.core.notify_log import LastNotification, NotifyRules, should_notify
from app.core.timeline import daily_values
from app.db import repository as repo
from app.db.models import Product, TelegramUser

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Outgoing:
    """Одне готове до відправки повідомлення."""

    user: TelegramUser
    product: Product
    signal: DiscountSignal
    text: str

    @property
    def chat_id(self) -> int:
        return self.user.chat_id


def _rules_for(subscription_threshold: Decimal | None, base: DiscountRules) -> DiscountRules:
    """Правила з урахуванням особистого порогу підписки."""
    if subscription_threshold is None:
        return base
    return DiscountRules(
        min_drop_percent=Decimal(str(subscription_threshold)),
        min_samples=base.min_samples,
        bucket_percent=base.bucket_percent,
    )


async def collect(
    session: AsyncSession,
    *,
    rules: DiscountRules | None = None,
    notify_rules: NotifyRules | None = None,
    now: datetime | None = None,
    only_in_stock: bool = True,
) -> list[Outgoing]:
    """Складає список того, що варто надіслати просто зараз.

    Нічого не надсилає й нічого не пише в базу — рішення можна спокійно
    подивитись очима перед першим бойовим прогоном.
    """
    rules = rules or DiscountRules.from_settings()
    notify_rules = notify_rules or NotifyRules.from_settings()
    now = now or datetime.now(timezone.utc)

    products = await repo.list_products(
        session, repo.ProductFilter(only_active=True, limit=MAX_PRODUCTS)
    )

    outgoing: list[Outgoing] = []
    for product in products:
        prices = product.prices                    # найновіші перші
        if not prices:
            continue
        if only_in_stock and not prices[0].in_stock:
            continue

        # по добах, а не по сирих замірах: обхід іде кілька разів на день,
        # і `min_samples` має лишатись кількістю днів спостережень
        history = daily_values((price.parsed_at, price.price) for price in prices)
        if len(history) < rules.min_samples:
            continue

        mode = price_mode(history, bucket_percent=rules.bucket_percent)
        if mode is None or mode.value <= 0:
            continue

        current = prices[0].price          # найсвіжіша ціна, а не денний зріз
        drop = (mode.value - current) / mode.value * Decimal("100")

        for subscription in await repo.subscriptions_for_product(session, product.id):
            user = subscription.user
            personal = _rules_for(subscription.min_drop_percent, rules)
            if drop <= personal.min_drop_percent:
                continue

            last_row = await repo.get_last_notification(session, user.id, product.id)
            last = (
                LastNotification(price=last_row.price, sent_at=last_row.sent_at)
                if last_row is not None
                else None
            )
            # ціна, вище якої знижка вважається закінченою — рахується від
            # особистого порогу, бо для різних людей «закінчилась» настає різно
            recovery = mode.value * (Decimal("100") - personal.min_drop_percent) / Decimal("100")
            since = _prices_after(prices, last_row.sent_at if last_row else None)

            decision = should_notify(
                current=current,
                last=last,
                prices_since_last=since,
                recovery_price=recovery,
                rules=notify_rules,
                now=now,
            )
            if not decision.send:
                logger.debug(
                    "Мовчу про %s для %s: %s", product.id, user.telegram_id, decision.reason
                )
                continue

            signal = DiscountSignal(
                current=to_cents(current),
                mode=to_cents(mode.value),
                drop_percent=to_cents(drop),
                samples=mode.samples,
            )
            outgoing.append(
                Outgoing(
                    user=user,
                    product=product,
                    signal=signal,
                    text=discount_alert(
                        name=product.name,
                        url=product.url,
                        price=f"{signal.current:.2f}",
                        currency=prices[0].currency,
                        usual=f"{signal.mode:.2f}",
                        drop_percent=f"{signal.drop_percent:.2f}",
                        saving=f"{signal.saving:.2f}",
                        samples=signal.samples,
                        in_stock=bool(prices[0].in_stock),
                    ),
                )
            )
            logger.info(
                "Готую сповіщення: товар %s -> %s (%s%%)",
                product.id,
                user.telegram_id,
                signal.drop_percent,
            )

    return outgoing


def _prices_after(prices, moment: datetime | None) -> list[Decimal]:
    """Ціни, зафіксовані після вказаного моменту (найстаріші перші)."""
    if moment is None:
        return []
    if moment.tzinfo is None:                      # зі SQLite час приходить «наївним»
        moment = moment.replace(tzinfo=timezone.utc)

    result = []
    for price in reversed(prices):
        parsed_at = price.parsed_at
        if parsed_at.tzinfo is None:
            parsed_at = parsed_at.replace(tzinfo=timezone.utc)
        if parsed_at > moment:
            result.append(price.price)
    return result


async def mark_sent(session: AsyncSession, item: Outgoing, *, sent_at: datetime | None = None) -> None:
    """Записує факт відправки — саме за цим журналом працює дедуплікація."""
    await repo.add_notification(
        session,
        user_id=item.user.id,
        product_id=item.product.id,
        price=item.signal.current,
        mode_price=item.signal.mode,
        drop_percent=item.signal.drop_percent,
        sent_at=sent_at,
    )
