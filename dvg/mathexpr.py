"""
Safe evaluator for IR-provided math expressions, e.g. a graph's f(x).

SECURITY: the IR is untrusted input (authored by an LLM or an end user), so we
MUST NOT use eval/exec/compile on these strings — that is a code-injection sink.
Instead we parse to an AST and interpret only a strict allowlist of node types,
names, and functions, failing closed on anything else. No Python code from the
expression is ever executed; unknown constructs raise before any callable is
returned (validated once at IR load time).
"""

from __future__ import annotations

import ast
import math
from typing import Callable

# Defense in depth: bound the input length before parsing.
MAX_LEN = 200

_FUNCS = {
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh,
    "exp": math.exp, "log": math.log, "log10": math.log10,
    "sqrt": math.sqrt, "abs": abs, "floor": math.floor, "ceil": math.ceil,
}
_CONSTS = {"pi": math.pi, "e": math.e, "tau": math.tau}
_BINOPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.Pow: lambda a, b: a ** b,
    ast.Mod: lambda a, b: a % b,
}
_UNARY = {ast.UAdd: lambda a: +a, ast.USub: lambda a: -a}


class ExprError(ValueError):
    """Raised when an expression is malformed or uses a disallowed construct."""


def compile_expr(expr: str) -> Callable[[float], float]:
    """Parse `expr` into a safe callable f(x). Raises ExprError (fail-closed)
    on anything outside the allowlist."""
    if not isinstance(expr, str):
        raise ExprError("expression must be a string")
    if len(expr) > MAX_LEN:
        raise ExprError("expression too long")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ExprError(f"invalid expression: {exc}") from exc
    _check(tree.body)  # validate structure up front, before returning a callable

    def f(x: float) -> float:
        return float(_eval(tree.body, x))

    return f


def _check(node: ast.AST) -> None:
    if isinstance(node, ast.BinOp):
        if type(node.op) not in _BINOPS:
            raise ExprError(f"operator not allowed: {type(node.op).__name__}")
        _check(node.left)
        _check(node.right)
    elif isinstance(node, ast.UnaryOp):
        if type(node.op) not in _UNARY:
            raise ExprError("unary operator not allowed")
        _check(node.operand)
    elif isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
            raise ExprError("function not allowed")
        if node.keywords:
            raise ExprError("keyword args not allowed")
        for arg in node.args:
            _check(arg)
    elif isinstance(node, ast.Name):
        if node.id != "x" and node.id not in _CONSTS:
            raise ExprError(f"name not allowed: {node.id}")
    elif isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ExprError("only numeric constants allowed")
    else:
        raise ExprError(f"syntax not allowed: {type(node).__name__}")


def _eval(node: ast.AST, x: float) -> float:
    if isinstance(node, ast.BinOp):
        return _BINOPS[type(node.op)](_eval(node.left, x), _eval(node.right, x))
    if isinstance(node, ast.UnaryOp):
        return _UNARY[type(node.op)](_eval(node.operand, x))
    if isinstance(node, ast.Call):
        return _FUNCS[node.func.id](*[_eval(a, x) for a in node.args])
    if isinstance(node, ast.Name):
        return x if node.id == "x" else _CONSTS[node.id]
    if isinstance(node, ast.Constant):
        return float(node.value)
    raise ExprError("unexpected node")  # defense in depth; _check should prevent
