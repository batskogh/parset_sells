"""Друге демо-джерело: категорія Travel того самого навчального сайту.

Сенс файлу — перевірка фабрики: новий «магазин» додається одним файлом із
селекторами, ядро при цьому не міняється. Від `demo_books` відрізняється
стартовою адресою (категорія замість повного каталогу) і власним slug.
"""

from __future__ import annotations

from bs4 import Tag

from app.parsers.base import HtmlListParser
from app.parsers.registry import register
from app.parsers.throttle import DelayPolicy
from app.parsers.utils import clean_text


@register
class DemoTravelParser(HtmlListParser):
    name = "demo_travel"
    title = "Books to Scrape — Travel (демо)"
    base_url = "https://books.toscrape.com"
    currency = "GBP"

    start_urls = ["https://books.toscrape.com/catalogue/category/books/travel_2/index.html"]

    item_selector = "article.product_pod"
    name_selector = "h3 a"
    price_selector = "p.price_color"
    link_selector = "h3 a"
    image_selector = "div.image_container img"
    next_page_selector = "li.next a"

    delay = DelayPolicy(min_delay=1.0, max_delay=2.0, page_delay=3.0)
    max_pages = 2          # у категорії Travel всього 11 книжок
    max_items = 20

    def extract_name(self, card: Tag) -> str:
        # текст у <h3><a> обрізаний трьома крапками, повна назва — в title
        node = card.select_one(self.name_selector)
        if node is None:
            return ""
        return clean_text(node.get("title") or node.get_text(" "))

    def extract_in_stock(self, card: Tag) -> bool:
        node = card.select_one("p.instock.availability")
        return "in stock" in clean_text(node.get_text(" ") if node else "").lower()

    def extract_category(self, card: Tag, page_url: str) -> str | None:
        return "Travel"
