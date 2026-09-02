"""Клавіатури під повідомленнями бота.

Тримаються окремо від хендлерів із тієї ж причини, що й тексти: так їх видно
всі разом, і так їх можна перевіряти без Telegram.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# префікс у callback_data; далі йде id товару
REMOVE_PREFIX = "rm"

# скільки кнопок ставити в один рядок, щоб вони не стискались до нечитабельних
BUTTONS_PER_ROW = 4


def remove_callback(product_id: int) -> str:
    return f"{REMOVE_PREFIX}:{product_id}"


def parse_remove_callback(data: str | None) -> int | None:
    """Дістає id товару з натиснутої кнопки; None — якщо це не наша кнопка."""
    if not data or not data.startswith(f"{REMOVE_PREFIX}:"):
        return None
    _, _, raw = data.partition(":")
    return int(raw) if raw.isdigit() else None


def watch_list_keyboard(items: list) -> InlineKeyboardMarkup:
    """Кнопки «прибрати» під списком.

    На кнопці — номер рядка зі списку: у неї не влізе назва товару, а номер
    людина бачить поруч і не переплутає.
    """
    buttons = [
        InlineKeyboardButton(text=f"✕ {номер}", callback_data=remove_callback(item.product_id))
        for номер, item in enumerate(items, start=1)
    ]
    rows = [
        buttons[i : i + BUTTONS_PER_ROW] for i in range(0, len(buttons), BUTTONS_PER_ROW)
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)
