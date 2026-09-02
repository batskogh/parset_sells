"""Пошук знижок у вже зібраній історії цін.

Нічого не парсить і нічого не пише — тільки читає базу й показує товари, що
подешевшали проти своєї звичної ціни.

Приклади:
    python run_discounts.py
    python run_discounts.py --min-drop 5 --min-samples 3
    python run_discounts.py --shop citrus --all-products
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from decimal import Decimal

from app.config import get_settings
from app.core.discount import DiscountRules, price_mode
from app.core.aggregate import (
    assortment_report,
    basket_discount,
    group_reports,
    shop_index,
)
from app.core.discount_scan import MAX_PRODUCTS, load_histories, scan_products
from app.core.timeline import daily_values
from app.db import repository as repo
from app.db.base import dispose_db, session_scope


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Пошук знижок за модою історії цін")
    parser.add_argument("--shop", default=None, help="slug магазину (типово — усі)")
    parser.add_argument("--min-drop", type=float, default=None, help="поріг падіння, %%")
    parser.add_argument("--min-samples", type=int, default=None, help="мінімум замірів в історії")
    parser.add_argument("--bucket", type=float, default=None, help="ширина кошика моди, %%")
    parser.add_argument(
        "--all-products",
        action="store_true",
        help="перевіряти й ті товари, яких зараз немає в наявності",
    )
    parser.add_argument(
        "--modes",
        action="store_true",
        help="показати звичну ціну кожного товару — видно, з чим працює детектор",
    )
    parser.add_argument(
        "--basket",
        action="store_true",
        help="кошик: звична і поточна вартість набору товарів як цілого",
    )
    parser.add_argument(
        "--assortment",
        action="store_true",
        help="асортимент: типова ціна товару в магазині й ті, що дешевші за неї",
    )
    parser.add_argument(
        "--index",
        action="store_true",
        help="індекс: чи магазин загалом подорожчав проти звичного",
    )
    parser.add_argument(
        "--groups",
        action="store_true",
        help="товарні групи: порівняння схожих товарів між собою",
    )
    parser.add_argument(
        "--everything",
        action="store_true",
        help="усі рівні одразу: товар, кошик, асортимент, індекс, групи",
    )
    parser.add_argument("--log-level", default=None, help="DEBUG/INFO/WARNING")
    return parser


def build_rules(args: argparse.Namespace) -> DiscountRules:
    base = DiscountRules.from_settings()
    return DiscountRules(
        min_drop_percent=(
            Decimal(str(args.min_drop)) if args.min_drop is not None else base.min_drop_percent
        ),
        min_samples=args.min_samples if args.min_samples is not None else base.min_samples,
        bucket_percent=(
            Decimal(str(args.bucket)) if args.bucket is not None else base.bucket_percent
        ),
    )


async def print_modes(session, *, rules: DiscountRules, shop_id: int | None) -> None:
    """Показує, що детектор бачить у базі: звичну ціну й довжину історії."""
    products = await repo.list_products(
        session, repo.ProductFilter(shop_id=shop_id, only_active=True, limit=MAX_PRODUCTS)
    )
    print("\nЗвична ціна за історією:\n")
    for product in products:
        prices = daily_values((p.parsed_at, p.price) for p in product.prices)
        mode = price_mode(prices, bucket_percent=rules.bucket_percent) if prices else None
        if mode is None:
            print(f"  {product.name[:52]:<52} історії немає")
            continue
        enough = "" if mode.samples >= rules.min_samples else "  (замало замірів)"
        print(
            f"  {product.name[:52]:<52} {mode.value} × {mode.samples} замірів, "
            f"остання {prices[-1]}{enough}"
        )


def short_history_hint(histories) -> str:
    """Пояснює, чому спільна історія набору коротка.

    Ряд починається з дати, коли відомі всі товари, тож один товар, доданий
    учора, обрізає весь кошик до одного дня. Без цієї підказки «1 доба
    історії» виглядає як поломка.
    """
    with_start = [(min(day for day, _ in item.points), item) for item in histories if item.points]
    if not with_start:
        return ""
    start, latest = max(with_start, key=lambda pair: pair[0])
    return (
        f"ряд починається з {start:%d.%m.%Y} — пізніше за всіх у набір "
        f"додано «{latest.name[:40]}»"
    )


async def print_levels(histories, *, args: argparse.Namespace, rules: DiscountRules) -> None:
    """Рівні вище окремого товару: кошик, асортимент, індекс, групи."""
    if not histories:
        print("\nІсторії ще немає — нема з чого рахувати рівні вище товару.")
        return

    points = {item.id: item.points for item in histories}
    prices_now = {item.id: item.current for item in histories if item.current is not None}
    names = {item.id: item.name for item in histories}
    label = dict(names)

    if args.basket or args.everything:
        report = basket_discount(points, rules=rules)
        print("\n── Кошик ─────────────────────────────────────────────")
        print(f"  {report.render()}" if report else "  замало спільної історії")
        if report is not None and report.days < rules.min_samples:
            print(f"  {short_history_hint(histories)}")

    if args.assortment or args.everything:
        report = assortment_report(prices_now, rules=rules)
        print("\n── Асортимент ────────────────────────────────────────")
        if report is None:
            print("  цін немає")
        else:
            print(f"  {report.render()}")
            for item in report.cheaper[:10]:
                print(f"    {label.get(item.key, item.key)[:50]} — {item.price} "
                      f"(на {item.drop_percent}% нижче типової)")

    if args.index or args.everything:
        report = shop_index(points, rules=rules)
        print("\n── Індекс магазину ───────────────────────────────────")
        print(f"  {report.render()}" if report else "  замало спільної історії")

    if args.groups or args.everything:
        reports = group_reports(names, prices_now, rules=rules)
        print("\n── Товарні групи ─────────────────────────────────────")
        if not reports:
            print("  схожих товарів не знайшлось (потрібно щонайменше 3 з одною моделлю)")
        for report in reports[:10]:
            print(f"  {report.render()}")
            for item in report.cheaper[:5]:
                print(f"    {label.get(item.key, item.key)[:50]} — {item.price} "
                      f"(на {item.drop_percent}% нижче типової)")


async def main_async(args: argparse.Namespace) -> int:
    rules = build_rules(args)
    print(
        f"Правила: падіння понад {rules.min_drop_percent}% нижче моди, "
        f"мінімум {rules.min_samples} замірів, кошик {rules.bucket_percent}%"
    )

    async with session_scope() as session:
        shop_id = None
        if args.shop:
            shop = await repo.get_shop_by_slug(session, args.shop)
            if shop is None:
                print(f"Магазин '{args.shop}' не знайдено")
                return 2
            shop_id = shop.id

        found = await scan_products(
            session, rules=rules, shop_id=shop_id, only_in_stock=not args.all_products
        )
        if args.modes:
            await print_modes(session, rules=rules, shop_id=shop_id)

        if args.basket or args.assortment or args.index or args.groups or args.everything:
            histories = await load_histories(session, shop_id=shop_id)
            await print_levels(histories, args=args, rules=rules)

    if not found:
        print("Знижок не знайдено.")
        return 0

    print(f"\nЗнайдено знижок: {len(found)}\n")
    for item in found:
        print(f"  {item.render()}")
        print(f"    {item.product.url}")
    return 0


def main() -> int:
    args = build_arg_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level or get_settings().log_level, logging.INFO),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    try:
        return asyncio.run(_with_cleanup(args))
    except KeyboardInterrupt:
        print("\nПерервано користувачем")
        return 130


async def _with_cleanup(args: argparse.Namespace) -> int:
    try:
        return await main_async(args)
    finally:
        await dispose_db()


if __name__ == "__main__":
    raise SystemExit(main())
