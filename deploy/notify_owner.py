"""Службове повідомлення власнику бота.

Потрібне тому, що машина стоїть на горищі: єдиний спосіб дізнатися, що з нею
сталося, — щоб вона сама написала. Використовується `deploy/update.sh` після
кожної спроби оновлення.

    python deploy/notify_owner.py --status updated --branch main --revision abc1234
    python deploy/notify_owner.py --status rolled_back --details "$(tail -20 log)"
    echo "будь-який текст" | python deploy/notify_owner.py --raw

Мовчить (і виходить з нулем), якщо TELEGRAM_CHAT_ID не заданий: відсутність
адресата — не привід валити оновлення, яке вже відбулося.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from pathlib import Path

# Python кладе в sys.path каталог самого скрипта, а не той, звідки його
# запустили. Решта точок входу лежить у корені й про це не думає, а цей
# скрипт живе в deploy/ — без цього рядка `app` для нього не існує
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiogram.exceptions import TelegramAPIError  # noqa: E402

from app.bot.factory import TokenMissingError, create_bot  # noqa: E402
from app.bot.texts import update_report  # noqa: E402
from app.config import get_settings  # noqa: E402


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Написати власнику бота")
    parser.add_argument(
        "--status",
        default="updated",
        choices=["updated", "rolled_back", "failed"],
        help="що саме сталося з оновленням",
    )
    parser.add_argument("--branch", default="", help="гілка, з якої оновлювались")
    parser.add_argument("--revision", default="", help="коміт після оновлення")
    parser.add_argument(
        "--commit",
        action="append",
        default=[],
        dest="commits",
        help="заголовок коміта (можна кілька разів)",
    )
    parser.add_argument("--details", default="", help="хвіст логу або текст помилки")
    parser.add_argument(
        "--raw",
        action="store_true",
        help="надіслати текст зі stdin як є, без складання звіту",
    )
    return parser


async def send(text: str) -> int:
    settings = get_settings()
    if not settings.telegram_chat_id:
        print("TELEGRAM_CHAT_ID не заданий — нема кому писати.", file=sys.stderr)
        return 0

    try:
        bot = create_bot()
    except TokenMissingError as помилка:
        print(помилка, file=sys.stderr)
        return 0

    try:
        await bot.send_message(
            settings.telegram_chat_id, text, disable_web_page_preview=True
        )
    except TelegramAPIError as помилка:
        print(f"Telegram не прийняв повідомлення: {помилка}", file=sys.stderr)
        return 1
    finally:
        await bot.session.close()
    return 0


def main() -> int:
    args = build_arg_parser().parse_args()
    if args.raw:
        text = sys.stdin.read().strip()
        if not text:
            return 0
    else:
        text = update_report(
            status=args.status,
            branch=args.branch,
            revision=args.revision,
            commits=args.commits,
            details=args.details,
        )
    return asyncio.run(send(text))


if __name__ == "__main__":
    raise SystemExit(main())
