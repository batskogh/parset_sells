"""Розсилання сповіщень про знижки.

Читає вже зібрану історію, вирішує, кому що сказати, і надсилає. Нічого не
парсить — це окремий крок, який запускається після `run_parser.py`.

За замовчуванням **нічого не надсилає**: показує, що пішло б, і на цьому
зупиняється. Щоб справді написати людям, потрібен `--send` — рішення надто
помітне для людей, щоб бути поведінкою за замовчуванням.

    python run_notify.py                # подивитись, що пішло б
    python run_notify.py --send         # надіслати
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

from app.bot.factory import TokenMissingError, create_bot
from app.bot.texts import доби, shop_recovered, shop_silent_alert
from app.config import get_settings
from app.core.health import HealthVerdict, check_shops, collect_states, mark_told
from app.core.notifier import collect, mark_sent
from app.db import repository as repo
from app.db.base import dispose_db, init_db, session_scope

logger = logging.getLogger("notify")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Розсилання сповіщень про знижки")
    parser.add_argument(
        "--send",
        action="store_true",
        help="справді надіслати (без цього — лише показати, що пішло б)",
    )
    parser.add_argument(
        "--all-products",
        action="store_true",
        help="враховувати й товари, яких зараз немає в наявності",
    )
    parser.add_argument(
        "--skip-health",
        action="store_true",
        help="не перевіряти, чи всі магазини читаються",
    )
    parser.add_argument("--log-level", default=None, help="DEBUG/INFO/WARNING")
    return parser


async def main_async(args: argparse.Namespace) -> int:
    await init_db()

    async with session_scope() as session:
        outgoing = await collect(session, only_in_stock=not args.all_products)

    # Здоров'я магазинів перевіряється тут навмисно, а не окремим юнітом:
    # мовчазний магазин найпомітніший саме тоді, коли писати нема про що —
    # тиша в розсилці і є першим симптомом
    verdicts = [] if args.skip_health else await _check_health()

    if not outgoing and not verdicts:
        print("Нема про що писати: нових знижок немає, магазини читаються.")
        return 0

    if verdicts:
        print(f"Про магазини: {len(verdicts)}")
        for verdict in verdicts:
            стан = f"мовчить {доби(verdict.silent_days)}" if verdict.silent else "знову читається"
            print(f"  → власнику: {verdict.shop.title} — {стан}")
        print()

    print(f"Готово до відправки: {len(outgoing)}\n")
    for item in outgoing:
        кому = item.user.username or item.user.first_name or item.user.telegram_id
        print(f"  → {кому}: {item.product.name[:55]}")
        print(f"    {item.signal.current} замість {item.signal.mode} (−{item.signal.drop_percent}%)")

    if not args.send:
        print("\nЦе був показ без відправки. Щоб надіслати: python run_notify.py --send")
        return 0

    try:
        bot = create_bot()
    except TokenMissingError as exc:
        print(exc)
        return 2

    надіслано = 0
    try:
        await _tell_owner_about_shops(bot, verdicts)
        for item in outgoing:
            try:
                await bot.send_message(
                    item.chat_id, item.text, disable_web_page_preview=False
                )
            except TelegramForbiddenError:
                # людина заблокувала бота — більше не турбуємо, список лишаємо
                logger.warning("Користувач %s заблокував бота", item.user.telegram_id)
                async with session_scope() as session:
                    await repo.deactivate_user(session, item.user.telegram_id)
                continue
            except TelegramRetryAfter as exc:
                logger.warning("Ліміт Telegram: чекаю %s с", exc.retry_after)
                await asyncio.sleep(exc.retry_after)
                await bot.send_message(item.chat_id, item.text)
            except Exception:  # noqa: BLE001 — один невдалий лист не спиняє розсилку
                logger.exception("Не вдалося написати %s", item.user.telegram_id)
                continue

            async with session_scope() as session:
                await mark_sent(session, item)
            надіслано += 1
    finally:
        await bot.session.close()

    print(f"\nНадіслано: {надіслано} з {len(outgoing)}")
    return 0


async def _check_health() -> list[HealthVerdict]:
    """Кому з магазинів погано просто зараз."""
    async with session_scope() as session:
        states = await collect_states(session)
    return check_shops(states)


async def _tell_owner_about_shops(bot, verdicts: list[HealthVerdict]) -> None:
    """Пише власнику про зламані й одужалі магазини.

    Мітка ставиться лише після вдалої відправки: інакше після збою мережі
    магазин вважався б «уже обговореним», і про поламаний парсер не сказали
    б ніколи.
    """
    owner = get_settings().telegram_chat_id
    if not owner or not verdicts:
        return

    for verdict in verdicts:
        shop = verdict.shop
        if verdict.silent:
            text = shop_silent_alert(
                title=shop.title,
                slug=shop.slug,
                days=verdict.silent_days,
                products=shop.products,
                error=shop.last_error,
            )
        else:
            text = shop_recovered(title=shop.title)

        try:
            await bot.send_message(owner, text, disable_web_page_preview=True)
        except Exception:  # noqa: BLE001 — один невдалий лист не спиняє решту
            logger.exception("Не вдалося написати власнику про %s", shop.slug)
            continue

        async with session_scope() as session:
            await mark_told(session, verdict)


def main() -> int:
    args = build_arg_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level or get_settings().log_level, logging.INFO),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    logging.getLogger("alembic.runtime.plugins").setLevel(logging.WARNING)
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
