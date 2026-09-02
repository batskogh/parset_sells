"""Тести моди на рівнях вище окремого товару: кошик, асортимент, індекс."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.core.aggregate import assortment_report, basket_discount, shop_index
from app.core.discount import DiscountRules

D = Decimal
RULES = DiscountRules(min_drop_percent=D("10"), min_samples=5, bucket_percent=D("1"))


def день(number: int) -> date:
    return date(2026, 9, number)


def рівно(товар_цін: list[str], *, від: int = 1) -> list[tuple[date, Decimal]]:
    """Історія одного товару: по одному заміру на добу, починаючи з дати."""
    return [(день(від + i), D(price)) for i, price in enumerate(товар_цін)]


# ------------------------------------------------------------------- кошик


def test_кошик_бачить_звичну_вартість_набору() -> None:
    report = basket_discount(
        {
            "кава": рівно(["200", "200", "200", "200", "200", "200"]),
            "чипси": рівно(["74", "74", "74", "74", "74", "74"]),
        },
        rules=RULES,
    )
    assert report is not None
    assert report.items == 2
    assert report.days == 6
    assert report.mode == D("274.00")
    assert report.current == D("274.00")
    assert report.has_discount is False


def test_кошик_ловить_падіння_набору() -> None:
    report = basket_discount(
        {
            "кава": рівно(["200", "200", "200", "200", "200", "120"]),
            "чипси": рівно(["74", "74", "74", "74", "74", "74"]),
        },
        rules=RULES,
    )
    assert report is not None
    assert report.signal is not None
    assert report.current == D("194.00")
    assert report.mode == D("274.00")
    assert report.signal.drop_percent == D("29.20")


def test_кошик_це_сума_а_не_окремі_товари() -> None:
    """Один товар подорожчав, другий подешевшав більше — набір вигідніший."""
    report = basket_discount(
        {
            "кава": рівно(["200", "200", "200", "200", "200", "260"]),
            "мясо": рівно(["300", "300", "300", "300", "300", "180"]),
        },
        rules=RULES,
    )
    assert report is not None
    assert report.signal is not None
    assert report.current == D("440.00")      # 260 + 180 проти звичних 500


def test_кошик_не_реагує_на_розклад_парсингу() -> None:
    """Другого товару немає в замірах середніх днів — вартість не має стрибати."""
    report = basket_discount(
        {
            "кава": рівно(["200", "200", "200", "200", "200", "200"]),
            "чипси": [(день(1), D("74")), (день(6), D("74"))],
        },
        rules=RULES,
    )
    assert report is not None
    assert report.mode == D("274.00")
    assert report.has_discount is False


def test_кошик_приймає_генератори() -> None:
    """Історії можуть прийти ліниво — розрахунок не має їх «з'їдати»."""
    report = basket_discount(
        {
            "кава": (point for point in рівно(["200"] * 6)),
            "чипси": (point for point in рівно(["74"] * 6)),
        },
        rules=RULES,
    )
    assert report is not None
    assert report.items == 2
    assert report.mode == D("274.00")


def test_порожній_кошик() -> None:
    assert basket_discount({}, rules=RULES) is None


# -------------------------------------------------------------- асортимент


def test_типова_ціна_асортименту() -> None:
    prices = {"a": D("100"), "b": D("100"), "c": D("101"), "d": D("50")}
    report = assortment_report(prices, rules=RULES)
    assert report is not None
    assert report.products == 4
    assert D("99") <= report.mode <= D("101")


def test_асортимент_показує_дешевші_за_типову() -> None:
    prices = {"звичайний": D("100"), "ще один": D("100"), "дешевий": D("60")}
    report = assortment_report(prices, rules=RULES)
    assert report is not None
    assert [item.key for item in report.cheaper] == ["дешевий"]
    assert report.cheaper[0].drop_percent == D("40.00")


def test_на_різнорідному_асортименті_мода_вироджується_і_фіча_мовчить() -> None:
    """Межа сенсу асортиментної моди, зафіксована тестом.

    Три ціни без жодного скупчення — кожна у власному кошику, всі кошики
    однакової ваги. За правилом «при нічиї беремо нижчий» типовою ціною
    магазину техніки стають чипси за 74 ₴. Дешевших за них немає, тож список
    порожній: на різнорідних даних фіча не бреше, а просто мовчить.
    """
    prices = {"відеокарта": D("39199"), "інша карта": D("35000"), "чипси": D("74")}
    report = assortment_report(prices, rules=RULES)
    assert report is not None
    assert report.mode == D("74.00")
    assert report.cheaper == []


