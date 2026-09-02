"""Мода й знижка на рівнях, вищих за окремий товар.

Три різні питання, які легко сплутати:

* **кошик** — «мій набір коштує зазвичай стільки, а зараз стільки»: мода
  рахується від *суми* набору в часі, тобто це та сама знижка, що й для
  товару, тільки товаром виступає весь набір;
* **асортимент** — «яка ціна типова для товарів цього магазину» і які товари
  помітно дешевші за неї;
* **індекс магазину** — «чи магазин загалом подорожчав»: сумарна вартість
  асортименту в часі проти своєї звичної.

Перший і третій живуть у часі, тому спираються на добові зрізи з `timeline`.
Другий дивиться на зріз *зараз* і порівнює товари між собою.

Межа сенсу асортиментної моди (навмисно записана тут, бо про неї легко
забути): вона змішує непорівнюване. Мода цін магазину, де є і чипси за 74 ₴,
і відеокарта за 39 199 ₴, скаже лише те, що чипси дешеві, — і казатиме це
щодня, незалежно від знижок. Корисна вона там, де асортимент однорідний
(один тип товару), або як груба відповідь на питання «до якої цінової
категорії належить товар». Для «чи це вигідно» призначені мода товару й
мода товарної групи, а не ця.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.core.discount import (
    DiscountRules,
    DiscountSignal,
    find_discount,
    price_mode,
    to_cents,
)
from app.core.timeline import PricePoint, basket_series


# ------------------------------------------------------------------- кошик


@dataclass(frozen=True, slots=True)
class BasketReport:
    """Вартість набору товарів: звична, поточна і знижка, якщо вона є."""

    items: int
    days: int
    current: Decimal
    mode: Decimal
    signal: DiscountSignal | None

    @property
    def has_discount(self) -> bool:
        return self.signal is not None

    def render(self) -> str:
        head = (
            f"Кошик із {self.items} товар(ів): зараз {self.current}, "
            f"звично {self.mode} ({self.days} діб історії)"
        )
        if self.signal is None:
            return head
        return f"{head} — дешевше на {self.signal.drop_percent}%"


def basket_discount(
    histories: Mapping[object, Iterable[PricePoint]],
    *,
    rules: DiscountRules | None = None,
    require_all: bool = True,
) -> BasketReport | None:
    """Знижка на набір товарів як на одне ціле.

    Набір оцінюється сумою: якщо один товар подорожчав, а другий подешевшав
    більше, набір загалом вигідніший — саме це й цікавить при закупівлі.
    """
    rules = rules or DiscountRules()
    # історії можуть прийти генераторами, а читаються двічі — матеріалізуємо
    known = {key: list(points) for key, points in histories.items()}
    totals = basket_series(known, require_all=require_all)
    if not totals:
        return None

    values = [total for _, total in totals]
    mode = price_mode(values, bucket_percent=rules.bucket_percent)
    if mode is None:
        return None

    return BasketReport(
        items=len([key for key, points in known.items() if points]),
        days=len(totals),
        current=to_cents(values[-1]),
        mode=to_cents(mode.value),
        signal=find_discount(values, rules=rules),
    )


# -------------------------------------------------------------- асортимент


@dataclass(frozen=True, slots=True)
class CheaperThanTypical:
    """Товар, помітно дешевший за типову ціну магазину."""

    key: object
    price: Decimal
    drop_percent: Decimal


@dataclass(frozen=True, slots=True)
class AssortmentReport:
    """Типова ціна товару в магазині й ті, що помітно нижчі за неї."""

    mode: Decimal
    products: int
    cheaper: list[CheaperThanTypical] = field(default_factory=list)

    def render(self) -> str:
        return (
            f"Типова ціна магазину: {self.mode} ({self.products} товарів), "
            f"помітно дешевших: {len(self.cheaper)}"
        )


def assortment_report(
    prices_now: Mapping[object, Decimal],
    *,
    rules: DiscountRules | None = None,
) -> AssortmentReport | None:
    """Мода поточних цін магазину і товари, що нижчі за неї на поріг.

    Увага на межу сенсу, описану в шапці модуля: тут порівнюються різні
    товари між собою, а не товар сам із собою в часі. «Дешевший за типовий»
    не означає «подешевшав».
    """
    rules = rules or DiscountRules()
    values = [price for price in prices_now.values() if price is not None]
    if not values:
        return None

    mode = price_mode(values, bucket_percent=rules.bucket_percent)
    if mode is None or mode.value <= 0:
        return None

    cheaper: list[CheaperThanTypical] = []
    for key, price in prices_now.items():
        if price is None:
            continue
        drop = (mode.value - price) / mode.value * Decimal("100")
        if drop > rules.min_drop_percent:
            cheaper.append(CheaperThanTypical(key=key, price=to_cents(price), drop_percent=to_cents(drop)))

    cheaper.sort(key=lambda item: item.drop_percent, reverse=True)
    return AssortmentReport(mode=to_cents(mode.value), products=len(values), cheaper=cheaper)


# ---------------------------------------------------------- індекс магазину


@dataclass(frozen=True, slots=True)
class ShopIndexReport:
    """Чи магазин загалом подорожчав — фон, на якому читається знижка товару."""

    current: Decimal
    mode: Decimal
    change_percent: Decimal
    days: int
    products: int

    @property
    def direction(self) -> str:
        if self.change_percent > 0:
            return "дорожче за звичне"
        if self.change_percent < 0:
            return "дешевше за звичне"
        return "як звично"

    def render(self) -> str:
        return (
            f"Асортимент ({self.products} товарів) зараз {self.current}, "
            f"звично {self.mode} — {abs(self.change_percent)}% {self.direction}"
        )


def shop_index(
    histories: Mapping[object, Iterable[PricePoint]],
    *,
    rules: DiscountRules | None = None,
) -> ShopIndexReport | None:
    """Сумарна вартість асортименту в часі проти своєї звичної величини.

    Це контекст, а не привід сповіщати: якщо магазин підняв усі ціни на 20%,
    падіння окремого товару на 10% означає інше, ніж у спокійний час.
    """
    rules = rules or DiscountRules()
    known = {key: list(points) for key, points in histories.items()}
    totals = basket_series(known, require_all=True)
    if not totals:
        return None

    values = [total for _, total in totals]
    mode = price_mode(values, bucket_percent=rules.bucket_percent)
    if mode is None or mode.value <= 0:
        return None

    current = values[-1]
    change = (current - mode.value) / mode.value * Decimal("100")
    return ShopIndexReport(
        current=to_cents(current),
        mode=to_cents(mode.value),
        change_percent=to_cents(change),
        days=len(totals),
        products=len([key for key, points in known.items() if points]),
    )


# --------------------------------------------------------- товарна група


# «модельний» токен: суміш літер і цифр, як RTX5070, GDDR7, 9EN0116 — саме
# такі шматки назви й позначають конкретну модель, на відміну від слів
_MODEL_TOKEN = re.compile(r"[A-Za-zА-Яа-яЇїІіЄєҐґ]+\d[\w\d]*|\d+[A-Za-z]+[\w\d]*")


def model_tokens(name: str, *, min_length: int = 4) -> set[str]:
    """Токени назви, що схожі на позначення моделі.

    Груба, але прозора евристика: «Відеокарта MSI RTX5070 12GB GDDR7» дає
    {RTX5070, 12GB, GDDR7}. Слова без цифр відкидаються — вони описують тип
    товару, а не модель, і об'єднали б у групу все підряд.
    """
    found = {token.upper() for token in _MODEL_TOKEN.findall(name or "")}
    return {token for token in found if len(token) >= min_length}


@dataclass(frozen=True, slots=True)
class GroupReport:
    """Типова ціна групи схожих товарів і ті, що помітно дешевші."""

    token: str
    mode: Decimal
    members: int
    cheaper: list[CheaperThanTypical] = field(default_factory=list)

    def render(self) -> str:
        return (
            f"{self.token}: типово {self.mode} серед {self.members} товар(ів), "
            f"дешевших: {len(self.cheaper)}"
        )


def group_reports(
    names: Mapping[object, str],
    prices_now: Mapping[object, Decimal],
    *,
    rules: DiscountRules | None = None,
    min_members: int = 3,
) -> list[GroupReport]:
    """Порівнює схожі товари між собою.

    Це те, чого не бачить історія одного товару: конкретна відеокарта може
    ніколи не дешевшати, але коштувати помітно менше за інші такі самі.

    Товари вважаються схожими, якщо ділять «модельний» токен назви. Межа
    методу: він працює в межах одного магазину, де назви складені за спільним
    зразком. Зіставлення товарів **між** магазинами — окрема робота, бо там
    та сама карта зветься по-різному.
    """
    rules = rules or DiscountRules()

    groups: dict[str, list[object]] = {}
    for key, name in names.items():
        if key not in prices_now or prices_now[key] is None:
            continue
        for token in model_tokens(name):
            groups.setdefault(token, []).append(key)

    reports: list[GroupReport] = []
    for token, members in groups.items():
        if len(members) < min_members:
            continue
        prices = {key: prices_now[key] for key in members}
        report = assortment_report(prices, rules=rules)
        if report is None:
            continue
        reports.append(
            GroupReport(
                token=token,
                mode=report.mode,
                members=len(members),
                cheaper=report.cheaper,
            )
        )

    reports.sort(key=lambda item: (len(item.cheaper), item.members), reverse=True)
    return reports
