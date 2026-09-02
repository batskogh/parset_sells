"""Пошук парсера за посиланням на товар.

Коли людина кидає боту посилання, треба зрозуміти, чий це магазин. Єдине
джерело правди тут — реєстр парсерів: у кожного є `base_url`, і саме з нього
будується відповідність, а не з окремого списку доменів. Інакше додавання
магазину знову вимагало б правки в двох місцях.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from app.parsers.base import BaseParser
from app.parsers.registry import all_parsers

# посилання всередині звичайного повідомлення, разом із хвостом параметрів
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def extract_url(text: str | None) -> str | None:
    """Перше посилання з тексту повідомлення.

    Людина рідко надсилає голий URL: поруч буває підпис, емодзі або цитата.
    """
    if not text:
        return None
    match = URL_RE.search(text)
    return match.group(0).rstrip(").,;") if match else None


def host_of(url: str | None) -> str:
    """Хост посилання без `www.` і в нижньому регістрі."""
    if not url:
        return ""
    host = urlsplit(url.strip()).netloc.lower()
    host = host.split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _same_site(host: str, base_host: str) -> bool:
    """Чи належить хост тому самому сайту.

    Порівнюємо по сегментах, а не через `endswith`: інакше `notmaudau.com.ua`
    вважався б частиною `maudau.com.ua`.
    """
    return bool(host) and (host == base_host or host.endswith(f".{base_host}"))


def find_parser_for_url(url: str, *, only_enabled: bool = True) -> type[BaseParser] | None:
    """Парсер, якому належить це посилання, або None."""
    host = host_of(url)
    if not host:
        return None
    for parser in all_parsers(only_enabled=only_enabled):
        if _same_site(host, host_of(parser.base_url)):
            return parser
    return None


def supports_single_product(parser: type[BaseParser]) -> bool:
    """Чи вміє парсер знімати ціну з окремої сторінки товару.

    Каталожні парсери (demo_books) ходять по списках і не приймають адресу
    конкретного товару — з них додати товар посиланням не можна.
    """
    from app.parsers.base import LdJsonProductParser

    return issubclass(parser, LdJsonProductParser)


def known_shops(*, only_enabled: bool = True) -> list[tuple[str, str]]:
    """Магазини, з яких можна додати товар: (назва, адреса)."""
    return [
        (parser.title or parser.name, parser.base_url)
        for parser in all_parsers(only_enabled=only_enabled)
        if supports_single_product(parser)
    ]
