from datetime import date

import pytest

from jambachat.utilities.calculator import CalculationError, evaluate
from jambachat.utilities.dates import business_days_between, describe_gap, shift


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("2+2", 4),
        ("1200*0.15", 180),
        ("2^10", 1024),
        ("10 ÷ 4", 2.5),
        ("round(10/3, 2)", 3.33),
        ("-(3 - 5) * 2", 4),
        ("max(1, 7, 3)", 7),
        ("sqrt(16) + raiz(9)", 7),
        ("factorial(5)", 120),
        ("7 % 3", 1),
    ],
)
def test_evaluate(expression: str, expected: float) -> None:
    assert evaluate(expression) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("expression", "message"),
    [
        ("1/0", "divisão por zero"),
        ("9**9**9", "grande demais"),
        ('__import__("os")', "inválida"),
        ("open('x')", "inválida"),
        ("(1).__class__", "inválida"),
        ("[1, 2]", "inválida"),
        ("sqrt(-1)", "inválida"),
        ("factorial(1000)", "grande demais"),
        ("2 +", "inválida"),
        ("", "vazia"),
        ("1" * 400, "longa"),
        ("True + 1", "inválida"),
    ],
)
def test_evaluate_rejects(expression: str, message: str) -> None:
    with pytest.raises(CalculationError, match=message):
        evaluate(expression)


def test_business_days() -> None:
    monday, next_monday = date(2026, 11, 2), date(2026, 11, 9)
    assert business_days_between(monday, next_monday) == 5
    assert business_days_between(next_monday, monday) == -5
    assert business_days_between(date(2026, 11, 6), date(2026, 11, 8)) == 0


def test_gap_and_shift() -> None:
    gap = describe_gap(date(2026, 11, 3), date(2026, 12, 25))
    assert (gap["days"], gap["months"], gap["remaining_days"]) == (52, 1, 22)
    assert shift(date(2026, 1, 31), months=1) == date(2026, 2, 28)
    assert shift(date(2026, 11, 3), days=10, weeks=1) == date(2026, 11, 20)
