"""Здоров'я магазинів: хто перестав читатися і коли ожив.

Магазин ламається тихо. Сайт міняє розмітку, ставить антибот-перевірку або
просто перейменовує сторінку — парсер щоразу чесно повертає нуль товарів,
прогін іде далі, решта магазинів збирається, і в журналі systemd усе зелене.
Людина дізнається про це через тиждень, коли помітить, що ціни в списку
підозріло рівні.

Тому мовчання тут — окрема подія, про яку говорять вголос. Питання «коли цей
магазин востаннє справді читався» має точну відповідь у журналі прогонів
(`shop_runs`), а не в побічних ознаках: час останньої ціни перестає
оновлюватись, щойно ввімкнути `STORE_PRICE_ONLY_ON_CHANGE`, а час оновлення
картки не рухається, поки в ній нічого не змінилось. Обидва мовчали б
однаково і про здоровий магазин зі стабільною ціною, і про мертвий.

Рішення тут — чисті функції над зліпком стану: їх видно всі одразу й можна
перевіряти тестами без бази.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.runner import RunSummary
from app.db import repository as repo

logger = logging.getLogger(__name__)

SILENT = "silent"
RECOVERED = "recovered"


@dataclass(frozen=True, slots=True)
class HealthRules:
    """Скільки терпіти мовчання і як часто про нього нагадувати."""

    silent_days: int = 2
    repeat_hours: int = 24

    @classmethod
    def from_settings(cls) -> "HealthRules":
        settings = get_settings()
        return cls(
            silent_days=settings.health_silent_days,
            repeat_hours=settings.health_repeat_hours,
        )


@dataclass(frozen=True, slots=True)
class ShopState:
    """Зліпок стану магазину на момент перевірки."""

    shop_id: int
    slug: str
    title: str
    products: int
    known_since: datetime
    last_success: datetime | None
    alerted_at: datetime | None
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class HealthVerdict:
    """Що саме сталося з магазином і чи варто про це казати."""

    shop: ShopState
    kind: str
    silent_days: int = 0

    @property
    def silent(self) -> bool:
        return self.kind == SILENT


def _aware(moment: datetime) -> datetime:
    """Зі SQLite час приходить «наївним» — доводимо до UTC."""
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def check_shops(
    states: Iterable[ShopState],
    *,
    rules: HealthRules | None = None,
    now: datetime | None = None,
) -> list[HealthVerdict]:
    """Кому з магазинів зараз погано, а хто щойно повернувся до життя.

    Магазини без активних товарів пропускаються: стежити там нема за чим, і
    мовчання порожнього магазину — не новина, а норма.
    """
    rules = rules or HealthRules.from_settings()
    now = now or datetime.now(timezone.utc)
    поріг = timedelta(days=rules.silent_days)

    verdicts: list[HealthVerdict] = []
    for state in states:
        if state.products <= 0:
            continue

        # магазин, який жодного разу нічого не дав, рахується від дня появи:
        # інакше про поламаний з першого дня не сказали б ніколи
        остання_вдача = _aware(state.last_success) if state.last_success else None
        відлік = остання_вдача or _aware(state.known_since)
        мовчить = now - відлік
        днів = мовчить.days

        if мовчить < поріг:
            # ожив — але сказати про це варто лише тому, кого раніше тривожили
            if state.alerted_at is not None:
                verdicts.append(HealthVerdict(shop=state, kind=RECOVERED))
            continue

        if state.alerted_at is None:
            verdicts.append(HealthVerdict(shop=state, kind=SILENT, silent_days=днів))
            continue

        # мовчить далі: нагадуємо, але не частіше, ніж домовлено
        від_останнього = now - _aware(state.alerted_at)
        if від_останнього >= timedelta(hours=rules.repeat_hours):
            verdicts.append(HealthVerdict(shop=state, kind=SILENT, silent_days=днів))

    return verdicts


async def collect_states(session: AsyncSession) -> list[ShopState]:
    """Складає зліпок стану всіх активних магазинів."""
    shops = await repo.list_shops(session, only_active=True)
    successes = await repo.last_successful_runs(session)
    runs = await repo.last_runs(session)
    counts = await repo.active_product_counts(session)

    states = []
    for shop in shops:
        last_run = runs.get(shop.slug)
        states.append(
            ShopState(
                shop_id=shop.id,
                slug=shop.slug,
                title=shop.title or shop.slug,
                products=counts.get(shop.id, 0),
                known_since=shop.created_at,
                last_success=successes.get(shop.slug),
                alerted_at=shop.silence_alert_at,
                last_error=last_run.error_text if last_run else None,
            )
        )
    return states


async def mark_told(
    session: AsyncSession, verdict: HealthVerdict, *, now: datetime | None = None
) -> None:
    """Запам'ятовує, що про магазин уже сказали (або що він одужав)."""
    at = None if verdict.kind == RECOVERED else (now or datetime.now(timezone.utc))
    await repo.mark_shop_silence_alert(session, verdict.shop.shop_id, at=at)


async def record_runs(
    session: AsyncSession,
    summary: RunSummary,
    *,
    failed_to_build: Sequence[tuple[str, str]] = (),
    finished_at: datetime | None = None,
) -> None:
    """Записує підсумок прогону — по рядку на магазин.

    Магазини, які не вдалося навіть підготувати, теж потрапляють у журнал:
    для стеження за мовчанням різниці немає, чому саме магазин не читався.
    """
    for result in summary.results:
        await repo.add_shop_run(
            session,
            shop_slug=result.shop_slug,
            items=len(result.items),
            errors=len(result.errors),
            error_text=result.errors[0] if result.errors else None,
            duration=result.duration,
            finished_at=finished_at,
        )

    for slug, причина in failed_to_build:
        await repo.add_shop_run(
            session,
            shop_slug=slug,
            items=0,
            errors=1,
            error_text=причина,
            finished_at=finished_at,
        )

    logger.info(
        "Записано прогонів: %s", len(summary.results) + len(failed_to_build)
    )