def test_на_однорідному_асортименті_мода_осмислена() -> None:
    """Там, де товари одного класу, типова ціна справді щось означає."""
    prices = {
        "карта 1": D("39199"),
        "карта 2": D("39000"),
        "карта 3": D("39100"),
        "карта 4": D("35000"),
        "уцінена": D("20000"),
    }
    report = assortment_report(prices, rules=RULES)
    assert report is not None
    assert D("38900") <= report.mode <= D("39300")
    assert [item.key for item in report.cheaper] == ["уцінена", "карта 4"]


def test_порожній_асортимент() -> None:
    assert assortment_report({}, rules=RULES) is None


# ---------------------------------------------------------- індекс магазину


def test_індекс_бачить_загальне_подорожчання() -> None:
    report = shop_index(
        {
            "a": рівно(["100", "100", "100", "100", "100", "130"]),
            "b": рівно(["200", "200", "200", "200", "200", "260"]),
        },
        rules=RULES,
    )
    assert report is not None
    assert report.mode == D("300.00")
    assert report.current == D("390.00")
    assert report.change_percent == D("30.00")
    assert report.direction == "дорожче за звичне"


def test_індекс_спокійного_магазину() -> None:
    report = shop_index({"a": рівно(["100"] * 6), "b": рівно(["200"] * 6)}, rules=RULES)
    assert report is not None
    assert report.change_percent == D("0.00")
    assert report.direction == "як звично"


def test_індекс_бачить_загальне_здешевлення() -> None:
    report = shop_index(
        {"a": рівно(["100", "100", "100", "100", "100", "70"])},
        rules=RULES,
    )
    assert report is not None
    assert report.change_percent == D("-30.00")
    assert report.direction == "дешевше за звичне"


def test_індекс_без_історії() -> None:
    assert shop_index({}, rules=RULES) is None


# ---------------------------------------------------------- товарна група


def test_модельні_токени_з_назви() -> None:
    from app.core.aggregate import model_tokens

    tokens = model_tokens("Відеокарта MSI RTX5070 12GB GDDR7 (RTX5070 12G GAM TRIO OC)")
    assert "RTX5070" in tokens
    assert "GDDR7" in tokens
    # слова без цифр — це тип товару, а не модель
    assert "ВІДЕОКАРТА" not in tokens
    assert "MSI" not in tokens


def test_група_ловить_дешевшу_серед_схожих() -> None:
    """Історія товару цього не побачить: ціна могла жодного разу не падати."""
    from app.core.aggregate import group_reports

    names = {
        1: "Відеокарта MSI RTX5070 12GB",
        2: "Відеокарта GIGABYTE RTX5070 12GB",
        3: "Відеокарта ASUS RTX5070 12GB",
        4: "Відеокарта PALIT RTX5070 12GB",
    }
    prices = {1: D("39199"), 2: D("39000"), 3: D("39100"), 4: D("25000")}

    reports = group_reports(names, prices, rules=RULES)
    rtx = next(report for report in reports if report.token == "RTX5070")
    assert rtx.members == 4
    assert [item.key for item in rtx.cheaper] == [4]
    assert rtx.cheaper[0].drop_percent > D("35")


def test_замала_група_ігнорується() -> None:
    """Двох товарів замало, щоб казати про типову ціну класу."""
    from app.core.aggregate import group_reports

    names = {1: "Відеокарта MSI RTX5070", 2: "Відеокарта ASUS RTX5070"}
    prices = {1: D("39000"), 2: D("20000")}
    assert group_reports(names, prices, rules=RULES, min_members=3) == []


def test_різні_моделі_не_потрапляють_в_одну_групу() -> None:
    from app.core.aggregate import group_reports

    names = {
        1: "Відеокарта MSI RTX5070",
        2: "Відеокарта MSI RTX5090",
        3: "Чипси Люкс 125 г",
    }
    prices = {1: D("39000"), 2: D("90000"), 3: D("74")}
    tokens = {report.token for report in group_reports(names, prices, rules=RULES, min_members=2)}
    assert "RTX5070" not in tokens or "RTX5090" not in tokens
