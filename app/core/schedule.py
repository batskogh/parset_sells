"""Розклад прогонів для запуску в Docker.

На голому сервері розклад ведуть таймери systemd (`deploy/systemd/`). У
контейнері systemd немає, а cron там незручний: не бачить змінних оточення,
пише логи в нікуди й не вміє наздоганяти пропущене. Тому розклад тут свій —
але робить рівно те саме, що таймери:

* **ті самі години** — обхід 00/06/12/18, розсилка 07/13/19, бекап 03:00;
* **розкид часу** (`RandomizedDelaySec`) — рівний ритм секунда в секунду
  виглядає як робот;
* **наздоганяння пропущеного** (`Persistent=true`) — якщо контейнер лежав у
  момент запуску, прогін іде одразу після старту.

Колись ми відкинули «власний цикл» з аргументом «хто підніме процес, коли він
впаде». У Docker відповідь є: `restart: unless-stopped`. Цикл тут лише
вирішує, *коли* запускати; самі прогони йдуть окремими процесами, тож падіння
парсера не валить планувальник.

Час — місцевий час контейнера (`TZ` у compose). Тут чисті функції; цикл,
процеси й стан — у `run_scheduler.py`.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.config import get_settings


@dataclass(frozen=True, slots=True)
class Job:
    """Одна регулярна робота: що запускати й о котрій."""

    name: str
    command: tuple[str, ...]
    hours: tuple[int, ...]
    jitter_minutes: int = 0
    timeout_minutes: int = 30


def parse_hours(raw: str) -> tuple[int, ...]:
    """«0,6,12,18» → (0, 6, 12, 18). Сміття й години поза добою відкидаються."""
    hours = set()
    for part in raw.replace(" ", "").split(","):
        if part.isdigit() and 0 <= int(part) <= 23:
            hours.add(int(part))
    return tuple(sorted(hours))


def default_jobs() -> list[Job]:
    """Ті самі роботи й години, що й у таймерах systemd."""
    settings = get_settings()
    return [
        Job(
            name="parse",
            command=("run_parser.py", "--all"),
            hours=parse_hours(settings.schedule_parse_hours),
            jitter_minutes=20,
            timeout_minutes=30,
        ),
        Job(
            name="notify",
            command=("run_notify.py", "--send"),
            hours=parse_hours(settings.schedule_notify_hours),
            jitter_minutes=10,
            timeout_minutes=15,
        ),
        Job(
            name="backup",
            command=("deploy/backup_db.py",),
            hours=parse_hours(settings.schedule_backup_hours),
            jitter_minutes=0,
            timeout_minutes=5,
        ),
    ]


def previous_slot(hours: Sequence[int], now: datetime) -> datetime | None:
    """Останній плановий момент, що вже настав (не пізніше `now`)."""
    if not hours:
        return None
    for days_back in range(0, 2):
        day = (now - timedelta(days=days_back)).replace(minute=0, second=0, microsecond=0)
        for hour in sorted(hours, reverse=True):
            slot = day.replace(hour=hour)
            if slot <= now:
                return slot
    return None


def next_slot(hours: Sequence[int], now: datetime) -> datetime | None:
    """Найближчий плановий момент строго після `now`, без розкиду."""
    if not hours:
        return None
    for days_ahead in range(0, 2):
        day = (now + timedelta(days=days_ahead)).replace(minute=0, second=0, microsecond=0)
        for hour in sorted(hours):
            slot = day.replace(hour=hour)
            if slot > now:
                return slot
    return None


def next_run(job: Job, now: datetime, rng: random.Random | None = None) -> datetime | None:
    """Коли запускати наступного разу — з розкидом, як `RandomizedDelaySec`."""
    slot = next_slot(job.hours, now)
    if slot is None:
        return None
    rng = rng or random.Random()
    return slot + timedelta(seconds=rng.uniform(0, job.jitter_minutes * 60))


def missed(job: Job, last_started: datetime | None, now: datetime) -> bool:
    """Чи пропущено плановий запуск, поки планувальник не працював.

    Пропущеним вважається останній момент, що вже настав, якщо після нього
    робота не стартувала. Про перший старт (стану ще немає) відповідь «так»:
    на свіжому розгортанні обхід одразу наповнить базу, а не чекатиме до
    наступного кола.
    """
    slot = previous_slot(job.hours, now)
    if slot is None:
        return False
    return last_started is None or last_started < slot
