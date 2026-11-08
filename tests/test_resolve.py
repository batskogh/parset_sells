"""Тести розпізнавання магазину за посиланням.

Найдорожча помилка тут — сплутати чужий домен зі своїм: тоді бот спробує
читати сторінку не тим парсером і видасть незрозумілу відмову замість
чесного «не знаю такого магазину».
"""

from __future__ import annotations

import pytest

from app.parsers.resolve import (
    extract_url,
    find_parser_for_url,
    host_of,
    known_shops,
    supports_single_product,
)


@pytest.mark.parametrize(
    ("текст", "очікуємо"),
    [
        ("https://citrus.ua/p/1.html", "https://citrus.ua/p/1.html"),
        ("глянь оце https://citrus.ua/p/1.html, норм?", "https://citrus.ua/p/1.html"),
        ("http://maudau.com.ua/x", "http://maudau.com.ua/x"),
        ("(https://citrus.ua/p/2.html)", "https://citrus.ua/p/2.html"),
        ("просто текст", None),
        ("", None),
        (None, None),
    ],
)
def test_витягує_посилання_з_повідомлення(текст, очікуємо) -> None:
    """Люди рідко надсилають голий URL — поруч буває підпис чи дужки."""
    assert extract_url(текст) == очікуємо


@pytest.mark.parametrize(
    ("url", "хост"),
    [
        ("https://www.staff-clothes.com/p/1", "staff-clothes.com"),
        ("https://CITRUS.ua/P/1", "citrus.ua"),
        ("https://hard.rozetka.com.ua/ua/x/", "hard.rozetka.com.ua"),
        ("https://maudau.com.ua:443/x", "maudau.com.ua"),
        ("", ""),
    ],
)
def test_нормалізує_хост(url, хост) -> None:
    assert host_of(url) == хост


def test_знаходить_магазин_за_доменом() -> None:
    parser = find_parser_for_url("https://maudau.com.ua/product/chipsy-872080")
    assert parser is not None and parser.name == "maudau"


def test_субдомен_належить_тому_самому_сайту() -> None:
    """Якби Rozetka була підключена, hard.rozetka.com.ua мав би до неї потрапити."""
    from app.parsers.resolve import _same_site

    assert _same_site("hard.rozetka.com.ua", "rozetka.com.ua") is True
    assert _same_site("rozetka.com.ua", "rozetka.com.ua") is True


def test_схожий_домен_не_вважається_своїм() -> None:
    """Головна пастка: `endswith` сплутав би notmaudau.com.ua з maudau.com.ua."""
    from app.parsers.resolve import _same_site

    assert _same_site("notmaudau.com.ua", "maudau.com.ua") is False
    assert find_parser_for_url("https://notmaudau.com.ua/product/x") is None


def test_невідомий_магазин() -> None:
    assert find_parser_for_url("https://hard.rozetka.com.ua/ua/x/p1/") is None


def test_вимкнений_магазин_знаходиться_лише_явно() -> None:
    """staff вимкнений через Cloudflare — але бот має відрізняти його від чужого."""
    assert find_parser_for_url("https://www.staff-clothes.com/product/9en0116/") is None
    parser = find_parser_for_url(
        "https://www.staff-clothes.com/product/9en0116/", only_enabled=False
    )
    assert parser is not None and parser.name == "staff_clothes"


def test_сільпо_відомий_але_вимкнений() -> None:
    """Сільпо за Cloudflare: посилання не має ставати заявкою на невідомий магазин."""
    url = "https://silpo.ua/product/sukharyky-jokers-pshenychno-zhytni-smak-smetana-ta-zelen-1021809"
    assert find_parser_for_url(url) is None
    parser = find_parser_for_url(url, only_enabled=False)
    assert parser is not None and parser.name == "silpo"


def test_каталожний_парсер_не_приймає_окремий_товар() -> None:
    from app.parsers.sites.citrus import CitrusParser
    from app.parsers.sites.demo_books import DemoBooksParser

    assert supports_single_product(CitrusParser) is True
    assert supports_single_product(DemoBooksParser) is False


def test_перелік_магазинів_лише_придатні() -> None:
    """У списку для людини мають бути ті, з яких справді можна додати товар."""
    назви = {назва for назва, _ in known_shops()}
    assert "MAUDAU" in назви
    assert "Цитрус" in назви
    assert not any("Scrape" in назва for назва in назви)   # каталожний
    assert "Staff" not in назви                            # вимкнений
