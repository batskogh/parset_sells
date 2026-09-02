"""Створення бота й диспетчера.

Одне місце, де збирається бот, — щоб і polling (розробка), і вебхук
(робочий режим) працювали з тим самим набором хендлерів. Різниця між ними
лише в тому, звідки беруться апдейти.
"""

from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from app.bot.handlers import common
from app.bot.texts import BOT_COMMANDS
from app.config import get_settings

logger = logging.getLogger(__name__)


class TokenMissingError(RuntimeError):
    """Токен не заданий — бот не може працювати."""


def create_bot() -> Bot:
    settings = get_settings()
    if not settings.telegram_ready:
        raise TokenMissingError(
            "Не заданий TELEGRAM_BOT_TOKEN. Візьміть токен у @BotFather і "
            "впишіть його в .env — у репозиторій цей файл не потрапляє."
        )
    return Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher() -> Dispatcher:
    """Диспетчер з підключеними хендлерами."""
    dispatcher = Dispatcher()
    dispatcher.include_router(common.router)
    return dispatcher


async def set_bot_commands(bot: Bot) -> None:
    """Список команд у меню Telegram — щоб їх не треба було вгадувати."""
    await bot.set_my_commands(
        [BotCommand(command=name, description=title) for name, title in BOT_COMMANDS.items()]
    )
