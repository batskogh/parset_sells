"""Часові зрізи історії цін.

Історія в базі нерівномірна: товари парсяться в різні моменти, якийсь день
магазин міг бути недоступний, новий товар з'являється пізніше за інші, а
обхід іде кілька разів на добу. Через це сирі заміри не годяться для моди:
вона почала б описувати розклад парсингу, а не ціни. Для одного товару це
виправляє `daily_values`, для набору товарів (кошик, асортимент, індекс
магазину) — `basket_series`: інакше сума стрибала б від того, який товар
парсився останнім.

Тому перед складанням історія зводиться до добових зрізів: на кожну добу
береться остання відома ціна кожного товару й переноситься вперед, доки не
з'явиться новіша. Це і є «остання відома ціна на дату».

Друга пастка — поява товару. Якщо почати рахувати суму з дня, коли частина
товарів ще не має жодної ціни, набір «подорожчає» в момент, коли товар
додали, — і це виглядатиме як зростання цін, хоча ціни не рухались. Тому
сумарний ряд починається з дати, коли **всі** товари набору вже відомі.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal

# один замір: коли і почім
PricePoint = tuple[date, Decimal]


def daily_last(points: Iterable[PricePoint]) -> dict[date, Decimal]:
    """Один зріз на добу: якщо за день замірів кілька, лишається останній."""
    series: dict[date, Decimal] = {}
    for day, price in sorted(points, key=lambda item: item[0]):
        series[day] = price
    return series


def daily_values(points: Iterable[tuple[datetime | date, Decimal]]) -> list[Decimal]:
    """Історія одного товару по добах, найстаріші ціни перші.

    Потрібна там, де рахується мода. Мода відповідає на питання «яка ціна
    у цього товару звична», і відповідь не має залежати від того, скільки
    разів на добу ми зазирнули в магазин: при чотирьох обходах замість
    одного та сама незмінна ціна дала б учетверо більше замірів, поріг
    `min_samples` набрався б за півдоби, і бот оголосив би звичною ціну,
    яку бачив один-єдиний день.

    Тому на кожну добу лишається остання відома ціна, а `min_samples`
    так і лишається кількістю **днів спостережень**.
    """
    # сортуємо за повним моментом, а не за добою: інакше порядок замірів
    # усередині дня залежав би від того, як їх дістали з бази, і «остання
    # ціна доби» могла б виявитись першою
    series: dict[date, Decimal] = {}
    for moment, price in sorted(points, key=lambda item: item[0]):
        day = moment.date() if isinstance(moment, datetime) else moment
        series[day] = price
    return [series[day] for day in sorted(series)]


def days_between(start: date, end: date) -> list[date]:
    """Усі доби відрізка включно з краями."""
    if end < start:
        return []
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def fill_forward(series: Mapping[date, Decimal], days: Sequence[date]) -> dict[date, Decimal]:
    """Переносить останню відому ціну вперед по добах.

    Доби до першого заміру лишаються порожніми: ціни тоді ще не знали, і
    вигадувати її назад не можна.
    """
    filled: dict[date, Decimal] = {}
    last: Decimal | None = None
    for day in days:
        if day in series:
            last = series[day]
        if last is not None:
            filled[day] = last
    return filled


def basket_series(
    histories: Mapping[int, Iterable[PricePoint]],
    *,
    require_all: bool = True,
) -> list[tuple[date, Decimal]]:
    """Вартість набору товарів на кожну добу.

    `histories` — заміри кожного товару, ключ довільний (зазвичай id товару).

    За `require_all` ряд починається з дати, коли відомі ціни **всіх** товарів
    набору: інакше поява товару в базі виглядала б як подорожчання набору.
    Якщо вимкнути, сума рахується з усього, що відоме на дату — тоді ряд довший,
    але його початок непорівнянний з кінцем.
    """
    dailies = {key: daily_last(points) for key, points in histories.items()}
    dailies = {key: series for key, series in dailies.items() if series}
    if not dailies:
        return []

    first_days = [min(series) for series in dailies.values()]
    last_days = [max(series) for series in dailies.values()]
    start = max(first_days) if require_all else min(first_days)
    end = max(last_days)

    days = days_between(start, end)
    filled = {key: fill_forward(series, days) for key, series in dailies.items()}

    totals: list[tuple[date, Decimal]] = []
    for day in days:
        known = [prices[day] for prices in filled.values() if day in prices]
        if not known or (require_all and len(known) != len(filled)):
            continue
        totals.append((day, sum(known, Decimal("0"))))
    return totals
