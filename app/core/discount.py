"""Детектор реальної знижки.

«Реальна» знижка в цьому проєкті — не те, що намалював магазин біля цінника.
`old_price` із сайту не доказ: його накручують, і перекреслена ціна може
роками стояти вища за справжню. Тому знижка визначається від **моди** всієї
історії ціни товару: ціна впала нижче звичної більш ніж на заданий відсоток.

Чому саме мода:
  * мінімум ловить разовий викид і оголошує його нормою;
  * середнє розмивається довгими акціями й повзе за ними;
  * мода показує ціну, за якою товар стоїть **більшість часу**.

Пастка моди на цінах: числа майже неперервні, і якщо рахувати «в лоб», кожне
значення унікальне — мода вироджується в перше-ліпше. Тому ціни спершу
групуються в кошики, ширина яких задається у відсотках від типової ціни
товару (`DISCOUNT_BUCKET_PERCENT`). Відсоток, а не гривні, бо історія може
бути і про чипси за 74 ₴, і про відеокарту за 39 199 ₴.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from app.config import get_settings

CENT = Decimal("0.01")


def to_cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class DiscountRules:
    """Пороги, за якими падіння ціни вважається знижкою."""

    min_drop_percent: Decimal = Decimal("10")
    min_samples: int = 5
    bucket_percent: Decimal = Decimal("1")

    @classmethod
    def from_settings(cls) -> "DiscountRules":
        settings = get_settings()
        return cls(
            min_drop_percent=Decimal(str(settings.discount_min_drop_percent)),
            min_samples=settings.discount_min_samples,
            bucket_percent=Decimal(str(settings.discount_bucket_percent)),
        )


@dataclass(frozen=True, slots=True)
class PriceMode:
    """Звична ціна товару та її вага в історії."""

    value: Decimal          # сама звична ціна
    hits: int               # скільки замірів потрапило в її кошик
    samples: int            # скільки замірів усього

    @property
    def share(self) -> Decimal:
        """Яку частку історії займає звична ціна — від 0 до 1."""
        return to_cents(Decimal(self.hits) / Decimal(self.samples)) if self.samples else Decimal("0")


@dataclass(frozen=True, slots=True)
class DiscountSignal:
    """Знайдена знижка: на скільки нинішня ціна нижча за звичну."""

    current: Decimal
    mode: Decimal
    drop_percent: Decimal
    samples: int

    @property
    def saving(self) -> Decimal:
        return to_cents(self.mode - self.current)


def _median(values: Sequence[Decimal]) -> Decimal:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return to_cents((ordered[middle - 1] + ordered[middle]) / 2)


def bucket_step(prices: Sequence[Decimal], bucket_percent: Decimal) -> Decimal:
    """Ширина кошика для групування цін.

    Рахується один раз на всю історію — від медіани, а не від кожної ціни
    окремо: інакше сусідні заміри потрапляли б у кошики різної ширини й
    групування залежало б від порядку.
    """
    if not prices:
        return CENT
    step = _median(prices) * bucket_percent / Decimal("100")
    return max(to_cents(step), CENT)


def price_mode(prices: Sequence[Decimal], *, bucket_percent: Decimal = Decimal("1")) -> PriceMode | None:
    """Звична ціна: центр найнаселенішого кошика історії.

    Повертає None на порожній історії. Кількість замірів тут не перевіряється —
    це робота `find_discount`, бо мода сама по собі корисна і на короткій історії.
    """
    values = [p for p in prices if p is not None]
    if not values:
        return None

    step = bucket_step(values, bucket_percent)
    buckets: dict[int, list[Decimal]] = {}
    for price in values:
        key = int((price / step).to_integral_value(rounding=ROUND_HALF_UP))
        buckets.setdefault(key, []).append(price)

    counts = Counter({key: len(group) for key, group in buckets.items()})
    top = max(counts.values())
    # при нічиїй беремо нижчий кошик: звична ціна тоді нижча, поріг знижки
    # суворіший, і система радше промовчить, ніж збреше про знижку
    winner = min(key for key, count in counts.items() if count == top)

    return PriceMode(
        value=_median(buckets[winner]),
        hits=top,
        samples=len(values),
    )


def find_discount(
    history: Sequence[Decimal],
    *,
    current: Decimal | None = None,
    rules: DiscountRules | None = None,
) -> DiscountSignal | None:
    """Шукає реальну знижку в історії цін одного товару.

    `history` — ціни в хронологічному порядку (найстаріша перша). Якщо
    `current` не задано, поточною вважається остання ціна історії.

    Повертає None, коли знижки немає або історія ще закоротка, щоб про щось
    судити: на трьох замірах «звична ціна» — це вигадка.
    """
    rules = rules or DiscountRules()
    values = [p for p in history if p is not None]
    if len(values) < rules.min_samples:
        return None

    price_now = current if current is not None else values[-1]
    mode = price_mode(values, bucket_percent=rules.bucket_percent)
    if mode is None or mode.value <= 0:
        return None

    drop = (mode.value - price_now) / mode.value * Decimal("100")
    if drop <= rules.min_drop_percent:
        return None

    return DiscountSignal(
        current=to_cents(price_now),
        mode=to_cents(mode.value),
        drop_percent=to_cents(drop),
        samples=mode.samples,
    )
