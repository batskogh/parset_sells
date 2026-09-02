"""Цитрус — парсер сторінок товарів.

Другий реальний магазин проєкту. Обраний після того, як Rozetka і
staff-clothes виявились за антибот-перевіркою Cloudflare: цей сайт віддає
сторінку звичайному HTTP-клієнту й тримає ціну в `ld+json`.

Особливості проти `maudau`:
  * канонічна адреса лежить у `Offer.url`, а не в `Product.url`;
  * артикул віддається полем `sku` числом (напр. 791431) — той самий номер
    стоїть у хвості адреси товару;
  * `availability` пишеться через `http://schema.org/...` (не https) —
    базовий клас звіряє лише останній сегмент, тож це не заважає.
"""

from __future__ import annotations

from app.parsers.base import LdJsonProductParser
from app.parsers.registry import register
from app.parsers.throttle import DelayPolicy


@register
class CitrusParser(LdJsonProductParser):
    name = "citrus"
    title = "Цитрус"
    base_url = "https://citrus.ua"
    currency = "UAH"

    start_urls = [
        # MSI RTX 5070 Gaming Trio OC — на момент заведення «немає в наявності»
        "https://citrus.ua/videokarti/videokarta-msi-rtx5070-12gb-gddr7"
        "-rtx5070-12gb-gaming-rtx5070-12g-gam-trio-oc-791431.html",
    ]

    delay = DelayPolicy(min_delay=2.0, max_delay=4.0)
    headers = {
        "Accept-Language": "uk-UA,uk;q=0.9",
        "Referer": "https://citrus.ua/",
    }
