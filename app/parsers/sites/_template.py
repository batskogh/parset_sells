"""ШАБЛОН для нового сайту.

Модулі, що починаються з `_`, реєстр ігнорує — це заготовка.
Порядок дій:
  1. скопіювати файл під назвою сайту, напр. `rozetka.py`;
  2. прибрати `_` з імені файлу і заповнити селектори;
  3. запустити `python run_parser.py --site <slug> --dry-run --limit 5`.

Нижче два варіанти: HTML-каталог (типовий) і JSON API (коли сайт віддає дані
готовою відповіддю — це і швидше, і стабільніше за селектори).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal

from bs4 import BeautifulSoup, Tag

from app.parsers.base import BaseParser, HtmlListParser
from app.parsers.models import ParsedProduct
from app.parsers.registry import register
from app.parsers.throttle import DelayPolicy
from app.parsers.utils import absolute_url, clean_text, parse_price


# ─────────────────────────── Варіант 1: HTML-каталог ───────────────────────────
# @register        <- розкоментувати
class ShopHtmlTemplate(HtmlListParser):
    name = "shop_slug"                      # унікальний slug магазину
    title = "Назва магазину"
    base_url = "https://example.com"
    currency = "UAH"

    start_urls = [
        "https://example.com/catalog/category-1/",
        "https://example.com/catalog/category-2/",
    ]

    # селектори картки товару в списку
    item_selector = "div.product-card"
    name_selector = ".product-card__title"
    price_selector = ".product-card__price"
    old_price_selector = ".product-card__price--old"
    link_selector = "a.product-card__link"
    image_selector = "img.product-card__image"
    out_of_stock_selector = ".product-card__status--unavailable"

    next_page_selector = "a.pagination__next"
    max_pages = 20

    # затримки саме для цього сайту
    delay = DelayPolicy(min_delay=1.5, max_delay=4.0)

    # свої заголовки/проксі за потреби
    headers = {"Referer": "https://example.com/"}
    # proxy = "http://user:pass@host:port"

    # заходити на сторінку кожного товару (повільніше, але точніше)
    fetch_details = False

    # --- нижче — приклади точкових перевизначень, видаліть зайве ---

    def extract_external_id(self, card: Tag) -> str | None:
        return card.get("data-product-id")

    def parse_detail(self, item: ParsedProduct, soup: BeautifulSoup) -> ParsedProduct:
        """Спрацьовує лише коли fetch_details = True."""
        sku = soup.select_one(".product__sku")
        if sku:
            item.external_id = clean_text(sku.get_text())
        return item

    def next_page_url(self, soup: BeautifulSoup, page_url: str, page_num: int) -> str | None:
        """Варіант пагінації через ?page=N замість кнопки «далі»."""
        if page_num >= self.max_pages:
            return None
        return f"{self.start_urls[0]}?page={page_num + 1}"


# ──────────────────────────── Варіант 2: JSON API ─────────────────────────────
# @register        <- розкоментувати
class ShopApiTemplate(BaseParser):
    name = "shop_api_slug"
    title = "Магазин з API"
    base_url = "https://example.com"
    delay = DelayPolicy(min_delay=0.5, max_delay=1.5)

    api_url = "https://example.com/api/v1/products"
    page_size = 50
    max_pages = 20

    async def collect(self) -> AsyncIterator[ParsedProduct]:
        for page in range(1, self.max_pages + 1):
            # fetch_json сам витримує паузу перед запитом
            data = await self.fetch_json(
                self.api_url, params={"page": page, "limit": self.page_size}
            )
            items = data.get("items") or []
            if not items:
                break

            for raw in items:
                try:
                    yield ParsedProduct(
                        name=raw["title"],
                        url=absolute_url(self.base_url, raw["url"]),
                        price=Decimal(str(raw["price"])),
                        old_price=parse_price(raw.get("old_price")),
                        currency=raw.get("currency", "UAH"),
                        in_stock=raw.get("in_stock", True),
                        external_id=str(raw.get("id") or "") or None,
                        image_url=raw.get("image"),
                        category=raw.get("category"),
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    self.report_error(f"Некоректний елемент API: {exc}")
