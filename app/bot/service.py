"""Робота бота без Telegram.

Хендлер має бути тонким: узяв дані з апдейта — віддав сюди. Тоді поведінку
бота можна перевіряти звичайними тестами з базою, не піднімаючи ані мережі,
ані самого Telegram, і не вигадуючи підробки для типів aiogram.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.texts import welcome
from app.db import repository as repo

logger = logging.getLogger(__name__)


async def greet(
    session: AsyncSession,
    *,
    telegram_id: int,
    chat_id: int,
    username: str | None = None,
    first_name: str | None = None,
) -> str:
    """Реєструє людину (або вітає знайому) і повертає текст відповіді."""
    user, created = await repo.get_or_create_user(
        session,
        telegram_id=telegram_id,
        chat_id=chat_id,
        username=username,
        first_name=first_name,
    )
    logger.info(
        "%s користувач %s (%s)",
        "Новий" if created else "Повернувся",
        telegram_id,
        username or "без імені",
    )
    return welcome(user.first_name, returning=not created)


async def stop_notifications(session: AsyncSession, *, telegram_id: int) -> bool:
    """Вимикає сповіщення, не чіпаючи список товарів."""
    stopped = await repo.deactivate_user(session, telegram_id)
    if stopped:
        logger.info("Користувач %s вимкнув сповіщення", telegram_id)
    return stopped
