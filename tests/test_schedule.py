"""Тести розкладу для Docker.

Розклад у контейнері мусить поводитись так само, як таймери systemd на голому
сервері: ті самі години, розкид часу і — найважливіше — наздоганяння
пропущеного. Контейнер перезапускається частіше за сервер (оновлення образу,
перезавантаження хоста), і без наздоганяння кожен перезапуск о 05:59
коштував би пропущеного обходу.

Найпідступніші місця — перехід через північ і межі слотів: «рівно о 06:00»
має рахуватись як настале, а не як майбутнє.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

import pytest

from run_scheduler import busy_jobs, load_state, save_state

from app.config import get_settings
from app.core.schedule import (
    Job,
    default_jobs,
    missed,
    next_run,
    next_slot,
    parse_hours,
    previous_slot,
)

ОБХІД = Job(name="parse", command=("run_parser.py", "--all"), hours=(0, 6, 12, 18), jitter_minutes=20)


def о(день: int, година: int, хвилина: int = 0) -> datetime:
    return datetime(2026, 9, день, година, хвилина)


# ------------------------------------------------------------------- години


def test_години_розбираються_з_рядка() -> None:
    assert parse_hours("0,6,12,18") == (0, 6, 12, 18)
    assert parse_hours(" 18, 6 ,0,12 ") == (0, 6, 12, 18)


def test_сміття_й_години_поза_добою_відкидаються() -> None:
    """Описка в .env не має зупинити планувальник — лише вкоротити розклад."""
    assert parse_hours("6,24,abc,,-1,7") == (6, 7)
    assert parse_hours("") == ()


def test_типові_роботи_повторюють_таймери_systemd(monkeypatch) -> None:
    for ключ in ("SCHEDULE_PARSE_HOURS", "SCHEDULE_NOTIFY_HOURS", "SCHEDULE_BACKUP_HOURS"):
        monkeypatch.delenv(ключ, raising=False)
    get_settings.cache_clear()

    роботи = {job.name: job for job in default_jobs()}

    assert роботи["parse"].hours == (0, 6, 12, 18)
    assert роботи["notify"].hours == (7, 13, 19), "нічної розсилки бути не повинно"
    assert роботи["backup"].hours == (3,)
    assert роботи["notify"].command == ("run_notify.py", "--send")
    get_settings.cache_clear()


# ---------------------------------------------------------------- наступний


def test_наступний_слот_у_межах_доби() -> None:
    assert next_slot(ОБХІД.hours, о(10, 7, 30)) == о(10, 12)


def test_наступний_слот_через_північ() -> None:
    assert next_slot(ОБХІД.hours, о(10, 19)) == о(11, 0)


def test_рівно_в_слот_наступний_уже_інший() -> None:
    """О 06:00 рівно запуск 06:00 уже настав — наступний о 12:00."""
    assert next_slot(ОБХІД.hours, о(10, 6)) == о(10, 12)


def test_розкид_не_виходить_за_межі() -> None:
    rng = random.Random(42)
    for _ in range(200):
        момент = next_run(ОБХІД, о(10, 7), rng)
        assert о(10, 12) <= момент <= о(10, 12, 20)


def test_без_розкиду_запуск_рівно_в_слот() -> None:
    бекап = Job(name="backup", command=("x",), hours=(3,), jitter_minutes=0)
    assert next_run(бекап, о(10, 4)) == о(11, 3)


def test_робота_без_годин_не_запускається() -> None:
    вимкнена = Job(name="x", command=("x",), hours=())
    assert next_run(вимкнена, о(10, 4)) is None
    assert missed(вимкнена, None, о(10, 4)) is False


# ------------------------------------------------------------- наздоганяння


def test_попередній_слот_через_північ() -> None:
    assert previous_slot(ОБХІД.hours, о(10, 3)) == о(10, 0)
    assert previous_slot((18,), о(10, 3)) == о(9, 18)


def test_пропущений_запуск_помічається() -> None:
    """Контейнер лежав з 05:00 до 07:00 — обхід 06:00 пропущено."""
    assert missed(ОБХІД, last_started=о(10, 0, 5), now=о(10, 7)) is True


def test_виконаний_запуск_не_повторюється() -> None:
    assert missed(ОБХІД, last_started=о(10, 6, 12), now=о(10, 7)) is False


def test_перший_старт_одразу_наповнює_базу() -> None:
    """На свіжому розгортанні чекати до наступного кола нема сенсу."""
    assert missed(ОБХІД, last_started=None, now=о(10, 7)) is True


def test_розкид_не_робить_запуск_пропущеним() -> None:
    """Старт о 06:17 через розкид — це виконаний запуск 06:00, а не пропуск."""
    assert missed(ОБХІД, last_started=о(10, 6, 17), now=о(10, 11, 59)) is False


# ------------------------------------------------------------------- стан


def test_стан_переживає_перезапуск(tmp_path) -> None:
    шлях = tmp_path / "scheduler.json"
    save_state(шлях, {"parse": о(10, 6, 12), "notify": о(10, 7, 3)})

    assert load_state(шлях) == {"parse": о(10, 6, 12), "notify": о(10, 7, 3)}
    assert not (tmp_path / "scheduler.tmp").exists(), "тимчасовий файл має зникнути"


@pytest.mark.parametrize("вміст", ["", "{не json", '["список"]', '{"parse": "не дата"}'])
def test_зіпсований_стан_не_валить_планувальник(tmp_path, вміст) -> None:
    """Гірше, що може статися, — зайвий прогін, а не мертвий планувальник."""
    шлях = tmp_path / "scheduler.json"
    шлях.write_text(вміст, encoding="utf-8")

    assert load_state(шлях) == {}


def test_відсутній_стан_як_порожній(tmp_path) -> None:
    assert load_state(tmp_path / "немає.json") == {}


def test_доба_вся_покрита_розкладом() -> None:
    """Груба перевірка: з будь-якої хвилини доби наступний обхід — не далі 6 годин."""
    початок = о(10, 0)
    for крок in range(24 * 12):
        момент = початок + timedelta(minutes=5 * крок)
        assert next_slot(ОБХІД.hours, момент) - момент <= timedelta(hours=6)


# ------------------------------------------------- чи зайнятий планувальник


def _процес(proc, pid: str, *аргументи: str) -> None:
    тека = proc / pid
    тека.mkdir()
    (тека / "cmdline").write_bytes(b"\x00".join(а.encode() for а in аргументи) + b"\x00")


def test_обхід_що_йде_помічається(tmp_path) -> None:
    """Автооновлення не має перезапускати контейнер посеред обходу."""
    _процес(tmp_path, "1", "python", "run_scheduler.py")
    _процес(tmp_path, "42", "/usr/local/bin/python", "run_parser.py", "--all")
    (tmp_path / "self").mkdir()       # нецифрові записи /proc ігноруються

    assert busy_jobs(tmp_path) == ["parse"]


def test_сам_планувальник_роботою_не_вважається(tmp_path) -> None:
    _процес(tmp_path, "1", "python", "run_scheduler.py")

    assert busy_jobs(tmp_path) == []


def test_без_proc_нічого_не_ламається(tmp_path) -> None:
    assert busy_jobs(tmp_path / "немає") == []
