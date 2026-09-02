"""Точка входу для парсингу.

Приклади:
    python run_parser.py --list
    python run_parser.py --all
    python run_parser.py --site demo_books --limit 10 --dry-run
    python run_parser.py --site shop_a --site shop_b --concurrency 2 --min-delay 2 --max-delay 5
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from app.config import get_settings
from app.core.health import record_runs
from app.core.runner import ParserRunner
from app.db import repository as repo
from app.db.base import dispose_db, init_db, session_scope
from app.parsers.registry import all_parsers, get_parser
from app.parsers.resolve import supports_single_product
from app.parsers.throttle import DelayPolicy


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Асинхронний парсер цін")
    parser.add_argument("--site", action="append", default=[], help="slug парсера (можна кілька разів)")
    parser.add_argument("--all", action="store_true", help="запустити всі активні парсери")
    parser.add_argument("--list", action="store_true", help="показати доступні парсери")
    parser.add_argument("--dry-run", action="store_true", help="без запису в БД")
    parser.add_argument("--limit", type=int, default=None, help="максимум товарів на сайт")
    parser.add_argument("--concurrency", type=int, default=None, help="скільки сайтів паралельно")
    parser.add_argument("--min-delay", type=float, default=None, help="мін. пауза між запитами, с")
    parser.add_argument("--max-delay", type=float, default=None, help="макс. пауза між запитами, с")
    parser.add_argument(
        "--from-code",
        action="store_true",
        help="брати адреси зі списку в коді парсера, а не з бази",
    )
    parser.add_argument("--log-level", default=None, help="DEBUG/INFO/WARNING")
    return parser


async def build_parser(cls, *, delay, limit, from_db: bool):
    """Готує парсер до прогону, підставивши адреси товарів із бази.

    Товари приходять від людей через бота, тож список у коді парсера — лише
    заготовка для ручного прогону. Щойно в базі з'явились товари цього
    магазину, стежимо саме за ними. Каталожних парсерів це не стосується:
    вони ходять по списках, а не по окремих сторінках.
    """
    kwargs = {"delay": delay, "max_items": limit}
    if not from_db or not supports_single_product(cls):
        return cls(**kwargs)

    async with session_scope() as session:
        urls = await repo.product_urls_for_shop(session, cls.name)

    logger = logging.getLogger("parser.targets")
    if not urls:
        logger.info("%s: у базі товарів немає — беру список із коду парсера", cls.name)
        return cls(**kwargs)

    logger.info("%s: стежу за %s товар(ами) з бази", cls.name, len(urls))
    return cls(product_urls=urls, **kwargs)


def print_parsers() -> None:
    print("Доступні парсери:")
    for cls in all_parsers(only_enabled=False):
        state = "" if cls.enabled else "  [вимкнено]"
        print(f"  {cls.name:<20} {cls.title or '-':<32} {cls.base_url}{state}")


async def main_async(args: argparse.Namespace) -> int:
    settings = get_settings()

    if args.list:
        print_parsers()
        return 0

    if args.all:
        classes = all_parsers()
    elif args.site:
        classes = [get_parser(slug) for slug in args.site]
    else:
        print("Вкажіть --all або --site <slug>. Список: --list")
        return 2

    delay_override = None
    if args.min_delay is not None or args.max_delay is not None:
        min_delay = args.min_delay if args.min_delay is not None else settings.default_min_delay
        max_delay = args.max_delay if args.max_delay is not None else settings.default_max_delay
        delay_override = DelayPolicy(min_delay=min_delay, max_delay=max_delay)

    await init_db()

    # Підготовка теж може впасти — база не відповіла, у магазина зіпсований
    # список адрес. Раніше це валило весь прогін ще до першого запиту, і
    # решта магазинів не отримувала свого шансу
    parsers = []
    broken: list[tuple[str, str]] = []
    for cls in classes:
        try:
            parsers.append(
                await build_parser(
                    cls, delay=delay_override, limit=args.limit, from_db=not args.from_code
                )
            )
        except Exception as помилка:  # noqa: BLE001 — один магазин не валить прогін
            logging.getLogger("parser.targets").exception(
                "%s не вдалося підготувати", cls.name
            )
            broken.append((cls.name, str(помилка)))

    if not parsers:
        print("Жоден парсер не вдалося підготувати:")
        for slug, причина in broken:
            print(f"  ! {slug}: {причина}")
        return 1

    runner = ParserRunner(
        parsers,
        max_concurrency=args.concurrency,
        dry_run=args.dry_run,
    )
    summary = await runner.run()
    print(summary.render())
    for slug, причина in broken:
        print(f"  [ERR] {slug}: не вдалося підготувати — {причина}")

    if not args.dry_run:
        async with session_scope() as session:
            await record_runs(session, summary, failed_to_build=broken)

    return summary.exit_code


def main() -> int:
    args = build_arg_parser().parse_args()
    setup_logging(args.log_level or get_settings().log_level)
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
