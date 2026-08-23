"""Службові SQL-view для зручного читання БД «очима людини».

Таблиця `prices` нормалізована: у ній лежить `product_id`, а не назва товару.
Щоб дивитись історію напряму в БД (DB Browser, DBeaver, `sqlite3`) і одразу
бачити «коли / що / де / почім», є два view. Дані вони не дублюють —
перераховуються на льоту, тому перейменування товару відразу видно в історії.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

# Повна історія: один рядок = один зріз ціни з датою, назвою і магазином
PRICE_HISTORY_VIEW = """
CREATE VIEW v_price_history AS
SELECT
    pr.parsed_at        AS parsed_at,      -- коли саме зафіксовано ціну
    p.name              AS product_name,   -- назва товару
    s.title             AS shop_title,     -- магазин
    pr.price            AS price,
    pr.old_price        AS old_price,
    pr.currency         AS currency,
    pr.in_stock         AS in_stock,
    p.external_id       AS external_id,
    p.category          AS category,
    p.url               AS product_url,
    s.slug              AS shop_slug,
    p.id                AS product_id,
    pr.id               AS price_id
FROM prices pr
JOIN products p ON p.id = pr.product_id
JOIN shops    s ON s.id = p.shop_id
"""

# Зріз «поточний стан»: остання відома ціна кожного товару та її дата
CURRENT_PRICES_VIEW = """
CREATE VIEW v_current_prices AS
SELECT
    p.name        AS product_name,
    s.title       AS shop_title,
    pr.price      AS price,
    pr.currency   AS currency,
    pr.parsed_at  AS price_date,
    pr.in_stock   AS in_stock,
    p.url         AS product_url,
    p.id          AS product_id
FROM products p
JOIN shops s ON s.id = p.shop_id
LEFT JOIN prices pr ON pr.id = (
    SELECT id FROM prices
    WHERE product_id = p.id
    ORDER BY parsed_at DESC, id DESC
    LIMIT 1
)
"""

VIEWS: dict[str, str] = {
    "v_price_history": PRICE_HISTORY_VIEW,
    "v_current_prices": CURRENT_PRICES_VIEW,
}


async def create_views(conn: AsyncConnection) -> None:
    """Перестворює view (вони не містять даних, тому DROP безпечний)."""
    for name, ddl in VIEWS.items():
        await conn.execute(text(f"DROP VIEW IF EXISTS {name}"))
        await conn.execute(text(ddl))
