"""Оркестратор: паралельно по сайтах, послідовно всередині сайту."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.config import get_settings
from app.core.storage import SaveStats, save_result
from app.parsers.base import BaseParser
from app.parsers.models import ParseResult

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RunSummary:
    results: list[ParseResult] = field(default_factory=list)
    stats: list[SaveStats] = field(default_factory=list)
    duration: float = 0.0

    @property
    def total_items(self) -> int:
        return sum(len(r.items) for r in self.results)

    @property
    def total_errors(self) -> int:
        return sum(len(r.errors) for r in self.results)

    def render(self) -> str:
        lines = [
            f"Прогін завершено за {self.duration:.1f}с: "
            f"сайтів {len(self.results)}, товарів {self.total_items}, "
            f"помилок {self.total_errors}"
        ]
        for result in self.results:
            mark = "OK " if result.ok else "ERR"
            lines.append(
                f"  [{mark}] {result.shop_slug}: товарів {len(result.items)}, "
                f"сторінок {result.pages_visited}, {result.duration:.1f}с"
            )
            for error in result.errors[:5]:
                lines.append(f"        ! {error}")
        for stat in self.stats:
            lines.append(f"  БД  {stat}")
        return "\n".join(lines)


class ParserRunner:
    """Запускає набір парсерів.

    * різні сайти йдуть **паралельно** (обмежено семафором);
    * усередині сайту запити суворо **послідовні** (це гарантує сам BaseParser);
    * запис у SQLite серіалізовано окремим локом — щоб не ловити `database is locked`.
    """

    def __init__(
        self,
        parsers: Sequence[BaseParser],
        *,
        max_concurrency: int | None = None,
        dry_run: bool = False,
        only_if_changed: bool | None = None,
    ) -> None:
        if not parsers:
            raise ValueError("Список парсерів порожній")
        settings = get_settings()
        self.parsers = list(parsers)
        self.max_concurrency = max_concurrency or settings.max_concurrent_sites
        self.dry_run = dry_run
        self.only_if_changed = only_if_changed
        self._semaphore = asyncio.Semaphore(max(1, self.max_concurrency))
        self._db_lock = asyncio.Lock()

    async def _run_one(self, parser: BaseParser) -> tuple[ParseResult, SaveStats | None]:
        async with self._semaphore:
            try:
                async with parser:
                    result = await parser.run()
            except Exception as exc:  # noqa: BLE001 — збій сайту не валить прогін
                logger.exception("Парсер %s не запустився", parser.name)
                result = ParseResult(shop_slug=parser.name, errors=[str(exc)])
                return result, None

        if self.dry_run or not result.items:
            return result, None

        async with self._db_lock:
            stats = await save_result(parser, result, only_if_changed=self.only_if_changed)
        return result, stats

    async def run(self) -> RunSummary:
        started = time.perf_counter()
        logger.info(
            "Запуск %s парсер(ів), паралельність %s%s",
            len(self.parsers),
            self.max_concurrency,
            " (dry-run, без запису в БД)" if self.dry_run else "",
        )

        pairs = await asyncio.gather(*(self._run_one(p) for p in self.parsers))

        summary = RunSummary(duration=time.perf_counter() - started)
        for result, stats in pairs:
            summary.results.append(result)
            if stats is not None:
                summary.stats.append(stats)
        return summary
