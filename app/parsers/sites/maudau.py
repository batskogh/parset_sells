"""MAUDAU — парсер сторінок товарів.

Чому не `HtmlListParser`: сайт — SPA на Nuxt, розмітка каталогу збирається
скриптом і класи в ній згенеровані (міняються з кожним білдом). Натомість на
кожній сторінці товару лежить `application/ld+json` типу `Product` — звідти й
беремо ціну, валюту й наявність. Уся ця механіка живе в `LdJsonProductParser`.
"""

from __future__ import annotations

from app.parsers.base import LdJsonProductParser
from app.parsers.registry import register
from app.parsers.throttle import DelayPolicy


@register
class MaudauParser(LdJsonProductParser):
    name = "maudau"
    title = "MAUDAU"
    base_url = "https://maudau.com.ua"
    currency = "UAH"

    # сторінки товарів для ручного прогону; бот пізніше передаватиме свій список
    start_urls = [
        "https://maudau.com.ua/product/chipsy-khvyliasti-liuks-zi-smakom-lysychok-125-h-872080",
    ]

    # сторінка товару важка (~2 МБ), тож ходимо неквапливо
    delay = DelayPolicy(min_delay=2.0, max_delay=4.0)
    headers = {
        "Accept-Language": "uk-UA,uk;q=0.9",
        "Referer": "https://maudau.com.ua/",
    }

    # у розмітці цього сайту поля `url` немає — канонічну адресу брати нізвідки
    prefer_canonical_url = False

    def extract_external_id(self, product: dict, url: str) -> str | None:
        """Артикул — числовий хвіст адреси; `sku` сайт у розмітці не віддає.

        Хвіст `...-lysychok-125-h-872080` дає 872080 — той самий номер, що
        стоїть у назві товару на сайті.
        """
        tail = url.rstrip("/").rsplit("-", 1)[-1]
        return tail if tail.isdigit() else None
