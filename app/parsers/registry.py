"""Реєстр парсерів.

Кожен парсер у `app/parsers/sites/` позначається декоратором `@register`
і автоматично підхоплюється — правити список у коді не треба.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import TypeVar

from app.parsers.base import BaseParser

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, type[BaseParser]] = {}
_loaded = False

T = TypeVar("T", bound=type[BaseParser])


def register(cls: T) -> T:
    """Декоратор реєстрації парсера."""
    if not getattr(cls, "name", ""):
        raise ValueError(f"{cls.__name__}: атрибут `name` обов'язковий для реєстрації")
    if cls.name in _REGISTRY and _REGISTRY[cls.name] is not cls:
        raise ValueError(f"Парсер зі slug '{cls.name}' вже зареєстровано")
    _REGISTRY[cls.name] = cls
    return cls


def discover() -> None:
    """Імпортує всі модулі з `app.parsers.sites`, щоб спрацювали декоратори."""
    global _loaded
    if _loaded:
        return

    from app.parsers import sites

    for module_info in pkgutil.iter_modules(sites.__path__):
        if module_info.name.startswith("_"):
            continue
        try:
            importlib.import_module(f"{sites.__name__}.{module_info.name}")
        except Exception:  # noqa: BLE001 — зламаний модуль не має валити решту
            logger.exception("Не вдалося імпортувати парсер %s", module_info.name)
    _loaded = True


def all_parsers(*, only_enabled: bool = True) -> list[type[BaseParser]]:
    discover()
    items = sorted(_REGISTRY.values(), key=lambda cls: cls.name)
    if only_enabled:
        items = [cls for cls in items if cls.enabled]
    return items


def get_parser(slug: str) -> type[BaseParser]:
    discover()
    try:
        return _REGISTRY[slug]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "—"
        raise KeyError(f"Парсер '{slug}' не знайдено. Доступні: {known}") from None


def parser_slugs() -> list[str]:
    return [cls.name for cls in all_parsers()]
