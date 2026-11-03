import pytest

from jambachat.money import format_brl, parse_amount


@pytest.mark.parametrize(
    ("value", "cents"),
    [
        (42, 4200),
        (42.5, 4250),
        ("42,50", 4250),
        ("R$ 1.234,56", 123456),
        ("1,234.56", 123456),
        ("1.500", 150000),
        ("1,500", 150000),
        ("0,99", 99),
        ("12.5", 1250),
        ("3.000.000", 300000000),
        ("10,5", 1050),
        (" 7 ", 700),
    ],
)
def test_parse_amount(value: object, cents: int) -> None:
    assert parse_amount(value) == cents


@pytest.mark.parametrize("value", ["", "abc", "0", "-3", True, None, "9999999999999", float("nan")])
def test_parse_amount_rejects(value: object) -> None:
    with pytest.raises(ValueError, match=r"valor|zero|alto"):
        parse_amount(value)


@pytest.mark.parametrize(
    ("cents", "text"),
    [(0, "R$ 0,00"), (5, "R$ 0,05"), (123456, "R$ 1.234,56"), (-990, "-R$ 9,90")],
)
def test_format_brl(cents: int, text: str) -> None:
    assert format_brl(cents) == text
