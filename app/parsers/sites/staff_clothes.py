"""Staff — парсер сторінок товарів. ВИМКНЕНО: сайт за Cloudflare.

Розмітка тут придатна: ціна лежить у `ld+json` (`Product` → `offers`, 750 UAH),
тож розбір робить `LdJsonProductParser`, а нижче — лише відмінності сайту.
Проблема не в розборі, а в доступі: сайт віддає сторінку тільки браузеру.

Що показала перевірка 2026-09-02:
  * `httpx` отримує 403 з `cf-mitigated: challenge` і сторінкою «Just a
    moment...» — це антибот-перевірка Cloudflare, а не блокування за robots
    (robots.txt `/product/` дозволяє);
  * заголовки й HTTP/2 нічого не міняють — перевірка дивиться на TLS-підпис
    клієнта, а не на те, як він представляється;
  * публічного фіда чи API, яким магазин ділиться навмисно, знайти не вдалося
    (`/sitemap.xml`, `/feed`, `/google-merchant.xml` — усе 403).

Обходити антибот-захист у цьому проєкті не будемо. Тому `enabled = False`:
парсер не бере участі в `--all`, але код лишається робочим — якщо магазин
відкриє фід або зніме перевірку, досить прибрати цей прапорець.
"""

from __future__ import annotations

import re

from app.parsers.base import LdJsonProductParser
from app.parsers.registry import register
from app.parsers.throttle import DelayPolicy
from app.parsers.utils import clean_text

# у назві товару магазин дописує SEO-хвіст: «… на зав'язках ціна»
_SEO_TAIL_RE = re.compile(r"\s+ціна\s*$", re.IGNORECASE)


@register
class StaffClothesParser(LdJsonProductParser):
    name = "staff_clothes"
    title = "Staff"
    base_url = "https://www.staff-clothes.com"
    currency = "UAH"

    # сайт за антибот-перевіркою Cloudflare — див. пояснення в шапці файлу
    enabled = False

    start_urls = [
        "https://www.staff-clothes.com/product/9en0116/",
    ]

    delay = DelayPolicy(min_delay=2.0, max_delay=4.0)
    headers = {
        "Accept-Language": "uk-UA,uk;q=0.9",
        "Referer": "https://www.staff-clothes.com/",
    }

    def extract_name(self, product: dict) -> str:
        """Прибирає SEO-хвіст, щоб у списку товарів була людська назва."""
        return _SEO_TAIL_RE.sub("", clean_text(str(product.get("name") or "")))
