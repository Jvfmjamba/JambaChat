import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

MAX_CENTS = 100_000_000_000
_CLEAN = re.compile(r"[^\d,.\-]")


def parse_amount(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("valor inválido")
    if isinstance(value, int | float | Decimal):
        number = Decimal(str(value))
    elif isinstance(value, str):
        number = _parse_text(value)
    else:
        raise ValueError("valor inválido")
    if not number.is_finite():
        raise ValueError("valor inválido")
    cents = int((number * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if cents <= 0:
        raise ValueError("o valor precisa ser maior que zero")
    if cents > MAX_CENTS:
        raise ValueError("valor alto demais")
    return cents


def _parse_text(value: str) -> Decimal:
    text = _CLEAN.sub("", value.strip())
    if not text:
        raise ValueError("valor inválido")
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        whole, _, fraction = text.rpartition(",")
        text = text.replace(",", "") if len(fraction) == 3 and whole else text.replace(",", ".")
    elif text.count(".") > 1 or re.fullmatch(r"\d{1,3}\.\d{3}", text):
        text = text.replace(".", "")
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError("valor inválido") from exc


def format_brl(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    whole, fraction = divmod(abs(cents), 100)
    return f"{sign}R$ {whole:,}".replace(",", ".") + f",{fraction:02d}"
