"""Тести на те, щоб документація не почала брехати.

README читають тоді, коли щось не працює, — і команда з нього має спрацювати
з першого разу. Документація, яка бреше, гірша за відсутню: відсутня змушує
подивитись у код, а брехлива відправляє шукати неіснуючу проблему.

Тому тут звіряються дві речі, які найчастіше розходяться з кодом непомітно:
прапорці командного рядка й назви налаштувань.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
ДОКУМЕНТИ = [BASE_DIR / "README.md", BASE_DIR / "deploy" / "README.md"]
ТОЧКИ_ВХОДУ = ["run_parser", "run_notify", "run_discounts", "run_web", "run_bot"]

# прапорці, які дає сам argparse або чужі програми в прикладах
СТОРОННІ = {"--help", "--dir", "--keep", "--now", "--recurse-submodules"}


def текст_документів() -> str:
    return "\n\n".join(шлях.read_text(encoding="utf-8") for шлях in ДОКУМЕНТИ)


def прапорці_модуля(назва: str) -> set[str] | None:
    """Прапорці точки входу або None, якщо вона їх не оголошує."""
    модуль = importlib.import_module(назва)
    будівник = getattr(модуль, "build_arg_parser", None)
    if будівник is None:
        return None

    прапорці: set[str] = set()
    for action in будівник()._actions:
        прапорці.update(action.option_strings)
    return прапорці


def згадані_прапорці(модуль: str) -> set[str]:
    """Прапорці, приписані саме цій команді.

    Береться відрізок після назви команди до кінця рядка або до наступної
    команди — залежно від того, що раніше. В одному рядку їх буває дві
    («спершу run_parser.py --all, потім run_notify.py --send»), і прапорець
    другої легко приписати першій.
    """
    знайдені: set[str] = set()
    for рядок in текст_документів().splitlines():
        межі = list(re.finditer(r"run_\w+\.py", рядок))
        for номер, збіг in enumerate(межі):
            if збіг.group() != f"{модуль}.py":
                continue
            кінець = межі[номер + 1].start() if номер + 1 < len(межі) else len(рядок)
            знайдені.update(re.findall(r"--[a-z][a-z-]+", рядок[збіг.end() : кінець]))
    return знайдені


def test_прапорці_з_таблиць_справжні() -> None:
    """Таблиця прапорців застаріває непомітніше за команду в прикладі."""
    відомі: set[str] = set()
    for назва in ТОЧКИ_ВХОДУ:
        відомі |= прапорці_модуля(назва) or set()

    з_таблиць: set[str] = set()
    for рядок in текст_документів().splitlines():
        if рядок.startswith("|") and "`--" in рядок:
            з_таблиць.update(re.findall(r"`(--[a-z][a-z-]+)", рядок))

    вигадані = з_таблиць - відомі - СТОРОННІ
    assert not вигадані, f"у таблицях згадані неіснуючі прапорці: {sorted(вигадані)}"


@pytest.mark.parametrize("модуль", ТОЧКИ_ВХОДУ)
def test_прапорці_з_readme_існують(модуль: str) -> None:
    """Кожен `--прапорець`, згаданий поруч із командою, має бути справжнім."""
    відомі = прапорці_модуля(модуль)
    if відомі is None:
        pytest.skip(f"{модуль} не оголошує прапорців")

    вигадані = згадані_прапорці(модуль) - відомі - СТОРОННІ
    assert not вигадані, f"{модуль}.py таких прапорців не знає: {sorted(вигадані)}"


def test_налаштування_з_readme_описані_в_env_example() -> None:
    """Назва змінної в README має існувати і в `.env.example`, і в конфізі."""
    приклад = (BASE_DIR / ".env.example").read_text(encoding="utf-8")
    конфіг = (BASE_DIR / "app" / "config.py").read_text(encoding="utf-8")
    оголошені = set(re.findall(r"^([A-Z][A-Z0-9_]+)=", приклад, flags=re.MULTILINE))

    # у документах змінні пишуться в апострофах: `DISCOUNT_MIN_SAMPLES`
    згадані = set(re.findall(r"`([A-Z][A-Z0-9_]{4,})`", текст_документів()))
    # службові слова в тому ж написанні, які змінними не є
    згадані -= {"HEAD", "README", "TODO", "SQLITE", "MIDNIGHT"}

    невідомі = {назва for назва in згадані if назва not in оголошені}
    assert not невідомі, f"немає в .env.example: {sorted(невідомі)}"

    неврахoвані = {назва for назва in згадані if f'"{назва}"' not in конфіг}
    assert not неврахoвані, f"config.py таких змінних не читає: {sorted(неврахoвані)}"


def test_шляхи_до_файлів_з_readme_існують() -> None:
    """Посилання на файли проєкту не мають вести в нікуди."""
    згадані = set(re.findall(r"`(app/[\w./]+|deploy/[\w./]+|run_\w+\.py)`", текст_документів()))
    зниклі = {шлях for шлях in згадані if not (BASE_DIR / шлях).exists()}

    assert not зниклі, f"у документах згадані неіснуючі файли: {sorted(зниклі)}"
