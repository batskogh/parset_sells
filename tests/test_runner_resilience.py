"""Тести стійкості прогону до збою окремого магазину.

Питання, на яке відповідають ці тести: що станеться з рештою, коли один
магазин ліг. Правильна відповідь — нічого. Магазин може віддати 403, змінити
розмітку, відповідати вічно або взагалі не запуститись; ціни інших магазинів
від цього не мають зникнути, а прогін — вважатися провальним.

Другий бік тієї ж монети — код виходу. Він потрапляє в systemd, і саме за
ним людина судить, чи все гаразд. Одна помилка з одного магазину, піднята до
`failed` на весь прогін, привчає не вірити червоному кольору.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from app.core.runner import ParserRunner, RunSummary
from app.parsers.models import ParsedProduct, ParseResult
from app.parsers.throttle import DelayPolicy

D = Decimal


class ФейковийПарсер:
    """Парсер рівно настільки, наскільки його бачить раннер."""

    def __init__(self, name: str, *, товарів: int = 1, помилка: str | None = None,
                 падає: bool = False) -> None:
        self.name = name
        self.title = name
        self.base_url = f"https://{name}.example"
        self.товарів = товарів
        self.помилка = помилка
        self.падає = падає
        self.delay = DelayPolicy(min_delay=0, max_delay=0)

    async def __aenter__(self):
        if self.падає:
            raise RuntimeError(f"{self.name}: мережа недоступна")
        return self

    async def __aexit__(self, *exc_info) -> None:
        return None

    async def run(self) -> ParseResult:
        items = [
            ParsedProduct(name=f"Товар {і}", url=f"{self.base_url}/{і}", price=D("100"))
            for і in range(self.товарів)
        ]
        return ParseResult(
            shop_slug=self.name,
            items=items,
            errors=[self.помилка] if self.помилка else [],
        )


def прогнати(parsers) -> RunSummary:
    runner = ParserRunner(parsers, dry_run=True)
    return asyncio.run(runner.run())


# ------------------------------------------------------- збій одного магазину


def test_магазин_що_не_запустився_не_чіпає_решту() -> None:
    summary = прогнати(
        [
            ФейковийПарсер("живий", товарів=3),
            ФейковийПарсер("мертвий", падає=True),
            ФейковийПарсер("теж_живий", товарів=2),
        ]
    )

    assert summary.total_items == 5, "товари решти магазинів мають зберегтись"
    assert [r.shop_slug for r in summary.productive_sites] == ["живий", "теж_живий"]
    assert [r.shop_slug for r in summary.barren_sites] == ["мертвий"]


def test_причина_збою_потрапляє_в_підсумок() -> None:
    summary = прогнати([ФейковийПарсер("мертвий", падає=True)])

    assert summary.total_errors == 1
    assert "мережа недоступна" in summary.results[0].errors[0]


def test_магазин_з_помилкою_але_з_товарами_вважається_продуктивним() -> None:
    """Каталог зчитався наполовину — це половина здобичі, а не провал."""
    summary = прогнати([ФейковийПарсер("частковий", товарів=2, помилка="сторінка 3: 500")])

    assert summary.total_errors == 1
    assert [r.shop_slug for r in summary.productive_sites] == ["частковий"]


# --------------------------------------------------------------- код виходу


def test_код_виходу_нуль_поки_хоч_щось_зібралось() -> None:
    summary = прогнати([ФейковийПарсер("живий"), ФейковийПарсер("мертвий", падає=True)])

    assert summary.exit_code == 0, (
        "один недоступний магазин не робить прогін провальним — "
        "інакше systemd світить червоним щодня, і на це перестають дивитись"
    )


def test_код_виходу_одиниця_коли_не_вийшло_нічого() -> None:
    summary = прогнати(
        [ФейковийПарсер("мертвий", падає=True), ФейковийПарсер("порожній", товарів=0)]
    )

    assert summary.exit_code == 1


def test_підсумок_називає_мовчунів_поіменно() -> None:
    summary = прогнати([ФейковийПарсер("живий"), ФейковийПарсер("мертвий", падає=True)])
    текст = summary.render()

    assert "Нічого не дали: мертвий" in текст
    assert "прогін не провалено" in текст


def test_порожній_список_парсерів_це_помилка_виклику() -> None:
    with pytest.raises(ValueError):
        ParserRunner([])
