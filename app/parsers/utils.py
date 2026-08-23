"""Допоміжні функції парсингу: ціна, текст, посилання."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin

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
        tail = text.rsplit(sep, 1)[1]
        # рівно 3 цифри після одного роздільника — це розділювач тисяч (1.299)
        if len(tail) == 3 and text.count(sep) >= 1 and len(text.replace(sep, "")) > 3:
            text = text.replace(sep, "")
        else:
            text = text.replace(sep, ".")

    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return value.quantize(Decimal("0.01"))


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
