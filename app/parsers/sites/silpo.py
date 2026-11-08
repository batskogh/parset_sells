"""Сільпо — парсер сторінок товарів. ВИМКНЕНО: сайт за Cloudflare.

Розмітка придатна повністю: на сторінці товару лежить `ld+json` типу
`Product` з `offers.price` (28.99 UAH), `availability` і `sku` — тож розбір
цілком робить `LdJsonProductParser`, власного коду тут немає. Проблема не в
розборі, а в доступі: сторінку сайт віддає тільки браузеру.

Що показала перевірка 2026-11-08:
  * `httpx` отримує 403 зі сторінкою «Just a moment...» — антибот-перевірка
    Cloudflare; браузер її проходить, і сторінка в ньому зібрана на сервері
    разом із розміткою;
  * дані на сторінку кладе сервер (Angular SSR), а не скрипт у браузері —
    окремого запиту за ціною, який можна було б повторити, сторінка не робить.

Обходити антибот-захист у цьому проєкті не будемо (як і зі `staff_clothes`).
Тому `enabled = False`: парсер не бере участі в `--all`, а бот на посилання
з Сільпо відповідає, що магазин захищений, замість заявки на «невідомий
магазин». Якщо Сільпо зніме перевірку — досить прибрати цей прапорець.

Ціна в Сільпо залежить від магазину-філії. Без вибраної адреси сайт показує
ціну типової філії — саме вона й потрапляє в розмітку.
"""

from __future__ import annotations

from app.parsers.base import LdJsonProductParser
from app.parsers.registry import register
from app.parsers.throttle import DelayPolicy


@register
class SilpoParser(LdJsonProductParser):
    name = "silpo"
    title = "Сільпо"
    base_url = "https://silpo.ua"
    currency = "UAH"

    # сайт за антибот-перевіркою Cloudflare — див. пояснення в шапці файлу
    enabled = False

    start_urls = [
        "https://silpo.ua/product/sukharyky-jokers-pshenychno-zhytni-smak-smetana-ta-zelen-1021809",
    ]

    delay = DelayPolicy(min_delay=2.0, max_delay=4.0)
    headers = {
        "Accept-Language": "uk-UA,uk;q=0.9",
        "Referer": "https://silpo.ua/",
    }
