"""Базові команди: /start, /help, /stop.

Хендлери тут навмисно тонкі — вони дістають дані з апдейта й передають їх
далі. Уся робота, яку варто перевіряти тестами, живе в `app.bot.service`:
до неї не треба ані Telegram, ані мережі.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from app.bot import service
from app.bot.keyboards import REMOVE_PREFIX, parse_remove_callback, watch_list_keyboard
from app.bot.texts import (
    empty_list,
    help_text,
    not_a_link,
    remove_failed,
    removed,
    stopped,
    watch_list,
)
from app.db.base import session_scope
from app.parsers.resolve import extract_url

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


@router.message(F.text)
async def handle_text(message: Message) -> None:
    """Будь-який текст, що не команда, — спроба додати товар за посиланням."""
    if message.from_user is None:
        return

    url = extract_url(message.text)
    if url is None:
        await message.answer(not_a_link())
        return

    # похід на сторінку займає секунди — хай людина бачить, що бот не завис
    await message.chat.do("typing")

    async with session_scope() as session:
        result = await service.add_product_by_url(
            session,
            telegram_id=message.from_user.id,
            chat_id=message.chat.id,
            message_text=message.text,
        )
    await message.answer(result.text, disable_web_page_preview=True)


@router.message(Command("list"))
async def handle_list(message: Message) -> None:
    if message.from_user is None:
        return

    async with session_scope() as session:
        items = await service.list_watched(session, telegram_id=message.from_user.id)

    if not items:
        await message.answer(empty_list())
        return

    await message.answer(
        watch_list(items),
        reply_markup=watch_list_keyboard(items),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith(f"{REMOVE_PREFIX}:"))
async def handle_remove(callback: CallbackQuery) -> None:
    """Натиснута кнопка «прибрати» під списком."""
    product_id = parse_remove_callback(callback.data)
    if product_id is None or callback.from_user is None:
        await callback.answer()
        return

    async with session_scope() as session:
        name = await service.remove_watched(
            session, telegram_id=callback.from_user.id, product_id=product_id
        )
        items = await service.list_watched(session, telegram_id=callback.from_user.id)

    await callback.answer(removed(name) if name else remove_failed())

    if isinstance(callback.message, Message):
        # перемальовуємо список, щоб номери кнопок далі збігалися з рядками
        if items:
            await callback.message.edit_text(
                watch_list(items),
                reply_markup=watch_list_keyboard(items),
                disable_web_page_preview=True,
            )
        else:
            await callback.message.edit_text(empty_list())
