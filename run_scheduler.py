"""Планувальник прогонів для Docker: обхід, розсилка, бекап за розкладом.

На голому сервері цю роль грають таймери systemd. У контейнері їх немає, тож
тут той самий розклад іде власним циклом (чому так, а не cron, — у
`app.core.schedule`).

Кожна робота — окремий процес: падіння парсера не валить планувальник, а
кожен прогін пише власний журнал (`logs/parser.log`, `logs/notify.log`).
Роботи йдуть **по черзі**, не паралельно: розсилка ніколи не стартує
посеред обходу, бо чекає, доки він закінчиться.

Коли яка робота стартувала востаннє, пам'ятає `data/scheduler.json` — за ним
після перезапуску контейнера видно, що пропущено.

    python run_scheduler.py            # працювати постійно
    python run_scheduler.py --plan     # показати найближчі запуски й вийти
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import signal
import sys
from datetime import datetime
from pathlib import Path

from app.config import BASE_DIR, get_settings
from app.core.logs import setup_logging
from app.core.schedule import Job, default_jobs, missed, next_run, next_slot

logger = logging.getLogger("scheduler")

# найдовший сон між перевірками: якщо годинник машини стрибне (синхронізація
# часу, перехід на літній час), планувальник помітить це за хвилину
MAX_SLEEP_SECONDS = 60


def state_path() -> Path:
    return get_settings().db_path.parent / "scheduler.json"


def load_state(path: Path) -> dict[str, datetime]:
    """Коли яка робота стартувала востаннє. Зіпсований файл — як порожній."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {name: datetime.fromisoformat(value) for name, value in raw.items()}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def save_state(path: Path, state: dict[str, datetime]) -> None:
    """Пише атомарно: обірваний запис не має залишити напівфайл."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({name: moment.isoformat() for name, moment in state.items()}, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


async def run_job(job: Job, stop: asyncio.Event) -> int | None:
    """Запускає роботу окремим процесом і чекає на неї. Повертає код виходу."""
    logger.info("Старт: %s", job.name)
    started = datetime.now()
    process = await asyncio.create_subprocess_exec(
        sys.executable, *job.command, cwd=str(BASE_DIR)
    )

    waiter = asyncio.create_task(process.wait())
    stopper = asyncio.create_task(stop.wait())
    done, _ = await asyncio.wait(
        {waiter, stopper},
        timeout=job.timeout_minutes * 60,
        return_when=asyncio.FIRST_COMPLETED,
    )
    stopper.cancel()

    if waiter not in done:
        # або зупиняють контейнер, або робота зависла — в обох випадках не чекаємо
        причина = "зупинка планувальника" if stop.is_set() else f"таймаут {job.timeout_minutes} хв"
        logger.warning("%s: перериваю (%s)", job.name, причина)
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=20)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
        return None

    тривалість = (datetime.now() - started).total_seconds()
    code = process.returncode
    log = logger.info if code == 0 else logger.warning
    log("Готово: %s, код %s, %.0f с", job.name, code, тривалість)
    return code


async def main_async(jobs: list[Job]) -> int:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, AttributeError):   # Windows — лише для розробки
            pass

    path = state_path()
    state = load_state(path)
    rng = random.Random()

    async def виконати(job: Job) -> None:
        state[job.name] = datetime.now()
        save_state(path, state)
        await run_job(job, stop)

    # те, що пропущено, поки контейнер лежав, — одразу, по черзі
    for job in jobs:
        if stop.is_set():
            break
        if missed(job, state.get(job.name), datetime.now()):
            logger.info("Пропущений запуск %s — наздоганяю", job.name)
            await виконати(job)

    plan = {job.name: next_run(job, datetime.now(), rng) for job in jobs}
    for job in jobs:
        logger.info("Наступний %s: %s", job.name, f"{plan[job.name]:%Y-%m-%d %H:%M}")

    while not stop.is_set():
        now = datetime.now()
        due = [job for job in jobs if plan[job.name] is not None and plan[job.name] <= now]
        for job in due:
            if stop.is_set():
                break
            await виконати(job)
            plan[job.name] = next_run(job, datetime.now(), rng)
            logger.info("Наступний %s: %s", job.name, f"{plan[job.name]:%Y-%m-%d %H:%M}")
        if due:
            continue

        найближчий = min(moment for moment in plan.values() if moment is not None)
        чекати = max(1.0, min((найближчий - datetime.now()).total_seconds(), MAX_SLEEP_SECONDS))
        try:
            await asyncio.wait_for(stop.wait(), timeout=чекати)
        except asyncio.TimeoutError:
            pass

    logger.info("Планувальник зупинено")
    return 0


def busy_jobs(proc: Path = Path("/proc")) -> list[str]:
    """Які роботи саме зараз виконуються в цьому контейнері.

    Потрібно автооновленню: перезапуск контейнера посеред обходу обірвав би
    його. Дивимось у /proc, а не питаємо планувальник: він може бути зайнятий
    тією самою роботою й не відповісти.
    """
    скрипти = {job.command[0]: job.name for job in default_jobs()}
    знайдені = []
    try:
        записи = list(proc.iterdir())
    except OSError:
        return []
    for запис in записи:
        if not запис.name.isdigit():
            continue
        try:
            cmdline = (запис / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        for частина in cmdline:
            назва = скрипти.get(частина.decode(errors="ignore"))
            if назва and назва not in знайдені:
                знайдені.append(назва)
    return знайдені


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Розклад прогонів для Docker")
    parser.add_argument("--plan", action="store_true", help="показати найближчі запуски й вийти")
    parser.add_argument(
        "--busy",
        action="store_true",
        help="код 0, якщо зараз іде якась робота (для автооновлення), інакше 1",
    )
    parser.add_argument("--log-level", default=None, help="DEBUG/INFO/WARNING")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()

    if args.busy:
        зайняті = busy_jobs()
        print(", ".join(зайняті) if зайняті else "нічого не виконується")
        return 0 if зайняті else 1

    setup_logging("scheduler", level=args.log_level)
    jobs = [job for job in default_jobs() if job.hours]

    if args.plan:
        now = datetime.now()
        стан = load_state(state_path())
        print(f"Місцевий час контейнера: {now.astimezone():%Y-%m-%d %H:%M %Z}")
        for job in jobs:
            години = ", ".join(f"{h:02d}:00" for h in job.hours)
            наступний = next_slot(job.hours, now)
            востаннє = стан.get(job.name)
            print(f"  {job.name:<7} о {години} (+до {job.jitter_minutes} хв)")
            print(f"          наступний: {наступний:%Y-%m-%d %H:%M}")
            print(f"          востаннє:  {востаннє:%Y-%m-%d %H:%M}" if востаннє else
                  "          востаннє:  ще не запускався")
        return 0

    try:
        return asyncio.run(main_async(jobs))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
