"""Тести розкладки хендлерів бота.

Тут перевіряється не поведінка (вона живе в `test_bot_service.py`), а те, що
апдейт узагалі дійде до потрібного хендлера. aiogram перебирає їх у порядку
оголошення й віддає перший, чиї фільтри збіглися, — тож порядок у файлі це
не стиль, а логіка. Помилка тут тиха: команда просто перестає працювати, і
жоден тест поведінки цього не помітить, бо викликає сервіс напряму.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.bot.handlers.common import router

ЛОВЕЦЬ = "handle_text"


def імена_хендлерів() -> list[str]:
    return [handler.callback.__name__ for handler in router.message.handlers]


def фільтри(ім_я: str) -> list:
    handler = next(h for h in router.message.handlers if h.callback.__name__ == ім_я)
    return [f.magic for f in handler.filters or [] if getattr(f, "magic", None)]


def test_ловець_тексту_оголошений_останнім() -> None:
    """`F.text` збігається з будь-яким текстом, включно з командами."""
    імена = імена_хендлерів()
    assert імена[-1] == ЛОВЕЦЬ, (
        "хендлер, оголошений після ловця, ніколи не спрацює: "
        f"порядок зараз {імена}"
    )


def test_кожна_команда_оголошена_раніше_за_ловця() -> None:
    імена = імена_хендлерів()
    межа = імена.index(ЛОВЕЦЬ)
    команди = [ім_я for ім_я in імена if ім_я != ЛОВЕЦЬ]

    assert all(імена.index(ім_я) < межа for ім_я in команди)
    assert {"handle_start", "handle_help", "handle_stop", "handle_list"} <= set(команди)


def test_ловець_не_бере_на_себе_команди() -> None:
    """Другий замок на ті самі двері — на випадок, якщо порядок зіпсують."""
    перевірки = [magic.resolve(SimpleNamespace(text="/list")) for magic in фільтри(ЛОВЕЦЬ)]
    assert not all(перевірки), "команда не має потрапляти в ловець тексту"

    посилання = [
        magic.resolve(SimpleNamespace(text="https://citrus.ua/p/1.html"))
        for magic in фільтри(ЛОВЕЦЬ)
    ]
    assert all(посилання), "звичайне посилання ловець має брати"
