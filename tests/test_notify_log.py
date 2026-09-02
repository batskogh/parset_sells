"""Тести правила «не слати повторно про ту саму знижку».

Дві помилки тут коштують по-різному, і обидві дорого: зайве сповіщення
привчає ігнорувати бота, пропущене — знецінює його. Тому тести описують
сценарії з життя товару, а не окремі гілки коду.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.core.notify_log import LastNotification, NotifyRules, should_notify

D = Decimal
RULES = NotifyRules(cooldown_hours=24, deeper_by_percent=D("10"))
ЗАРАЗ = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)

# звична ціна 100, поріг 10% -> знижка починається нижче 90
ВІДНОВЛЕННЯ = D("90")


def було(*, годин_тому: int, ціна: str) -> LastNotification:
    return LastNotification(price=D(ціна), sent_at=ЗАРАЗ - timedelta(hours=годин_тому))


def рішення(current: str, last=None, since=(), **kwargs):
    return should_notify(
        current=D(current),
        last=last,
        prices_since_last=[D(str(p)) for p in since],
        recovery_price=ВІДНОВЛЕННЯ,
        rules=kwargs.pop("rules", RULES),
        now=kwargs.pop("now", ЗАРАЗ),
    )


def test_про_першу_знижку_кажемо_завжди() -> None:
    decision = рішення("80")
    assert decision.send is True
    assert "перша" in decision.reason


def test_про_ту_саму_знижку_вдруге_не_кажемо() -> None:
    """Наступного дня ціна та сама — новини немає."""
    decision = рішення("80", було(годин_тому=25, ціна="80"), since=[80])
    assert decision.send is False
    assert "вже казали" in decision.reason


def test_знижка_що_поглибшала_це_нова_новина() -> None:
    """Було 80 (−20%), стало 60 — про таке варто сказати."""
    decision = рішення("60", було(годин_тому=25, ціна="80"), since=[80, 60])
    assert decision.send is True
    assert "поглибшала" in decision.reason


def test_дрібне_поглиблення_не_привід() -> None:
    """78 замість 80 — це 2.5%, людина такого не чекає щодня."""
    decision = рішення("78", було(годин_тому=25, ціна="80"), since=[80, 78])
    assert decision.send is False


def test_ціна_відновилась_і_впала_знову() -> None:
    """Класичний випадок: акція скінчилась, за тиждень почалась нова."""
    decision = рішення("80", було(годин_тому=200, ціна="80"), since=[95, 100, 100, 80])
    assert decision.send is True
    assert "відновитись" in decision.reason


def test_пауза_стримує_навіть_нову_знижку() -> None:
    """Ціна стрибає туди-сюди — без паузи це був би потік сповіщень."""
    decision = рішення("70", було(годин_тому=2, ціна="80"), since=[100, 70])
    assert decision.send is False
    assert "пауза" in decision.reason


def test_після_паузи_поглиблення_проходить() -> None:
    decision = рішення("70", було(годин_тому=25, ціна="80"), since=[80, 70])
    assert decision.send is True


def test_подорожчання_після_сповіщення_не_привід_казати_ще_раз() -> None:
    """Ціна піднялась, але все ще нижче звичної — новини немає."""
    decision = рішення("85", було(годин_тому=25, ціна="80"), since=[80, 85])
    assert decision.send is False


def test_наївний_час_із_бази_не_ламає_порівняння() -> None:
    """SQLite віддає datetime без часової зони — це не має валити рішення."""
    last = LastNotification(price=D("80"), sent_at=datetime(2026, 9, 1, 10, 0))
    decision = рішення("80", last, since=[80])
    assert decision.send is False


def test_рішення_можна_читати_як_булеве() -> None:
    assert bool(рішення("80")) is True
    assert bool(рішення("80", було(годин_тому=1, ціна="80"))) is False


def test_пороги_беруться_з_правил() -> None:
    м_який = NotifyRules(cooldown_hours=1, deeper_by_percent=D("2"))
    decision = рішення("78", було(годин_тому=25, ціна="80"), since=[78], rules=м_який)
    assert decision.send is True
