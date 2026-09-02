"""Тихі години: коли сповіщення не надсилаються.

Обхід магазинів іде цілодобово — сайтам байдуже, котра година. Людям не
байдуже. Знижка, знайдена о третій ночі, о восьмій ранку нікуди не подінеться:
вона триває днями, а прогін іде кожні шість годин. Тож нічне сповіщення нічого
не рятує, зате будить.

Тому вночі просто нічого не надсилаємо — і нічого не запам'ятовуємо. Журнал
не оновлюється, знижка лишається невідправленою, і найближчий денний прогін
надішле її як звичайну новину. Ніякої черги відкладених повідомлень тут немає
навмисно: черга — це стан, який треба зберігати, чистити й пояснювати, а вона
не дає нічого, чого не дає простий повтор через кілька годин.

Години рахуються за **місцевим часом машини**. Це варте окремої уваги при
розгортанні: типовий контейнер Linux стоїть у UTC, і для Києва «22:00» у
такому контейнері настане о першій ночі.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time

from app.config import get_settings


@dataclass(frozen=True, slots=True)
class QuietHours:
    """Проміжок годин, коли бот мовчить. Рахується за місцевим часом."""

    start: int
    end: int

    @classmethod
    def from_settings(cls) -> "QuietHours":
        settings = get_settings()
        return cls(start=settings.notify_quiet_from, end=settings.notify_quiet_to)

    @property
    def enabled(self) -> bool:
        """Однакові межі означають «тихих годин немає»."""
        return self.start != self.end

    def contains(self, moment: datetime | None = None) -> bool:
        """Чи потрапляє момент у тихі години."""
        if not self.enabled:
            return False

        година = _local_hour(moment)
        if self.start < self.end:               # звичайний проміжок: 1..6
            return self.start <= година < self.end
        return година >= self.start or година < self.end     # через північ: 22..8

    def next_allowed(self, moment: datetime | None = None) -> time:
        """Коли знову можна писати — для повідомлення в консолі."""
        return time(hour=self.end % 24)

    def describe(self) -> str:
        return f"{self.start:02d}:00–{self.end:02d}:00"


def _local_hour(moment: datetime | None) -> int:
    """Година за місцевим часом машини.

    Момент із поясом переводиться в місцевий; без поясу вважається місцевим —
    саме такий приходить із `datetime.now()`.
    """
    moment = moment or datetime.now()
    if moment.tzinfo is not None:
        moment = moment.astimezone()
    return moment.hour
