"""Пошук знижок у вже зібраній історії.

Розрахунок знижки живе в `discount.py` і нічого не знає про базу — сюди
винесено все, що з базою працює: витягти історію кожного товару й прикласти
до неї правила. Так детектор лишається чистою функцією, яку легко перевіряти
на вигаданих рядах, а цей модуль відповідає лише за «звідки взяти числа».
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.discount import DiscountRules, DiscountSignal, find_discount
from app.db import repository as repo
from app.db.models import Product

logger = logging.getLogger(__name__)

# стеля на кількість товарів в одному скані: `ProductFilter` вимагає числа,
# а «всі» для локальної бази — це саме такий порядок
MAX_PRODUCTS = 10_000


@dataclass(frozen=True, slots=True)
class ProductDiscount:
    """Знижка, прив'язана до товару."""

    product: Product
    signal: DiscountSignal

    def render(self) -> str:
        return (
            f"{self.product.name[:60]} — {self.signal.current} замість "
            f"звичних {self.signal.mode} (−{self.signal.drop_percent}%, "
            f"{self.signal.samples} замірів)"
        )


async def scan_products(
    session: AsyncSession,
    *,
    rules: DiscountRules | None = None,
    shop_id: int | None = None,
    only_in_stock: bool = True,
) -> list[ProductDiscount]:
    """Перевіряє кожен активний товар і повертає знайдені знижки.

    `only_in_stock` відсіює товари, яких зараз немає: сповіщати про знижку на
    те, чого не купити, — марно турбувати людину. Саме так поводиться,
    наприклад, відеокарта в citrus: ціна є, товару немає.
    """
    rules = rules or DiscountRules.from_settings()
    products = await repo.list_products(
        session, repo.ProductFilter(shop_id=shop_id, only_active=True, limit=MAX_PRODUCTS)
    )

    found: list[ProductDiscount] = []
    for product in products:
        # ціни вже підвантажені разом із товаром, найновіша перша
        prices = product.prices
        if not prices:
            continue
        if only_in_stock and not prices[0].in_stock:
            logger.debug("Пропущено (немає в наявності): %s", product.name[:50])
            continue

        # у зв'язку вони впорядковані від найновішої, а детектор чекає хронологію
        history = [price.price for price in reversed(prices)]
        signal = find_discount(history, rules=rules)
        if signal is not None:
            found.append(ProductDiscount(product=product, signal=signal))

    found.sort(key=lambda item: item.signal.drop_percent, reverse=True)
    return found
