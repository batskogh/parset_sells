"""Допоміжні функції парсингу: ціна, текст, посилання."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

_PRICE_CLEAN_RE = re.compile(r"[^\d,.\-]")
_SPACES_RE = re.compile(r"\s+")

CURRENCY_SIGNS = {
    "₴": "UAH",
    "грн": "UAH",
    "uah": "UAH",
    "$": "USD",
    "usd": "USD",
    "€": "EUR",
    "eur": "EUR",
    "zł": "PLN",
    "pln": "PLN",
    "£": "GBP",
    "gbp": "GBP",
}


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return _SPACES_RE.sub(" ", value.replace("\xa0", " ")).strip()


def parse_price(raw: str | None) -> Decimal | None:
    """Перетворює текст ціни на Decimal.

    Розуміє формати: "1 299,00 грн", "1,299.00", "12999₴", "1.299,50 zł".
    Повертає None, якщо числа немає.
    """
    if raw is None:
        return None
    text = clean_text(str(raw)).replace(" ", "")
    text = _PRICE_CLEAN_RE.sub("", text)
    if not text:
        return None

    has_comma, has_dot = "," in text, "." in text
    if has_comma and has_dot:
        # роздільник дробової частини — той, що правіше
        decimal_sep = "," if text.rfind(",") > text.rfind(".") else "."
        thousand_sep = "." if decimal_sep == "," else ","
        text = text.replace(thousand_sep, "").replace(decimal_sep, ".")
    elif has_comma or has_dot:
        sep = "," if has_comma else "."
        head, tail = text.rsplit(sep, 1)
        # рівно 3 цифри після одного роздільника — це розділювач тисяч (1.299),
        # але не тоді, коли ціла частина нульова: 0.750 — це 75 копійок
        if len(tail) == 3 and len(text.replace(sep, "")) > 3 and head.strip("-") != "0":
            text = text.replace(sep, "")
        else:
            text = text.replace(sep, ".")

    try:
        value = Decimal(text).quantize(Decimal("0.01"))
    except InvalidOperation:
        # сміття вигляду "1e999" або число, довше за точність Decimal
        return None
    # від'ємної ціни не буває: це або помилка розбору, або чуже число поруч
    return value if value >= 0 else None


def detect_currency(raw: str | None, default: str = "UAH") -> str:
    if not raw:
        return default
    lowered = raw.lower()
    for sign, code in CURRENCY_SIGNS.items():
        if sign in lowered:
            return code
    return default


def absolute_url(base: str, href: str | None) -> str:
    if not href:
        return ""
    return urljoin(base, href.strip())


# мітки рекламних кампаній: у посиланні з реклами їх десятки, і вони
# роблять із однієї сторінки товару кілька різних URL
_TRACKING_PREFIXES = ("utm_", "gad_", "gbraid", "wbraid", "gclid", "fbclid", "msclkid", "srsltid")


def strip_tracking_params(url: str) -> str:
    """Прибирає рекламні мітки з адреси, решту параметрів лишає.

    Потрібно тому, що товар у БД шукається за URL: посилання з реклами і
    те саме посилання з сайту інакше стануть двома різними товарами.
    """
    if not url:
        return ""
    parts = urlsplit(url.strip())
    if not parts.query:
        return urlunsplit(parts)
    kept = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith(_TRACKING_PREFIXES)
    ]
    return urlunsplit(parts._replace(query=urlencode(kept)))
