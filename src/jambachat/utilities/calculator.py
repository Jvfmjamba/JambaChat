import ast
import math
import operator
from collections.abc import Callable
from typing import Any

MAX_LENGTH = 300
MAX_EXPONENT = 10_000
MAX_MAGNITUDE = 1e300

_BINARY: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY: dict[type[ast.unaryop], Callable[[Any], Any]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sqrt": math.sqrt,
    "raiz": math.sqrt,
    "log": math.log,
    "log10": math.log10,
    "log2": math.log2,
    "exp": math.exp,
    "floor": math.floor,
    "ceil": math.ceil,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "radians": math.radians,
    "degrees": math.degrees,
    "factorial": math.factorial,
}
_CONSTANTS = {"pi": math.pi, "e": math.e}
_REPLACEMENTS = {"^": "**", "×": "*", "÷": "/", "−": "-"}


class CalculationError(ValueError):
    pass


def evaluate(expression: str) -> float | int:
    text = expression.strip()
    if not text or len(text) > MAX_LENGTH:
        raise CalculationError("expressão vazia ou longa demais")
    for old, new in _REPLACEMENTS.items():
        text = text.replace(old, new)
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise CalculationError("expressão inválida") from exc
    try:
        result = _eval(tree.body)
    except CalculationError:
        raise
    except ZeroDivisionError as exc:
        raise CalculationError("divisão por zero") from exc
    except (OverflowError, MemoryError) as exc:
        raise CalculationError("resultado grande demais") from exc
    except (ValueError, TypeError) as exc:
        raise CalculationError("operação matemática inválida") from exc
    if isinstance(result, float) and not math.isfinite(result):
        raise CalculationError("resultado indefinido")
    if isinstance(result, float) and result.is_integer() and abs(result) < 1e15:
        return int(result)
    return result  # type: ignore[no-any-return]


def _check(value: Any) -> Any:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CalculationError("expressão inválida")
    if abs(value) > MAX_MAGNITUDE:
        raise CalculationError("resultado grande demais")
    return value


def _eval(node: ast.expr) -> Any:
    if isinstance(node, ast.Constant):
        return _check(node.value)
    if isinstance(node, ast.Name) and node.id in _CONSTANTS:
        return _CONSTANTS[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and (abs(right) > MAX_EXPONENT or abs(left) > 1e100):
            raise CalculationError("potência grande demais")
        return _check(_BINARY[type(node.op)](left, right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _check(_UNARY[type(node.op)](_eval(node.operand)))
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _FUNCTIONS
        and not node.keywords
        and len(node.args) <= 10
    ):
        args = [_eval(arg) for arg in node.args]
        if node.func.id == "factorial" and (not args or args[0] > 170):
            raise CalculationError("fatorial grande demais")
        return _check(_FUNCTIONS[node.func.id](*args))
    raise CalculationError("expressão inválida; use só números, operadores e funções matemáticas")
