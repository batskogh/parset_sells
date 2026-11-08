"""Тести розбору картки товару зі збережених сторінок.

Сторінки в `fixtures/` — не вигадана розмітка, а зрізи з живих сайтів
(books.toscrape, citrus, maudau, silpo). Мережі тут немає: тести мають ловити зміну
логіки розбору, а не падати від того, що магазин ліг або підняв ціну.

Коли якийсь із сайтів переробить розмітку, тест не побачить цього — це нормально,
його робота інша: тримати вже досягнуту поведінку. Зміну на сайті ловить
живий прогін.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.parsers.sites.citrus import CitrusParser
from app.parsers.sites.demo_books import DemoBooksParser
from app.parsers.sites.maudau import MaudauParser
from app.parsers.sites.silpo import SilpoParser

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ------------------------------------------------- каталог на CSS-селекторах


@pytest.fixture
def books() -> DemoBooksParser:
    return DemoBooksParser()


def test_знаходить_усі_картки_каталогу(books: DemoBooksParser) -> None:
    soup = books.soup(fixture("books_listing.html"))
    assert len(list(books.find_cards(soup))) == 2


def test_розбирає_картку_каталогу(books: DemoBooksParser) -> None:
    page_url = "https://books.toscrape.com/catalogue/page-1.html"
    soup = books.soup(fixture("books_listing.html"))
    card = next(iter(books.find_cards(soup)))

    item = books.parse_card(card, page_url)

    assert item is not None
    assert item.name == "A Light in the Attic"     # повна назва з title, не обрізана
    assert item.price == Decimal("51.77")
    assert item.currency == "GBP"
    assert item.in_stock is True
    assert item.url.startswith("https://books.toscrape.com/catalogue/")
    assert item.external_id == "a-light-in-the-attic_1000"
    assert item.image_url is not None


def test_бере_повну_назву_а_не_обрізану(books: DemoBooksParser) -> None:
    """У розмітці текст посилання обрізаний трьома крапками, повна назва — в title."""
    soup = books.soup(fixture("books_listing.html"))
    card = next(iter(books.find_cards(soup)))
    assert "..." not in books.extract_name(card)


# ----------------------------------------------------- сторінка товару ld+json


def test_розбирає_сторінку_citrus() -> None:
    parser = CitrusParser()
    url = "https://citrus.ua/videokarti/videokarta-msi-rtx5070-791431.html"

    item = parser.parse_product_page(fixture("citrus_product.html"), url)

    assert item is not None
    assert item.price == Decimal("39199.00")
    assert item.currency == "UAH"
    assert item.external_id == "791431"
    assert "MSI" in item.name
    assert parser.result.errors == []


def test_розпізнає_відсутність_товару() -> None:
    """У citrus ця картка позначена OutOfStock — і через http://, не https://."""
    parser = CitrusParser()
    item = parser.parse_product_page(fixture("citrus_product.html"), "https://citrus.ua/x.html")
    assert item is not None
    assert item.in_stock is False


def test_бере_канонічну_адресу_з_offer() -> None:
    """citrus кладе канонічний URL в Offer.url — саме він має лягти в базу."""
    parser = CitrusParser()
    item = parser.parse_product_page(
        fixture("citrus_product.html"),
        "https://citrus.ua/videokarti/videokarta-msi-rtx5070-791431.html?utm_source=google&gclid=XYZ",
    )
    assert item is not None
    assert "utm_source" not in item.url
    assert "gclid" not in item.url
    assert item.url.endswith(".html")


def test_розбирає_сторінку_maudau() -> None:
    parser = MaudauParser()
    url = "https://maudau.com.ua/product/chipsy-khvyliasti-liuks-zi-smakom-lysychok-125-h-872080"

    item = parser.parse_product_page(fixture("maudau_product.html"), url)

    assert item is not None
    assert item.price == Decimal("74.00")
    assert item.currency == "UAH"
    assert item.in_stock is True
    assert item.external_id == "872080"      # числовий хвіст адреси: sku сайт не віддає
    assert item.image_url and item.image_url.startswith("https://")


def test_чистить_рекламні_мітки_навіть_без_канонічної_адреси() -> None:
    """У maudau канонічного URL у розмітці немає, тож чиститься адреса запиту."""
    parser = MaudauParser()
    item = parser.parse_product_page(
        fixture("maudau_product.html"),
        "https://maudau.com.ua/product/chipsy-872080?utm_source=telegram&gbraid=ABC",
    )
    assert item is not None
    assert item.url == "https://maudau.com.ua/product/chipsy-872080"


def test_розбирає_сторінку_silpo() -> None:
    """Перед Product у Сільпо стоїть ld+json WebPage — брати треба саме Product."""
    parser = SilpoParser()
    url = "https://silpo.ua/product/sukharyky-jokers-pshenychno-zhytni-smak-smetana-ta-zelen-1021809"

    item = parser.parse_product_page(fixture("silpo_product.html"), f"{url}?utm_source=telegram")

    assert item is not None
    assert item.name == "Сухарики Jokers пшенично-житні смак сметана та зелень 125г"
    assert item.price == Decimal("28.99")
    assert item.currency == "UAH"
    assert item.in_stock is True
    assert item.external_id == "1021809"     # sku з розмітки, збігається з хвостом адреси
    assert item.url == url                   # канонічна адреса з розмітки, без міток
    assert item.image_url and item.image_url.startswith("https://images.silpo.ua/")


# ---------------------------------------------------------------- збої розбору


def test_сторінка_без_розмітки_не_валить_прогін() -> None:
    """Магазин без ld+json має дати зрозумілу помилку, а не виняток."""
    parser = CitrusParser()
    item = parser.parse_product_page(fixture("no_markup_product.html"), "https://citrus.ua/x.html")

    assert item is None
    assert len(parser.result.errors) == 1
    assert "ld+json" in parser.result.errors[0]


def test_картка_без_ціни_пропускається(books: DemoBooksParser) -> None:
    """Без ціни товар не має сенсу — картка мовчки пропускається."""
    html = fixture("books_listing.html").replace("price_color", "price_hidden")
    soup = books.soup(html)
    card = next(iter(books.find_cards(soup)))
    assert books.parse_card(card, "https://books.toscrape.com/") is None
