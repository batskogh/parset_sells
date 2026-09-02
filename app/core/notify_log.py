"""Рішення, чи слати сповіщення — і чи не казали цього вже.

Знижка триває днями, а перевірка ціни йде щодня. Якщо просто слати все, що
знайшов детектор, одне падіння перетвориться на щоденне нагадування, і бота
вимкнуть — не тому, що він помиляється, а тому що набридає.

Тому «та сама знижка» визначена як **епізод**, а не як окремий замір. Епізод
триває, доки ціна не піднялась назад до звичної; про нього говорять один раз.
З цього правила є два свідомі винятки:

* **знижка поглибшала** — було −15%, стало −40%: це вже інша новина;
* **ціна відновилась і впала знову** — почався новий епізод.

І один запобіжник поверх усього: пауза після останнього сповіщення. Без неї
ціна, що стрибає туди-сюди між знижкою і нормою кілька разів на день,
породжувала б «новий епізод» на кожному стрибку.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.config import get_settings


@dataclass(frozen=True, slots=True)
class NotifyRules:
    """Пороги, що стримують повторні сповіщення."""

    cooldown_hours: int = 24
    deeper_by_percent: Decimal = Decimal("10")

    @classmethod
    def from_settings(cls) -> "NotifyRules":
        settings = get_settings()
        return cls(
            cooldown_hours=settings.notify_cooldown_hours,
            deeper_by_percent=Decimal(str(settings.notify_deeper_percent)),
        )


@dataclass(frozen=True, slots=True)
class LastNotification:
    """Що саме вже казали цій людині про цей товар."""

    price: Decimal
    sent_at: datetime


@dataclass(frozen=True, slots=True)
class NotifyDecision:
    """Рішення разом із причиною — причина йде в лог і пояснює поведінку."""

    send: bool
    reason: str

    def __bool__(self) -> bool:
        return self.send


def should_notify(
    *,
    current: Decimal,
    last: LastNotification | None,
    prices_since_last: Sequence[Decimal] = (),
    recovery_price: Decimal,
    rules: NotifyRules | None = None,
    now: datetime | None = None,
) -> NotifyDecision:
    """Чи казати про цю знижку зараз.

    `recovery_price` — ціна, вище якої знижка вважається закінченою (звичайно
    це мода мінус поріг). `prices_since_last` — заміри після останнього
    сповіщення; якщо серед них є ціна вище `recovery_price`, епізод
    завершився, і наступне падіння вже нова новина.
    """
    rules = rules or NotifyRules()
    now = now or datetime.now(timezone.utc)

    if last is None:
        return NotifyDecision(True, "перша знижка на цей товар")

    sent_at = last.sent_at
    if sent_at.tzinfo is None:                     # зі SQLite час може прийти «наївним»
        sent_at = sent_at.replace(tzinfo=timezone.utc)

    if now - sent_at < timedelta(hours=rules.cooldown_hours):
        return NotifyDecision(False, "щойно сповіщали — пауза ще не минула")

    if any(price > recovery_price for price in prices_since_last):
        return NotifyDecision(True, "ціна встигла відновитись — це вже нова знижка")

    if last.price > 0:
        deeper = (last.price - current) / last.price * Decimal("100")
        if deeper >= rules.deeper_by_percent:
            return NotifyDecision(True, f"знижка поглибшала ще на {deeper:.2f}%")

    return NotifyDecision(False, "про цю знижку вже казали")
