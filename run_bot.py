"""Запуск бота в режимі polling.

Це режим для розробки: бот сам питає Telegram про нові повідомлення, тож
публічна адреса не потрібна. Робочий режим планується на вебхуках — там
Telegram сам стукає до нас, але для цього потрібен HTTPS із дійсним
сертифікатом (тунель або хостинг).

Хендлери в обох режимах ті самі — різниця лише в тому, звідки беруться
апдейти, тож перехід на вебхук нічого тут не ламає.

    python run_bot.py
    python run_bot.py --log-level DEBUG
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from app.bot.factory import TokenMissingError, create_bot, create_dispatcher, set_bot_commands
from app.config import get_settings
from app.db.base import dispose_db, init_db


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Телеграм-бот стеження за цінами")
    parser.add_argument("--log-level", default=None, help="DEBUG/INFO/WARNING")
    parser.add_argument(
        "--skip-updates",
        action="store_true",
        help="не розбирати повідомлення, що надійшли, поки бот був вимкнений",
    )
    return parser


async def main_async(args: argparse.Namespace) -> int:
    logger = logging.getLogger("bot")

    try:
        bot = create_bot()
    except TokenMissingError as exc:
        print(exc)
        return 2

    # схема має бути свіжою: /start одразу пише в telegram_users
    await init_db()

    dispatcher = create_dispatcher()
    try:
        me = await bot.get_me()
        logger.info("Бот @%s готовий", me.username)
        await set_bot_commands(bot)

        if args.skip_updates:
            await bot.delete_webhook(drop_pending_updates=True)

        await dispatcher.start_polling(bot, handle_signals=False)
    finally:
        await bot.session.close()
        await dispose_db()
    return 0


def main() -> int:
    args = build_arg_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level or get_settings().log_level, logging.INFO),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    # службовий шум alembic не потрібен у логах бота — міграції видно й так
    logging.getLogger("alembic.runtime.plugins").setLevel(logging.WARNING)
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\nБот зупинений")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
