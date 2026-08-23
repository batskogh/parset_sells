# Парсер цін

Асинхронний парсинг цін із кількох сайтів + SQLite-база + локальна панель CRUD.

## Що всередині

```
parset_sells/
├── run_parser.py              # CLI запуску парсингу
├── run_web.py                 # запуск локальної панелі
├── requirements.txt
├── .env.example               # скопіювати в .env і поправити
└── app/
    ├── config.py              # налаштування з .env
    ├── core/
    │   ├── runner.py          # оркестратор: паралельно по сайтах
    │   └── storage.py         # запис результату в БД
    ├── db/
    │   ├── base.py            # engine + сесії
    │   ├── models.py          # Shop / Product / Price
    │   └── repository.py      # CRUD-операції
    ├── parsers/
    │   ├── base.py            # BaseParser (ABC) + HtmlListParser
    │   ├── registry.py        # автореєстрація парсерів
    │   ├── http.py            # HTTP-клієнт з ретраями
    │   ├── throttle.py        # DelayPolicy — затримки
    │   ├── utils.py           # parse_price, clean_text, absolute_url
    │   └── sites/
    │       ├── _template.py   # ШАБЛОН для нового сайту
    │       └── demo_books.py  # робочий приклад
    └── web/
        ├── main.py            # FastAPI-застосунок
        ├── routes_ui.py       # HTML CRUD
        ├── routes_api.py      # JSON API CRUD
        ├── templates/
        └── static/
```

## Встановлення

```bash
python -m venv .venv
```

```bash
.venv\Scripts\activate
```

```bash
pip install -r requirements.txt
```

```bash
copy .env.example .env
```

## Запуск

Список доступних парсерів:

```bash
python run_parser.py --list
```

Пробний прогін без запису в БД:

```bash
python run_parser.py --site demo_books --limit 10 --dry-run
```

Усі сайти з записом у БД:

```bash
python run_parser.py --all
```

Локальна панель (CRUD) — http://127.0.0.1:8010 :

```bash
python run_web.py
```

Порт задається в `.env` (`WEB_PORT`) або прапорцем:

```bash
python run_web.py --port 8123
```

Корисні прапорці `run_parser.py`:

| Прапорець | Призначення |
|---|---|
| `--site <slug>` | конкретний парсер (можна вказати кілька разів) |
| `--all` | усі активні парсери |
| `--limit N` | максимум товарів на сайт (для тестів) |
| `--dry-run` | без запису в БД |
| `--concurrency N` | скільки сайтів паралельно |
| `--min-delay` / `--max-delay` | перекрити затримки для всіх парсерів |
| `--log-level DEBUG` | детальні логи |

## Як додати новий сайт

1. Скопіюйте `app/parsers/sites/_template.py` у файл з назвою сайту, напр. `rozetka.py`
   (файли на `_` реєстр ігнорує).
2. Розкоментуйте `@register`, заповніть `name`, `title`, `base_url`, `start_urls` і CSS-селектори.
3. Перевірте: `python run_parser.py --site rozetka --limit 5 --dry-run --log-level DEBUG`.

Мінімальний парсер:

```python
from app.parsers.base import HtmlListParser
from app.parsers.registry import register
from app.parsers.throttle import DelayPolicy


@register
class RozetkaParser(HtmlListParser):
    name = "rozetka"
    title = "Rozetka"
    base_url = "https://rozetka.com.ua"
    start_urls = ["https://rozetka.com.ua/notebooks/c80004/"]

    item_selector = ".goods-tile"
    name_selector = ".goods-tile__title"
    price_selector = ".goods-tile__price-value"
    link_selector = "a.goods-tile__heading"
    next_page_selector = "a.pagination__direction--forward"

    delay = DelayPolicy(min_delay=2.0, max_delay=5.0)
```

Якщо розмітка нестандартна — перевизначте точково: `extract_price`, `extract_name`,
`extract_external_id`, `parse_card`, `next_page_url`. Якщо сайт віддає JSON —
успадкуйтесь напряму від `BaseParser` і реалізуйте `collect()` (приклад у `_template.py`).

## Модель паралелізму

* **Між сайтами — паралельно.** `ParserRunner` запускає парсери через `asyncio.gather`,
  кількість одночасних сайтів обмежує семафор (`MAX_CONCURRENT_SITES`).
* **Усередині сайту — послідовно.** Кожен парсер робить `await` на кожен запит по черзі:
  жодних одночасних запитів до одного домену.
* **Затримки.** `DelayPolicy(min_delay, max_delay)` — випадкова пауза перед кожним
  запитом (щоб не бити рівним ритмом); `page_delay` — окрема пауза між сторінками каталогу.
  Задається на рівні класу парсера, глобально в `.env` або прапорцями CLI.
* **Стійкість.** Ретраї з експоненційним backoff на 429/5xx; збій одного сайту або однієї
  картки не зупиняє решту — помилки збираються в `ParseResult.errors`.
* **Запис у БД** серіалізовано окремим локом: SQLite не любить паралельних записів.

## База даних

SQLite (`data/prices.db`), три таблиці:

* `shops` — магазин: `slug` (= `name` парсера), `title`, `base_url`, `is_active`;
* `products` — товар: `shop_id`, `name`, `url`, `external_id`, `category`, `image_url`,
  `notes`, `is_active`; унікальність за парою (`shop_id`, `url`);
* `prices` — історія: `product_id`, `price`, `old_price`, `currency`, `in_stock`, `parsed_at`.

Кожен прогін додає **новий рядок** у `prices` — так зберігається історія:
`parsed_at` фіксує, коли саме діяла ця ціна.
Щоб писати лише зміни, поставте `STORE_PRICE_ONLY_ON_CHANGE=true` у `.env`.

Видалення магазину каскадом видаляє його товари й ціни.

### Читання бази напряму

Таблиця `prices` нормалізована — у ній `product_id`, а не назва. Щоб дивитись історію
в DB Browser / DBeaver / `sqlite3` і одразу бачити «коли, що, де, почім», є два view
(дані не дублюються, рахуються на льоту):

* `v_price_history` — уся історія: `parsed_at`, `product_name`, `shop_title`, `price`,
  `old_price`, `currency`, `in_stock`, `product_url`;
* `v_current_prices` — лише остання ціна кожного товару та її дата (`price_date`).

```sql
SELECT parsed_at, product_name, shop_title, price, currency
FROM v_price_history
WHERE product_name LIKE '%ноутбук%'
ORDER BY parsed_at DESC;
```

## Веб-панель

* `/products` — список із пошуком, фільтром за магазином і пагінацією;
* `/products/new`, `/products/{id}/edit` — створення й редагування;
* `/products/{id}` — картка товару з історією цін (ціну можна додати або видалити вручну);
* `/shops` — CRUD магазинів;
* `/docs` — Swagger для JSON API (`/api/products`, `/api/shops`, `/api/products/{id}/prices`).

## Про відповідальність

Перед парсингом конкретного сайту перевірте його `robots.txt` та умови використання,
тримайте адекватні затримки й не створюйте зайвого навантаження.
