"""Проміжні структури даних, які повертають парсери."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


@dataclass(slots=True)
class ParsedProduct:
    """Один товар, знятий із сайту.

    Це «сирий» результат парсингу — у БД він перетворюється на Product + Price.
    """

    name: str
    url: str
    price: Decimal
    old_price: Decimal | None = None
    currency: str = "UAH"
    in_stock: bool = True
    external_id: str | None = None
    image_url: str | None = None
    category: str | None = None
    parsed_at: datetime | None = None

    def __post_init__(self) -> None:
        self.name = " ".join(self.name.split())
        if not self.name:
            raise ValueError("ParsedProduct.name не може бути порожнім")
        if not self.url:
            raise ValueError(f"ParsedProduct.url не може бути порожнім ({self.name!r})")


@dataclass(slots=True)
class ParseResult:
    """Підсумок роботи одного парсера."""

    shop_slug: str
    items: list[ParsedProduct] = field(default_factory=list)
    pages_visited: int = 0
    errors: list[str] = field(default_factory=list)
    duration: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.errors
