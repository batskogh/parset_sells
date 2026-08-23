"""Робочий приклад парсера на CSS-селекторах.

books.toscrape.com — навчальний сайт, створений спеціально для тренування
парсингу. Використовується як демо: показує повний цикл без ризику
навантажити чужий бойовий сайт.
"""

from __future__ import annotations

from bs4 import Tag

from app.parsers.base import HtmlListParser
from app.parsers.registry import register
from app.parsers.throttle import DelayPolicy
from app.parsers.utils import clean_text


@register
class DemoBooksParser(HtmlListParser):
    name = "demo_books"
    title = "Books to Scrape (демо)"
    base_url = "https://books.toscrape.com"
    currency = "GBP"

    start_urls = ["https://books.toscrape.com/catalogue/page-1.html"]

    item_selector = "article.product_pod"
    name_selector = "h3 a"
    price_selector = "p.price_color"
    link_selector = "h3 a"
    image_selector = "div.image_container img"
    next_page_selector = "li.next a"

    # 1–2 с між запитами; окремо 3 с між сторінками каталогу
    delay = DelayPolicy(min_delay=1.0, max_delay=2.0, page_delay=3.0)
    max_pages = 3          # демо: не тягнемо всі 50 сторінок
    max_items = 60

    def extract_name(self, card: Tag) -> str:
        # у <h3><a> текст обрізаний трьома крапками, повна назва — в title
        node = card.select_one(self.name_selector)
        if node is None:
            return ""
        return clean_text(node.get("title") or node.get_text(" "))

    def extract_in_stock(self, card: Tag) -> bool:
        availability = clean_text(
            card.select_one("p.instock.availability").get_text(" ")
            if card.select_one("p.instock.availability")
            else ""
        )
        return "in stock" in availability.lower()

    def extract_external_id(self, card: Tag) -> str | None:
        # у цього сайту артикулів немає — беремо slug з URL
        node = card.select_one("h3 a")
        href = node.get("href") if node else ""
        return href.rsplit("/", 2)[-2] if href and "/" in href else None
