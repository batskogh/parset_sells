"""MAUDAU — парсер сторінок товарів.

Чому не `HtmlListParser`: сайт — SPA на Nuxt, розмітка каталогу збирається
скриптом і класи в ній згенеровані (міняються з кожним білдом). Натомість на
кожній сторінці товару лежить готовий `application/ld+json` типу `Product` із
ціною, валютою і наявністю — це стабільний контракт, а не верстка.

Тому парсер працює не по каталогу, а по списку конкретних сторінок товарів:
саме так і задумано в проєкті — користувач надсилає боту посилання на товар.

Список адрес береться (у порядку спадання пріоритету):
  1. з аргументу `product_urls` конструктора;
  2. з `start_urls` класу — типово для ручного прогону.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from decimal import Decimal
from typing import Any, ClassVar

from bs4 import BeautifulSoup

from app.parsers.base import BaseParser
from app.parsers.models import ParsedProduct
from app.parsers.registry import register
from app.parsers.throttle import DelayPolicy
from app.parsers.utils import clean_text, parse_price


@register
class MaudauParser(BaseParser):
    name = "maudau"
    title = "MAUDAU"
    base_url = "https://maudau.com.ua"
    currency = "UAH"

    # сторінки товарів для ручного прогону; бот пізніше передаватиме свій список
    start_urls: ClassVar[Sequence[str]] = (
        "https://maudau.com.ua/product/chipsy-khvyliasti-liuks-zi-smakom-lysychok-125-h-872080",
    )

    # сторінка товару важка (~2 МБ), тож ходимо неквапливо
    delay = DelayPolicy(min_delay=2.0, max_delay=4.0)
    headers = {
        "Accept-Language": "uk-UA,uk;q=0.9",
        "Referer": "https://maudau.com.ua/",
    }

    def __init__(self, *, product_urls: Sequence[str] | None = None, **kwargs):
        super().__init__(**kwargs)
        self.product_urls = tuple(product_urls or type(self).start_urls)
        if not self.product_urls:
            raise ValueError(f"{self.name}: не задано жодної сторінки товару")

    # ------------------------------------------------------------- JSON-LD

    @staticmethod
    def iter_ld_json(soup: BeautifulSoup) -> list[dict[str, Any]]:
        """Усі об'єкти з `<script type="application/ld+json">` сторінки."""
        found: list[dict[str, Any]] = []
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(tag.string or "")
            except (json.JSONDecodeError, TypeError):
                continue
            found.extend(d for d in (data if isinstance(data, list) else [data]) if isinstance(d, dict))
        return found

    def find_product_ld(self, soup: BeautifulSoup) -> dict[str, Any] | None:
        for node in self.iter_ld_json(soup):
            if node.get("@type") == "Product":
                return node
        return None

    @staticmethod
    def _first_offer(product_ld: dict[str, Any]) -> dict[str, Any]:
        offers = product_ld.get("offers") or {}
        if isinstance(offers, list):
            return next((o for o in offers if isinstance(o, dict)), {})
        return offers if isinstance(offers, dict) else {}

    # ---------------------------------------------------------- витягування

    def extract_price(self, offer: dict[str, Any]) -> Decimal | None:
        price = offer.get("price")
        if price is None:
            spec = offer.get("priceSpecification") or {}
            price = spec.get("price") if isinstance(spec, dict) else None
        return parse_price(price)

    def extract_in_stock(self, offer: dict[str, Any]) -> bool:
        # schema.org: InStock / LimitedAvailability / PreOrder / OutOfStock / SoldOut
        availability = str(offer.get("availability") or "").rsplit("/", 1)[-1].lower()
        if not availability:
            return True
        return availability not in {"outofstock", "soldout", "discontinued"}

    def extract_external_id(self, product_ld: dict[str, Any], url: str) -> str | None:
        """Артикул: sku з розмітки, інакше — числовий хвіст адреси товару."""
        sku = clean_text(str(product_ld.get("sku") or ""))
        if sku:
            return sku
        tail = url.rstrip("/").rsplit("-", 1)[-1]
        return tail if tail.isdigit() else None

    def parse_product_page(self, html: str, url: str) -> ParsedProduct | None:
        soup = self.soup(html)
        product_ld = self.find_product_ld(soup)
        if product_ld is None:
            self.report_error(f"Немає розмітки Product у ld+json: {url}")
            return None

        offer = self._first_offer(product_ld)
        price = self.extract_price(offer)
        if price is None:
            self.report_error(f"Не знайдено ціну: {url}")
            return None

        name = clean_text(str(product_ld.get("name") or ""))
        if not name:
            self.report_error(f"Не знайдено назву: {url}")
            return None

        image = product_ld.get("image")
        if isinstance(image, list):
            image = image[0] if image else None

        return ParsedProduct(
            name=name,
            url=url,
            price=price,
            currency=str(offer.get("priceCurrency") or self.currency),
            in_stock=self.extract_in_stock(offer),
            external_id=self.extract_external_id(product_ld, url),
            image_url=str(image) if image else None,
        )

    # ----------------------------------------------------------------- обхід

    async def collect(self) -> AsyncIterator[ParsedProduct]:
        for url in self.product_urls:
            try:
                html = await self.fetch(url)
            except Exception as exc:  # noqa: BLE001 — один недоступний товар не валить решту
                self.report_error(f"Сторінка {url}: {exc}")
                continue

            item = self.parse_product_page(html, url)
            if item is not None:
                yield item
