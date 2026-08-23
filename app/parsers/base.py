"""Базові класи парсерів.

Ієрархія:

    BaseParser        — контракт + життєвий цикл (сесія, затримки, лог, помилки)
      HtmlListParser  — готова реалізація для «каталог + пагінація» на CSS-селекторах
        <ВашСайт>     — у 90% випадків достатньо оголосити селектори

Правила гри:
  * усередині одного парсера запити йдуть **послідовно** (await у циклі);
  * паузи між запитами задає DelayPolicy;
  * паралелізм між сайтами організовує ParserRunner.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterable, Sequence
from decimal import Decimal
from typing import ClassVar

from bs4 import BeautifulSoup, Tag

from app.config import get_settings
from app.parsers.http import DEFAULT_HEADERS, HttpClient, HttpConfig
from app.parsers.models import ParsedProduct, ParseResult
from app.parsers.throttle import DelayPolicy
from app.parsers.utils import absolute_url, clean_text, detect_currency, parse_price


class BaseParser(ABC):
    """Абстрактний парсер одного магазину."""

    # --- обов'язкові атрибути підкласу ---
    name: ClassVar[str] = ""          # slug магазину, унікальний (напр. "rozetka")
    title: ClassVar[str] = ""         # людська назва магазину
    base_url: ClassVar[str] = ""      # https://example.com

    # --- налаштування за замовчуванням (перевизначаються в підкласі) ---
    currency: ClassVar[str] = "UAH"
    delay: ClassVar[DelayPolicy] = DelayPolicy()
    headers: ClassVar[dict[str, str]] = {}
    proxy: ClassVar[str | None] = None
    http2: ClassVar[bool] = False
    verify_ssl: ClassVar[bool] = True
    max_items: ClassVar[int | None] = None   # обмеження для тестових прогонів
    enabled: ClassVar[bool] = True

    def __init__(self, *, delay: DelayPolicy | None = None, max_items: int | None = None):
        if not self.name:
            raise ValueError(f"{type(self).__name__}: не задано атрибут `name`")
        self.delay = delay or type(self).delay
        self.max_items = max_items if max_items is not None else type(self).max_items
        self.logger = logging.getLogger(f"parser.{self.name}")
        self.result = ParseResult(shop_slug=self.name)
        self._http: HttpClient | None = None

    # ------------------------------------------------------------ життєвий цикл

    def build_http_config(self) -> HttpConfig:
        settings = get_settings()
        return HttpConfig(
            timeout=settings.http_timeout,
            retries=settings.http_retries,
            retry_backoff=settings.http_retry_backoff,
            headers={**DEFAULT_HEADERS, **self.headers},
            proxy=self.proxy,
            verify_ssl=self.verify_ssl,
            http2=self.http2,
        )

    async def __aenter__(self) -> "BaseParser":
        self._http = await HttpClient(self.build_http_config()).__aenter__()
        await self.on_start()
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.on_finish()
        if self._http is not None:
            await self._http.__aexit__(*exc_info)
            self._http = None

    async def on_start(self) -> None:
        """Хук: авторизація, отримання токенів, прогрів cookies."""

    async def on_finish(self) -> None:
        """Хук: прибирання після роботи."""

    @property
    def http(self) -> HttpClient:
        if self._http is None:
            raise RuntimeError(f"{self.name}: парсер треба запускати через `async with`")
        return self._http

    # ------------------------------------------------------------------ запити

    async def fetch(self, url: str, *, delay: bool = True, **kwargs) -> str:
        """Послідовний GET із паузою перед запитом."""
        if delay and self.result.pages_visited > 0:
            await self.delay.wait()
        self.logger.debug("GET %s", url)
        html = await self.http.get_text(url, **kwargs)
        self.result.pages_visited += 1
        return html

    async def fetch_json(self, url: str, *, delay: bool = True, **kwargs):
        if delay and self.result.pages_visited > 0:
            await self.delay.wait()
        self.logger.debug("GET(json) %s", url)
        data = await self.http.get_json(url, **kwargs)
        self.result.pages_visited += 1
        return data

    @staticmethod
    def soup(html: str) -> BeautifulSoup:
        return BeautifulSoup(html, "lxml")

    def report_error(self, message: str) -> None:
        self.logger.warning(message)
        self.result.errors.append(message)

    # ------------------------------------------------------- контракт підкласу

    @abstractmethod
    def collect(self) -> AsyncIterator[ParsedProduct]:
        """Асинхронний генератор товарів. Реалізується в підкласі.

        Приклад::

            async def collect(self):
                for url in self.start_urls:
                    html = await self.fetch(url)
                    for card in self.soup(html).select(".product"):
                        yield ParsedProduct(...)
        """
        raise NotImplementedError

    # -------------------------------------------------------------- запуск

    async def run(self) -> ParseResult:
        """Повний прогін парсера. Помилки не «вбивають» інші сайти."""
        started = time.perf_counter()
        self.logger.info("Старт парсингу %s", self.title or self.name)
        try:
            async for item in self.collect():
                self.result.items.append(item)
                if self.max_items and len(self.result.items) >= self.max_items:
                    self.logger.info("Досягнуто ліміт max_items=%s", self.max_items)
                    break
        except Exception as exc:  # noqa: BLE001 — ізолюємо збій одного сайту
            self.report_error(f"{type(exc).__name__}: {exc}")
            self.logger.exception("Парсер %s впав", self.name)

        self.result.duration = time.perf_counter() - started
        self.logger.info(
            "Готово %s: товарів=%s, сторінок=%s, помилок=%s, час=%.1fс",
            self.name,
            len(self.result.items),
            self.result.pages_visited,
            len(self.result.errors),
            self.result.duration,
        )
        return self.result


class HtmlListParser(BaseParser):
    """Парсер каталогу на CSS-селекторах.

    Щоб додати новий сайт, зазвичай достатньо оголосити селектори:

        class MyShop(HtmlListParser):
            name = "myshop"
            title = "My Shop"
            base_url = "https://myshop.ua"
            start_urls = ["https://myshop.ua/catalog/"]
            item_selector = ".product-card"
            name_selector = ".product-card__title"
            price_selector = ".product-card__price"
            link_selector = "a.product-card__link"
            next_page_selector = "a.pagination__next"

    Нестандартні місця точково перевизначаються методами
    `extract_*` / `parse_card` / `parse_detail`.
    """

    start_urls: ClassVar[Sequence[str]] = ()

    # селектори картки товару в списку
    item_selector: ClassVar[str] = ""
    name_selector: ClassVar[str] = ""
    price_selector: ClassVar[str] = ""
    old_price_selector: ClassVar[str] = ""
    link_selector: ClassVar[str] = ""
    image_selector: ClassVar[str] = ""
    external_id_selector: ClassVar[str] = ""
    out_of_stock_selector: ClassVar[str] = ""

    # атрибути, з яких брати значення (None -> текст вузла)
    price_attr: ClassVar[str | None] = None
    external_id_attr: ClassVar[str | None] = None

    # пагінація
    next_page_selector: ClassVar[str] = ""
    max_pages: ClassVar[int] = 20

    # чи заходити на сторінку кожного товару (повільніше, але точніше)
    fetch_details: ClassVar[bool] = False

    def __init__(self, *, start_urls: Sequence[str] | None = None, **kwargs):
        super().__init__(**kwargs)
        self.start_urls = tuple(start_urls or type(self).start_urls)
        if not self.start_urls:
            raise ValueError(f"{self.name}: не задано `start_urls`")
        if not self.item_selector:
            raise ValueError(f"{self.name}: не задано `item_selector`")

    # ------------------------------------------------------------- витягування

    @staticmethod
    def _node(root: Tag, selector: str) -> Tag | None:
        return root.select_one(selector) if selector else None

    def _text(self, root: Tag, selector: str, attr: str | None = None) -> str:
        node = self._node(root, selector)
        if node is None:
            return ""
        if attr:
            return clean_text(node.get(attr, ""))
        return clean_text(node.get_text(" "))

    def extract_name(self, card: Tag) -> str:
        return self._text(card, self.name_selector) or clean_text(card.get("title", ""))

    def extract_url(self, card: Tag, page_url: str) -> str:
        node = self._node(card, self.link_selector) or (
            card if card.name == "a" else card.select_one("a[href]")
        )
        href = node.get("href") if isinstance(node, Tag) else None
        return absolute_url(page_url or self.base_url, href)

    def extract_price(self, card: Tag) -> Decimal | None:
        return parse_price(self._text(card, self.price_selector, self.price_attr))

    def extract_old_price(self, card: Tag) -> Decimal | None:
        if not self.old_price_selector:
            return None
        return parse_price(self._text(card, self.old_price_selector))

    def extract_currency(self, card: Tag) -> str:
        raw = self._text(card, self.price_selector)
        return detect_currency(raw, self.currency)

    def extract_image(self, card: Tag) -> str | None:
        node = self._node(card, self.image_selector) or card.select_one("img")
        if node is None:
            return None
        src = node.get("src") or node.get("data-src") or node.get("data-original")
        return absolute_url(self.base_url, src) or None

    def extract_external_id(self, card: Tag) -> str | None:
        if not self.external_id_selector:
            return None
        return self._text(card, self.external_id_selector, self.external_id_attr) or None

    def extract_in_stock(self, card: Tag) -> bool:
        if not self.out_of_stock_selector:
            return True
        return self._node(card, self.out_of_stock_selector) is None

    def extract_category(self, card: Tag, page_url: str) -> str | None:
        return None

    # ------------------------------------------------------------ збірка товару

    def parse_card(self, card: Tag, page_url: str) -> ParsedProduct | None:
        """Перетворює картку товару на ParsedProduct. Повертає None — пропустити."""
        price = self.extract_price(card)
        if price is None:
            return None
        name = self.extract_name(card)
        url = self.extract_url(card, page_url)
        if not name or not url:
            return None
        return ParsedProduct(
            name=name,
            url=url,
            price=price,
            old_price=self.extract_old_price(card),
            currency=self.extract_currency(card),
            in_stock=self.extract_in_stock(card),
            external_id=self.extract_external_id(card),
            image_url=self.extract_image(card),
            category=self.extract_category(card, page_url),
        )

    def parse_detail(self, item: ParsedProduct, soup: BeautifulSoup) -> ParsedProduct:
        """Доповнення товару даними з його власної сторінки (якщо fetch_details)."""
        return item

    def find_cards(self, soup: BeautifulSoup) -> Iterable[Tag]:
        return soup.select(self.item_selector)

    def next_page_url(self, soup: BeautifulSoup, page_url: str, page_num: int) -> str | None:
        """Наступна сторінка каталогу. Перевизначте для схеми `?page=N`."""
        if not self.next_page_selector:
            return None
        node = soup.select_one(self.next_page_selector)
        if node is None:
            return None
        href = node.get("href")
        url = absolute_url(page_url, href)
        return url if url and url != page_url else None

    # ----------------------------------------------------------------- обхід

    async def collect(self) -> AsyncIterator[ParsedProduct]:
        seen: set[str] = set()

        for start_url in self.start_urls:
            page_url: str | None = start_url
            page_num = 1

            while page_url and page_num <= self.max_pages:
                try:
                    html = await self.fetch(page_url)
                except Exception as exc:  # noqa: BLE001
                    self.report_error(f"Сторінка {page_url}: {exc}")
                    break

                soup = self.soup(html)
                cards = list(self.find_cards(soup))
                if not cards:
                    self.logger.info("Порожня сторінка: %s", page_url)

                for card in cards:
                    try:
                        item = self.parse_card(card, page_url)
                    except Exception as exc:  # noqa: BLE001 — одна погана картка не валить прогін
                        self.report_error(f"Картка на {page_url}: {exc}")
                        continue
                    if item is None or item.url in seen:
                        continue
                    seen.add(item.url)

                    if self.fetch_details:
                        try:
                            detail_html = await self.fetch(item.url)
                            item = self.parse_detail(item, self.soup(detail_html))
                        except Exception as exc:  # noqa: BLE001
                            self.report_error(f"Деталі {item.url}: {exc}")

                    yield item

                next_url = self.next_page_url(soup, page_url, page_num)
                if not next_url:
                    break
                # звичайну паузу вже робить fetch(); тут — лише додаткова
                # пауза між сторінками, якщо її задано явно
                if self.delay.page_delay is not None:
                    await self.delay.wait_page()
                page_url = next_url
                page_num += 1
