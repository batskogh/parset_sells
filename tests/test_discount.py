"""Тести детектора знижки.

Ряди тут вигадані навмисно: у реальній історії немає потрібних крайніх
випадків — довгої акції, різкого викиду, повільного сповзання ціни. Кожен ряд
описує ситуацію, у якій система має або промовчати, або сповістити.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.discount import DiscountRules, find_discount, price_mode

RULES = DiscountRules(min_drop_percent=Decimal("10"), min_samples=5, bucket_percent=Decimal("1"))


def d(*values: str | int) -> list[Decimal]:
    return [Decimal(str(v)) for v in values]


# ----------------------------------------------------------------------- мода


def test_мода_це_найчастіша_ціна() -> None:
    mode = price_mode(d(100, 100, 100, 120, 90))
    assert mode is not None
    assert mode.value == Decimal("100")
    assert mode.hits == 3
    assert mode.samples == 5


def test_мода_не_вироджується_на_майже_однакових_цінах() -> None:
    """Головна пастка: без кошиків кожна ціна унікальна й мода безглузда."""
    prices = d("99.90", "100.10", "100.00", "99.95", "100.05", "80.00")
    mode = price_mode(prices, bucket_percent=Decimal("1"))
    assert mode is not None
    assert mode.hits == 5                     # усі п'ять «сотень» злились в один кошик
    assert Decimal("99.5") <= mode.value <= Decimal("100.5")


def test_мода_не_зсувається_разовим_викидом() -> None:
    """Один дешевий день не робить дешеву ціну звичною — на відміну від мінімуму."""
    prices = d(500, 500, 500, 500, 500, 250)
    mode = price_mode(prices)
    assert mode is not None
    assert mode.value == Decimal("500")


def test_мода_стійкіша_за_середнє() -> None:
    """Середнє поповзло б за викидом, мода лишається на звичній ціні."""
    prices = d(200, 200, 200, 200, 1000)
    mode = price_mode(prices)
    average = sum(prices) / len(prices)
    assert mode is not None
    assert mode.value == Decimal("200")
    assert average == Decimal("360")


def test_мода_працює_на_дорогому_товарі() -> None:
    """Кошик у відсотках, а не в гривнях: 39 199 і 39 250 — та сама ціна."""
    prices = d(39199, 39250, 39199, 39100, 35000)
    mode = price_mode(prices, bucket_percent=Decimal("1"))
    assert mode is not None
    assert Decimal("39000") <= mode.value <= Decimal("39300")


def test_мода_на_порожній_історії() -> None:
    assert price_mode([]) is None


def test_при_нічиї_береться_нижчий_кошик() -> None:
    """Обережність: нижча звична ціна означає суворіший поріг знижки."""
    mode = price_mode(d(100, 100, 200, 200))
    assert mode is not None
    assert mode.value == Decimal("100")


# -------------------------------------------------------------------- знижка


def test_знаходить_справжнє_падіння() -> None:
    signal = find_discount(d(100, 100, 100, 100, 100, 80), rules=RULES)
    assert signal is not None
    assert signal.mode == Decimal("100.00")
    assert signal.current == Decimal("80.00")
    assert signal.drop_percent == Decimal("20.00")
    assert signal.saving == Decimal("20.00")


def test_мовчить_коли_падіння_замале() -> None:
    """95 проти звичних 100 — це 5%, не привід будити людину."""
    assert find_discount(d(100, 100, 100, 100, 100, 95), rules=RULES) is None


def test_поріг_строгий_а_не_нестрогий() -> None:
    """Рівно 10% — ще не знижка: умова сформульована як «більш ніж»."""
    assert find_discount(d(100, 100, 100, 100, 100, 90), rules=RULES) is None
    assert find_discount(d(100, 100, 100, 100, 100, "89.99"), rules=RULES) is not None


def test_мовчить_на_короткій_історії() -> None:
    """Чотири заміри — це ще не «звична ціна», хоч би як виглядало падіння."""
    assert find_discount(d(100, 100, 100, 50), rules=RULES) is None


def test_рівно_мінімум_замірів_уже_рахується() -> None:
    signal = find_discount(d(100, 100, 100, 100, 50), rules=RULES)
    assert signal is not None
    assert signal.samples == 5


def test_не_бачить_знижки_коли_ціна_просто_коливається() -> None:
    prices = d("100.00", "101.00", "99.50", "100.50", "99.80", "100.20")
    assert find_discount(prices, rules=RULES) is None


def test_довга_акція_стає_новою_нормою() -> None:
    """Якщо «акційна» ціна тримається більшість часу — це вже не знижка.

    Саме тому мода рахується від усієї історії: магазин, що півроку тримає
    «знижку», перестає бути цікавим.
    """
    prices = d(200, 200, 150, 150, 150, 150, 150, 150, 150)
    assert find_discount(prices, rules=RULES) is None


def test_падіння_нижче_довгої_акції_помічається() -> None:
    prices = d(200, 200, 150, 150, 150, 150, 150, 150, 120)
    signal = find_discount(prices, rules=RULES)
    assert signal is not None
    assert signal.mode == Decimal("150.00")
    assert signal.drop_percent == Decimal("20.00")


def test_old_price_магазину_ні_на_що_не_впливає() -> None:
    """Детектор бачить лише історію, знята з сайту «стара ціна» сюди не потрапляє."""
    prices = d(100, 100, 100, 100, 100, 100)
    assert find_discount(prices, rules=RULES) is None


def test_поточну_ціну_можна_передати_окремо() -> None:
    """Знадобиться при перевірці свіжого заміру проти вже накопиченої історії."""
    history = d(100, 100, 100, 100, 100)
    signal = find_discount(history, current=Decimal("70"), rules=RULES)
    assert signal is not None
    assert signal.current == Decimal("70.00")
    assert signal.drop_percent == Decimal("30.00")


def test_зростання_ціни_не_знижка() -> None:
    assert find_discount(d(100, 100, 100, 100, 100, 130), rules=RULES) is None


def test_пороги_беруться_з_правил() -> None:
    prices = d(100, 100, 100, 100, 100, 93)
    assert find_discount(prices, rules=RULES) is None
    мякший_поріг = DiscountRules(min_drop_percent=Decimal("5"), min_samples=5)
    assert find_discount(prices, rules=мякший_поріг) is not None


@pytest.mark.parametrize("history", [[], [Decimal("100")]])
def test_порожня_і_однозамірна_історія(history: list[Decimal]) -> None:
    assert find_discount(history, rules=RULES) is None
