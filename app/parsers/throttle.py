"""Керування затримками між запитами."""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass


@dataclass(slots=True)
class DelayPolicy:
    """Політика затримок для одного сайту.

    min_delay/max_delay — випадкова пауза між запитами (щоб не бити рівним ритмом).
    Якщо min == max, пауза фіксована. 0/0 — без затримок.
    """

    min_delay: float = 1.0
    max_delay: float = 3.0
    # додаткова пауза між сторінками каталогу (0 = використати звичайну)
    page_delay: float | None = None

    def __post_init__(self) -> None:
        if self.min_delay < 0 or self.max_delay < 0:
            raise ValueError("Затримки не можуть бути від'ємними")
        if self.max_delay < self.min_delay:
            self.max_delay = self.min_delay

    def next_delay(self) -> float:
        if self.max_delay <= 0:
            return 0.0
        if self.max_delay == self.min_delay:
            return self.min_delay
        return random.uniform(self.min_delay, self.max_delay)

    async def wait(self) -> float:
        """Пауза між звичайними запитами."""
        delay = self.next_delay()
        if delay > 0:
            await asyncio.sleep(delay)
        return delay

    async def wait_page(self) -> float:
        """Пауза між сторінками каталогу."""
        delay = self.page_delay if self.page_delay is not None else self.next_delay()
        if delay > 0:
            await asyncio.sleep(delay)
        return delay
