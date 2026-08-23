"""Збереження результатів парсингу в БД."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.config import get_settings
from app.db import repository as repo
from app.db.base import session_scope
from app.parsers.base import BaseParser
from app.parsers.models import ParseResult

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SaveStats:
    shop_slug: str
    products_new: int = 0
    products_updated: int = 0
    prices_added: int = 0
    prices_skipped: int = 0

    def __str__(self) -> str:
        return (
            f"{self.shop_slug}: нових товарів {self.products_new}, "
            f"оновлено {self.products_updated}, цін записано {self.prices_added}"
            f" (пропущено без змін: {self.prices_skipped})"
        )


async def save_result(
    parser: BaseParser,
    result: ParseResult,
    *,
    only_if_changed: bool | None = None,
) -> SaveStats:
    """Записує товари й ціни одного магазину в одній транзакції."""
    settings = get_settings()
    if only_if_changed is None:
        only_if_changed = settings.store_price_only_on_change

    stats = SaveStats(shop_slug=result.shop_slug)

    async with session_scope() as session:
        shop = await repo.get_or_create_shop(
            session,
            slug=parser.name,
            title=parser.title or parser.name,
            base_url=parser.base_url,
        )

        for item in result.items:
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
            if existing is None:
                stats.products_new += 1
            else:
                stats.products_updated += 1

            price = await repo.add_price(
                session,
                product_id=product.id,
                price=item.price,
                old_price=item.old_price,
                currency=item.currency,
                in_stock=item.in_stock,
                parsed_at=item.parsed_at,
                only_if_changed=only_if_changed,
            )
            if price is None:
                stats.prices_skipped += 1
            else:
                stats.prices_added += 1

    logger.info("%s", stats)
    return stats
