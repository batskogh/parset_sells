"""Базові команди: /start, /help, /stop.

Хендлери тут навмисно тонкі — вони дістають дані з апдейта й передають їх
далі. Уся робота, яку варто перевіряти тестами, живе в `app.bot.service`:
до неї не треба ані Telegram, ані мережі.
"""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from app.bot import service
from app.bot.texts import help_text, stopped
from app.db.base import session_scope

router = Router(name="common")
logger = logging.getLogger(__name__)


@router.message(CommandStart())
async def handle_start(message: Message) -> None:
    if message.from_user is None:          # службові повідомлення без автора
        return

    async with session_scope() as session:
        text = await service.greet(
            session,
            telegram_id=message.from_user.id,
            chat_id=message.chat.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
        )
    await message.answer(text)


@router.message(Command("help"))
async def handle_help(message: Message) -> None:
    await message.answer(help_text())


@router.message(Command("stop"))
async def handle_stop(message: Message) -> None:
    if message.from_user is None:
        return

    async with session_scope() as session:
        await service.stop_notifications(session, telegram_id=message.from_user.id)
    await message.answer(stopped())
