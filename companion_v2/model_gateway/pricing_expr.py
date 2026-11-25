"""Pure stdlib recursive AST evaluator for New API quotaConversion tiered expressions.

Security posture:
- Strictly pure Python stdlib; zero network or host imports.
- Zero ``eval`` / ``exec`` / ``compile`` calls — executes parsed AST nodes directly
  via a bounded recursive interpreter.
- Enforces expression length cap, AST node cap, nesting depth cap (<= 32),
  evaluation step budget (<= 512), string length cap, numeric magnitude cap (1e18),
  unclosed quote rejection, ``str * int`` rejection, and ``RecursionError`` guards.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
import math
import re
from typing import Any


MAX_EXPR_LEN = 4096
MAX_AST_NODES = 1000
MAX_AST_DEPTH = 32
MAX_EVAL_STEPS = 512
MAX_STR_LEN = 10_000
MAX_LIST_LEN = 256
MAX_MAGNITUDE = 1e18
MAX_TIERS = 64
MAX_CALL_ARGS = 32
MAX_SINGLE_MULTIPLIER = 100.0
MAX_CHAINED_MULTIPLIER = 10_000.0

SUPPORTED_EXPR_CURRENCIES = ("USD", "CNY")

NUMERIC_VARIABLES = frozenset({
    "p", "c", "cr", "cc", "cc1h", "len",
    "r", "total", "img", "audio", "ws",
    "ai", "ao", "ii", "io", "vi", "vo",
    "input", "output", "cached", "cache_creation",
})
STRING_VARIABLES = frozenset({
    "tier", "service_tier", "model", "cache_ttl", "modality",
})
ALLOWED_VARIABLES = NUMERIC_VARIABLES | STRING_VARIABLES

ALLOWED_FUNCTIONS = frozenset({
    "min", "max", "abs", "ceil", "floor", "round", "clamp",
    "int", "float",
    "lower", "upper", "contains", "startsWith", "endsWith",
    "tier",
})

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUM_RE = re.compile(r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_MULTI_OPS = ("==", "!=", "<=", ">=", "&&", "||")
_SINGLE_OPS = frozenset("+-*/%<>!?:()[],")


class PricingExprError(ValueError):
    """Base error for pricing expression validation and execution."""

    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason


class PricingExprSyntaxError(PricingExprError):
    """Raised when an expression violates grammar or static safety rules."""

    def __init__(self, reason: str):
        super().__init__("pricing_expr_invalid", reason)


class TieredExprEvaluationError(PricingExprError):
    """Raised when a tiered_expr fails at runtime (never silently returns 0)."""

    def __init__(self, reason: str):
        super().__init__("pricing_expr_eval_failed", reason)


@dataclass(frozen=True)
class _Token:
    kind: str  # "NUM", "STR", "IDENT", "OP"
    value: str


@dataclass(frozen=True)
class CompiledExpr:
    original: str
    stripped: str
    python_source: str
    tree: ast.Expression
    used_variables: frozenset[str]


def strip_v1_prefix(source: str) -> str:
    """Strip optional leading ``v1:`` prefix while preserving trimmed expression text."""
    if type(source) is not str:
        raise PricingExprSyntaxError("表达式必须是字符串")
    text = source.strip()
    if text.startswith("v1:"):
        text = text[3:].strip()
    return text


def _tokenize(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    pos = 0
    length = len(text)
    while pos < length:
        ch = text[pos]
        if ch.isspace():
            pos += 1
            continue
        if ch in ('"', "'"):
            quote = ch
            pos += 1
            chars: list[str] = []
            closed = False
            while pos < length:
                cur = text[pos]
                if cur == "\\":
                    pos += 1
                    if pos >= length:
                        raise PricingExprSyntaxError("字符串字面量转义未闭合")
                    esc = text[pos]
                    if esc == "n":
                        chars.append("\n")
                    elif esc == "t":
                        chars.append("\t")
                    elif esc == "r":
                        chars.append("\r")
                    elif esc in ("\\", '"', "'"):
                        chars.append(esc)
                    else:
                        chars.append(esc)
                    pos += 1
                elif cur == quote:
                    closed = True
                    pos += 1
                    break
                else:
                    chars.append(cur)
                    pos += 1
                if len(chars) > MAX_STR_LEN:
                    raise PricingExprSyntaxError(f"字符串字面量长度超过上限 ({MAX_STR_LEN})")
            if not closed:
                raise PricingExprSyntaxError("字符串字面量缺少闭合引号")
            tokens.append(_Token("STR", "".join(chars)))
            continue

        if ch.isdigit() or (ch == "." and pos + 1 < length and text[pos + 1].isdigit()):
            match = _NUM_RE.match(text, pos)
            if match is None:
                raise PricingExprSyntaxError(f"无效数字字面量 (位置 {pos})")
            raw_num = match.group(0)
            pos = match.end()
            # Reject glued identifier immediately after number (e.g. 123abc)
            if pos < length and (text[pos].isalpha() or text[pos] == "_"):
                raise PricingExprSyntaxError(f"数字后紧跟非法标识符 (位置 {pos})")
            try:
                num_val = float(raw_num)
            except ValueError as error:
                raise PricingExprSyntaxError("数字字面量超出可解析范围") from error
            if not math.isfinite(num_val) or abs(num_val) > MAX_MAGNITUDE:
                raise PricingExprSyntaxError("数字字面量超出安全数值上限 (1e18)")
            tokens.append(_Token("NUM", raw_num))
            continue

        if ch.isascii() and (ch.isalpha() or ch == "_"):
            match = _IDENT_RE.match(text, pos)
            if match is None:
                raise PricingExprSyntaxError(f"无效标识符 (位置 {pos})")
            ident = match.group(0)
            pos = match.end()
            if ident.startswith("_"):
                raise PricingExprSyntaxError(f"禁止访问下划线开头的标识符: {ident}")
            tokens.append(_Token("IDENT", ident))
            continue

        two = text[pos : pos + 2]
        if two == "**":
            raise PricingExprSyntaxError("表达式不支持幂运算符 '**'")
        if two == "//":
            raise PricingExprSyntaxError("表达式不支持整除或注释符号 '//'")
        if two in _MULTI_OPS:
            tokens.append(_Token("OP", two))
            pos += 2
            continue

        if ch in _SINGLE_OPS:
            tokens.append(_Token("OP", ch))
            pos += 1
            continue

        raise PricingExprSyntaxError(f"表达式包含不支持的字符: {ch!r}")
    return tokens


def _split_top_level(tokens: list[_Token], sep: str) -> list[list[_Token]]:
    parts: list[list[_Token]] = []
    current: list[_Token] = []
    depth = 0
    for tok in tokens:
        if tok.kind == "OP" and tok.value in ("(", "["):
            depth += 1
        elif tok.kind == "OP" and tok.value in (")", "]"):
            depth -= 1
            if depth < 0:
                raise PricingExprSyntaxError("括号不匹配")
        if depth == 0 and tok.kind == "OP" and tok.value == sep:
            parts.append(current)
            current = []
        else:
            current.append(tok)
    if depth != 0:
        raise PricingExprSyntaxError("括号未闭合")
    parts.append(current)
    return parts


def _find_matching_close(tokens: list[_Token], start: int, open_ch: str, close_ch: str) -> int:
    depth = 0
    for idx in range(start, len(tokens)):
        tok = tokens[idx]
        if tok.kind == "OP":
            if tok.value == open_ch:
                depth += 1
            elif tok.value == close_ch:
                depth -= 1
                if depth == 0:
                    return idx
    raise PricingExprSyntaxError(f"缺少闭合符号 {close_ch!r}")


def _convert_tokens(tokens: list[_Token], depth: int = 0) -> list[str]:
    if depth > MAX_AST_DEPTH:
        raise PricingExprSyntaxError(f"表达式嵌套深度超过上限 ({MAX_AST_DEPTH})")
    if not tokens:
        raise PricingExprSyntaxError("表达式片段不能为空")

    # First pass: recursively transform parenthesized / bracketed sub-expressions.
    grouped: list[tuple[str, Any]] = []  # ("TOK", _Token) or ("RAW", list[str])
    idx = 0
    total = len(tokens)
    while idx < total:
        tok = tokens[idx]
        if tok.kind == "OP" and tok.value in ("(", "["):
            open_ch = tok.value
            close_ch = ")" if open_ch == "(" else "]"
            end = _find_matching_close(tokens, idx, open_ch, close_ch)
            inner = tokens[idx + 1 : end]
            if not inner:
                grouped.append(("RAW", [open_ch, close_ch]))
            else:
                parts = _split_top_level(inner, ",")
                if len(parts) > MAX_LIST_LEN:
                    raise PricingExprSyntaxError(f"列表或参数数量超过上限 ({MAX_LIST_LEN})")
                rendered_parts: list[str] = [open_ch]
                for part_idx, part in enumerate(parts):
                    if part_idx > 0:
                        rendered_parts.append(",")
                    if not part:
                        # Allow trailing comma only in list literals, disallow empty args
                        if open_ch == "[" and part_idx == len(parts) - 1:
                            continue
                        raise PricingExprSyntaxError("括号或参数列表中存在空片段")
                    rendered_parts.extend(_convert_tokens(part, depth + 1))
                rendered_parts.append(close_ch)
                grouped.append(("RAW", rendered_parts))
            idx = end + 1
            continue
        if tok.kind == "OP" and tok.value in (")", "]"):
            raise PricingExprSyntaxError("括号不匹配")
        grouped.append(("TOK", tok))
        idx += 1

    # Second pass: find top-level ternary '?' ... ':'
    q_idx: int | None = None
    for i, (kind, payload) in enumerate(grouped):
        if kind == "TOK" and payload.kind == "OP" and payload.value == "?":
            q_idx = i
            break
        if kind == "TOK" and payload.kind == "OP" and payload.value == ":":
            raise PricingExprSyntaxError("发现未配对的三元运算符 ':'")

    if q_idx is not None:
        nested = 0
        colon_idx: int | None = None
        for i in range(q_idx + 1, len(grouped)):
            kind, payload = grouped[i]
            if kind == "TOK" and payload.kind == "OP":
                if payload.value == "?":
                    nested += 1
                elif payload.value == ":":
                    if nested == 0:
                        colon_idx = i
                        break
                    nested -= 1
        if colon_idx is None:
            raise PricingExprSyntaxError("三元运算符 '?' 缺少对应的 ':'")
        cond_slice = grouped[:q_idx]
        true_slice = grouped[q_idx + 1 : colon_idx]
        false_slice = grouped[colon_idx + 1 :]
        if not cond_slice or not true_slice or not false_slice:
            raise PricingExprSyntaxError("三元运算符缺少条件或分支")
        cond_py = _convert_grouped(cond_slice, depth + 1)
        true_py = _convert_grouped(true_slice, depth + 1)
        false_py = _convert_grouped(false_slice, depth + 1)
        return ["(", *true_py, ")", "if", "(", *cond_py, ")", "else", "(", *false_py, ")"]

    return _render_flat_grouped(grouped)


def _convert_grouped(grouped: list[tuple[str, Any]], depth: int) -> list[str]:
    if depth > MAX_AST_DEPTH:
        raise PricingExprSyntaxError(f"表达式嵌套深度超过上限 ({MAX_AST_DEPTH})")
    # Check if any top-level '?' remains inside this slice
    q_idx: int | None = None
    for i, (kind, payload) in enumerate(grouped):
        if kind == "TOK" and payload.kind == "OP" and payload.value == "?":
            q_idx = i
            break
        if kind == "TOK" and payload.kind == "OP" and payload.value == ":":
            raise PricingExprSyntaxError("发现未配对的三元运算符 ':'")
    if q_idx is not None:
        nested = 0
        colon_idx: int | None = None
        for i in range(q_idx + 1, len(grouped)):
            kind, payload = grouped[i]
            if kind == "TOK" and payload.kind == "OP":
                if payload.value == "?":
                    nested += 1
                elif payload.value == ":":
                    if nested == 0:
                        colon_idx = i
                        break
                    nested -= 1
        if colon_idx is None:
            raise PricingExprSyntaxError("三元运算符 '?' 缺少对应的 ':'")
        cond_slice = grouped[:q_idx]
        true_slice = grouped[q_idx + 1 : colon_idx]
        false_slice = grouped[colon_idx + 1 :]
        if not cond_slice or not true_slice or not false_slice:
            raise PricingExprSyntaxError("三元运算符缺少条件或分支")
        return [
            "(", *_convert_grouped(true_slice, depth + 1), ")",
            "if", "(", *_convert_grouped(cond_slice, depth + 1), ")",
            "else", "(", *_convert_grouped(false_slice, depth + 1), ")",
        ]
    return _render_flat_grouped(grouped)


def _render_flat_grouped(grouped: list[tuple[str, Any]]) -> list[str]:
    # Rewrite unary '!' cleanly on flat tokens/raw groups.
    rendered: list[str] = []
    idx = 0
    total = len(grouped)
    while idx < total:
        kind, payload = grouped[idx]
        if kind == "TOK" and payload.kind == "OP" and payload.value == "!":
            bang_count = 0
            while (
                idx < total
                and grouped[idx][0] == "TOK"
                and grouped[idx][1].kind == "OP"
                and grouped[idx][1].value == "!"
            ):
                bang_count += 1
                idx += 1
                if bang_count > MAX_AST_DEPTH:
                    raise PricingExprSyntaxError("逻辑非嵌套层数超过上限")
            if idx >= total:
                raise PricingExprSyntaxError("逻辑非 '!' 缺少操作数")
            op_kind, op_payload = grouped[idx]
            if op_kind == "RAW":
                operand = list(op_payload)
                idx += 1
            elif op_payload.kind == "IDENT":
                ident_str = _render_token(op_payload)
                if idx + 1 < total and grouped[idx + 1][0] == "RAW" and grouped[idx + 1][1][0] == "(":
                    operand = [ident_str, *grouped[idx + 1][1]]
                    idx += 2
                else:
                    operand = [ident_str]
                    idx += 1
            elif op_payload.kind in ("NUM", "STR"):
                operand = [_render_token(op_payload)]
                idx += 1
            else:
                raise PricingExprSyntaxError("逻辑非 '!' 后的操作数无效")
            wrapped = operand
            for _ in range(bang_count):
                wrapped = ["(", "not", *wrapped, ")"]
            rendered.extend(wrapped)
            continue

        if kind == "RAW":
            rendered.extend(payload)
        else:
            rendered.append(_render_token(payload))
        idx += 1
    return rendered


def _render_token(tok: _Token) -> str:
    if tok.kind == "STR":
        return repr(tok.value)
    if tok.kind == "NUM":
        return tok.value
    if tok.kind == "OP":
        if tok.value == "&&":
            return "and"
        if tok.value == "||":
            return "or"
        return tok.value
    # IDENT
    if tok.value == "true":
        return "True"
    if tok.value == "false":
        return "False"
    return tok.value


def expr_to_python(source: str) -> str:
    """Convert an expr-lang pricing expression into restricted Python syntax."""
    stripped = strip_v1_prefix(source)
    if not stripped:
        raise PricingExprSyntaxError("表达式不能为空")
    if len(stripped) > MAX_EXPR_LEN:
        raise PricingExprSyntaxError(f"表达式长度超过上限 ({MAX_EXPR_LEN})")
    tokens = _tokenize(stripped)
    if not tokens:
        raise PricingExprSyntaxError("表达式不能为空")
    parts = _convert_tokens(tokens, depth=0)
    return " ".join(parts)


_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod)
_ALLOWED_UNARYOPS = (ast.UAdd, ast.USub, ast.Not)
_ALLOWED_BOOLOPS = (ast.And, ast.Or)
_ALLOWED_CMPOPS = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn)


def _validate_ast(tree: ast.Expression) -> frozenset[str]:
    node_count = 0
    used_vars: set[str] = set()

    def walk(node: ast.AST, depth: int) -> None:
        nonlocal node_count
        node_count += 1
        if node_count > MAX_AST_NODES:
            raise PricingExprSyntaxError(f"表达式节点数超过上限 ({MAX_AST_NODES})")
        if depth > MAX_AST_DEPTH:
            raise PricingExprSyntaxError(f"表达式 AST 嵌套深度超过上限 ({MAX_AST_DEPTH})")

        if isinstance(node, ast.Expression):
            walk(node.body, depth + 1)
            return
        if isinstance(node, ast.Constant):
            val = node.value
            if type(val) is bool:
                return
            if type(val) in (int, float):
                fval = float(val)
                if not math.isfinite(fval) or abs(fval) > MAX_MAGNITUDE:
                    raise PricingExprSyntaxError("常量数值超出安全范围")
                return
            if type(val) is str:
                if len(val) > MAX_STR_LEN:
                    raise PricingExprSyntaxError("字符串常量超过长度上限")
                return
            raise PricingExprSyntaxError(f"不支持的常量类型: {type(val).__name__}")
        if isinstance(node, ast.Name):
            if not isinstance(node.ctx, ast.Load):
                raise PricingExprSyntaxError("仅允许只读变量引用")
            if node.id.startswith("_") or node.id not in ALLOWED_VARIABLES:
                raise PricingExprSyntaxError(f"不支持的变量名: {node.id}")
            used_vars.add(node.id)
            return
        if isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, _ALLOWED_UNARYOPS):
                raise PricingExprSyntaxError(f"不支持的一元运算符: {type(node.op).__name__}")
            walk(node.operand, depth + 1)
            return
        if isinstance(node, ast.BinOp):
            if not isinstance(node.op, _ALLOWED_BINOPS):
                raise PricingExprSyntaxError(f"不支持的二元运算符: {type(node.op).__name__}")
            walk(node.left, depth + 1)
            walk(node.right, depth + 1)
            return
        if isinstance(node, ast.BoolOp):
            if not isinstance(node.op, _ALLOWED_BOOLOPS):
                raise PricingExprSyntaxError("不支持的布尔运算符")
            for val in node.values:
                walk(val, depth + 1)
            return
        if isinstance(node, ast.Compare):
            walk(node.left, depth + 1)
            for op in node.ops:
                if not isinstance(op, _ALLOWED_CMPOPS):
                    raise PricingExprSyntaxError(f"不支持的比较运算符: {type(op).__name__}")
            for comp in node.comparators:
                walk(comp, depth + 1)
            return
        if isinstance(node, ast.IfExp):
            walk(node.test, depth + 1)
            walk(node.body, depth + 1)
            walk(node.orelse, depth + 1)
            return
        if isinstance(node, (ast.List, ast.Tuple)):
            if not isinstance(node.ctx, ast.Load):
                raise PricingExprSyntaxError("仅允许只读列表字面量")
            if len(node.elts) > MAX_LIST_LEN:
                raise PricingExprSyntaxError(f"列表长度超过上限 ({MAX_LIST_LEN})")
            for elt in node.elts:
                walk(elt, depth + 1)
            return
        if isinstance(node, ast.Call):
            if node.keywords:
                raise PricingExprSyntaxError("函数调用不支持关键字参数")
            if not isinstance(node.func, ast.Name) or node.func.id not in ALLOWED_FUNCTIONS:
                fname = node.func.id if isinstance(node.func, ast.Name) else type(node.func).__name__
                raise PricingExprSyntaxError(f"不允许调用函数: {fname}")
            if len(node.args) > MAX_CALL_ARGS:
                raise PricingExprSyntaxError(f"函数参数个数超过上限 ({MAX_CALL_ARGS})")
            for arg in node.args:
                walk(arg, depth + 1)
            return

        raise PricingExprSyntaxError(f"不支持的语法节点: {type(node).__name__}")

    walk(tree, 0)
    return frozenset(used_vars)


def compile_expr(source: str) -> CompiledExpr:
    """Parse and statically validate an expr-lang pricing expression."""
    stripped = strip_v1_prefix(source)
    py_source = expr_to_python(source)
    try:
        tree = ast.parse(py_source, mode="eval")
    except RecursionError as error:
        raise PricingExprSyntaxError("表达式嵌套过深，超出解析限制") from error
    except SyntaxError as error:
        raise PricingExprSyntaxError(f"表达式语法错误: {error.msg}") from error
    except MemoryError as error:
        raise PricingExprSyntaxError("表达式超出内存解析限制") from error

    used_vars = _validate_ast(tree)
    return CompiledExpr(
        original=source.strip(),
        stripped=stripped,
        python_source=py_source,
        tree=tree,
        used_variables=used_vars,
    )


def _check_number(value: Any, *, context: str = "数值") -> float:
    if type(value) not in (int, float):
        raise TieredExprEvaluationError(f"{context}必须是数字，实际得到 {type(value).__name__}")
    num = float(value)
    if not math.isfinite(num):
        raise TieredExprEvaluationError(f"{context}产生非有限数值")
    if abs(num) > MAX_MAGNITUDE:
        raise TieredExprEvaluationError(f"{context}超出安全数值上限 (1e18)")
    return num


def _check_string(value: Any, *, context: str = "字符串") -> str:
    if type(value) is not str:
        raise TieredExprEvaluationError(f"{context}必须是字符串")
    if len(value) > MAX_STR_LEN:
        raise TieredExprEvaluationError(f"{context}长度超过上限 ({MAX_STR_LEN})")
    return value


def _call_builtin(name: str, args: list[Any], *, tier_sink: list[str] | None = None) -> Any:
    if name == "tier":
        if len(args) != 2:
            raise TieredExprEvaluationError("tier() 需要 2 个参数 (name, cost)")
        tier_name = _check_string(args[0], context="tier() 档位名").strip()[:64]
        cost = _check_number(args[1], context="tier() 费用")
        if tier_sink is not None and tier_name:
            tier_sink.append(tier_name)
        return cost
    if name in ("min", "max"):
        if not args:
            raise TieredExprEvaluationError(f"{name}() 至少需要 1 个参数")
        nums = [_check_number(arg, context=f"{name}() 参数") for arg in args]
        return min(nums) if name == "min" else max(nums)
    if name == "abs":
        if len(args) != 1:
            raise TieredExprEvaluationError("abs() 需要 1 个参数")
        return abs(_check_number(args[0], context="abs() 参数"))
    if name == "ceil":
        if len(args) != 1:
            raise TieredExprEvaluationError("ceil() 需要 1 个参数")
        num = _check_number(args[0], context="ceil() 参数")
        result = float(math.ceil(num))
        return _check_number(result, context="ceil() 结果")
    if name == "floor":
        if len(args) != 1:
            raise TieredExprEvaluationError("floor() 需要 1 个参数")
        num = _check_number(args[0], context="floor() 参数")
        result = float(math.floor(num))
        return _check_number(result, context="floor() 结果")
    if name == "round":
        if len(args) not in (1, 2):
            raise TieredExprEvaluationError("round() 需要 1 或 2 个参数")
        num = _check_number(args[0], context="round() 参数")
        if len(args) == 1:
            return _check_number(float(round(num)), context="round() 结果")
        digits_raw = _check_number(args[1], context="round() 位数")
        if digits_raw != int(digits_raw) or not (-12 <= int(digits_raw) <= 12):
            raise TieredExprEvaluationError("round() 位数必须是 -12 到 12 的整数")
        return _check_number(float(round(num, int(digits_raw))), context="round() 结果")
    if name == "clamp":
        if len(args) != 3:
            raise TieredExprEvaluationError("clamp() 需要 3 个参数 (x, lo, hi)")
        x = _check_number(args[0], context="clamp() 值")
        lo = _check_number(args[1], context="clamp() 下界")
        hi = _check_number(args[2], context="clamp() 上界")
        if lo > hi:
            raise TieredExprEvaluationError("clamp() 下界不能大于上界")
        return max(lo, min(hi, x))
    if name == "int":
        if len(args) != 1:
            raise TieredExprEvaluationError("int() 需要 1 个参数")
        num = _check_number(args[0], context="int() 参数")
        return int(num)
    if name == "float":
        if len(args) != 1:
            raise TieredExprEvaluationError("float() 需要 1 个参数")
        return _check_number(args[0], context="float() 参数")
    if name == "lower":
        if len(args) != 1:
            raise TieredExprEvaluationError("lower() 需要 1 个参数")
        return _check_string(args[0], context="lower() 参数").lower()
    if name == "upper":
        if len(args) != 1:
            raise TieredExprEvaluationError("upper() 需要 1 个参数")
        return _check_string(args[0], context="upper() 参数").upper()
    if name == "contains":
        if len(args) != 2:
            raise TieredExprEvaluationError("contains() 需要 2 个参数")
        hay = _check_string(args[0], context="contains() 目标字符串")
        needle = _check_string(args[1], context="contains() 子串")
        return needle in hay
    if name == "startsWith":
        if len(args) != 2:
            raise TieredExprEvaluationError("startsWith() 需要 2 个参数")
        hay = _check_string(args[0], context="startsWith() 目标字符串")
        prefix = _check_string(args[1], context="startsWith() 前缀")
        return hay.startswith(prefix)
    if name == "endsWith":
        if len(args) != 2:
            raise TieredExprEvaluationError("endsWith() 需要 2 个参数")
        hay = _check_string(args[0], context="endsWith() 目标字符串")
        suffix = _check_string(args[1], context="endsWith() 后缀")
        return hay.endswith(suffix)
    raise TieredExprEvaluationError(f"不允许调用函数: {name}")


def _compare_pair(op: ast.cmpop, left: Any, right: Any) -> bool:
    if isinstance(op, ast.Eq):
        if type(left) is bool or type(right) is bool:
            return type(left) is type(right) and left == right
        if type(left) in (int, float) and type(right) in (int, float):
            return float(left) == float(right)
        return type(left) is type(right) and left == right
    if isinstance(op, ast.NotEq):
        return not _compare_pair(ast.Eq(), left, right)
    if isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
        if type(left) in (int, float) and type(right) in (int, float):
            lf, rf = float(left), float(right)
            if isinstance(op, ast.Lt):
                return lf < rf
            if isinstance(op, ast.LtE):
                return lf <= rf
            if isinstance(op, ast.Gt):
                return lf > rf
            return lf >= rf
        if type(left) is str and type(right) is str:
            if isinstance(op, ast.Lt):
                return left < right
            if isinstance(op, ast.LtE):
                return left <= right
            if isinstance(op, ast.Gt):
                return left > right
            return left >= right
        raise TieredExprEvaluationError("顺序比较仅支持同类型数字或字符串")
    if isinstance(op, (ast.In, ast.NotIn)):
        if isinstance(right, (list, tuple)):
            matched = any(_compare_pair(ast.Eq(), left, item) for item in right)
        elif type(right) is str and type(left) is str:
            matched = left in right
        else:
            raise TieredExprEvaluationError("'in' 运算符右操作数必须是列表或字符串")
        return matched if isinstance(op, ast.In) else not matched
    raise TieredExprEvaluationError(f"不支持的比较运算符: {type(op).__name__}")


def eval_compiled_expr(
    compiled: CompiledExpr,
    variables: dict[str, Any],
    *,
    tier_sink: list[str] | None = None,
) -> Any:
    """Execute a compiled AST expression with strict depth and step limits."""
    steps = 0

    def interpret(node: ast.AST, depth: int) -> Any:
        nonlocal steps
        steps += 1
        if steps > MAX_EVAL_STEPS:
            raise TieredExprEvaluationError(f"表达式求值步数超过上限 ({MAX_EVAL_STEPS})")
        if depth > MAX_AST_DEPTH:
            raise TieredExprEvaluationError(f"表达式求值深度超过上限 ({MAX_AST_DEPTH})")

        if isinstance(node, ast.Expression):
            return interpret(node.body, depth + 1)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in variables:
                raise TieredExprEvaluationError(f"缺少表达式变量: {node.id}")
            return variables[node.id]
        if isinstance(node, ast.UnaryOp):
            operand = interpret(node.operand, depth + 1)
            if isinstance(node.op, ast.Not):
                return not bool(operand)
            num = _check_number(operand, context="一元运算")
            if isinstance(node.op, ast.UAdd):
                return +num
            if isinstance(node.op, ast.USub):
                return -num
            raise TieredExprEvaluationError("不支持的一元运算符")
        if isinstance(node, ast.BinOp):
            left = interpret(node.left, depth + 1)
            right = interpret(node.right, depth + 1)
            if isinstance(node.op, ast.Add):
                if type(left) is str and type(right) is str:
                    if len(left) + len(right) > MAX_STR_LEN:
                        raise TieredExprEvaluationError(f"字符串拼接超出上限 ({MAX_STR_LEN})")
                    return left + right
                return _check_number(
                    _check_number(left, context="加法左操作数") + _check_number(right, context="加法右操作数"),
                    context="加法结果",
                )
            if isinstance(node.op, ast.Sub):
                return _check_number(
                    _check_number(left, context="减法左操作数") - _check_number(right, context="减法右操作数"),
                    context="减法结果",
                )
            if isinstance(node.op, ast.Mult):
                # Explicitly rejects str * int, int * str, list * int, and bool * int!
                lf = _check_number(left, context="乘法左操作数")
                rf = _check_number(right, context="乘法右操作数")
                return _check_number(lf * rf, context="乘法结果")
            if isinstance(node.op, ast.Div):
                lf = _check_number(left, context="除法左操作数")
                rf = _check_number(right, context="除法右操作数")
                if rf == 0.0:
                    raise TieredExprEvaluationError("表达式发生除以零错误")
                return _check_number(lf / rf, context="除法结果")
            if isinstance(node.op, ast.Mod):
                lf = _check_number(left, context="取模左操作数")
                rf = _check_number(right, context="取模右操作数")
                if rf == 0.0:
                    raise TieredExprEvaluationError("表达式发生对零取模错误")
                return _check_number(lf % rf, context="取模结果")
            raise TieredExprEvaluationError("不支持的二元运算符")
        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                result: Any = True
                for item in node.values:
                    result = interpret(item, depth + 1)
                    if not result:
                        return False
                return True
            if isinstance(node.op, ast.Or):
                for item in node.values:
                    result = interpret(item, depth + 1)
                    if result:
                        return True
                return False
            raise TieredExprEvaluationError("不支持的布尔运算符")
        if isinstance(node, ast.Compare):
            left = interpret(node.left, depth + 1)
            for op, comparator in zip(node.ops, node.comparators):
                right = interpret(comparator, depth + 1)
                if not _compare_pair(op, left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.IfExp):
            cond = interpret(node.test, depth + 1)
            if cond:
                return interpret(node.body, depth + 1)
            return interpret(node.orelse, depth + 1)
        if isinstance(node, (ast.List, ast.Tuple)):
            return [interpret(elt, depth + 1) for elt in node.elts]
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise TieredExprEvaluationError("仅支持直接调用白名单内置函数")
            args = [interpret(arg, depth + 1) for arg in node.args]
            return _call_builtin(node.func.id, args, tier_sink=tier_sink)
        raise TieredExprEvaluationError(f"不支持的执行节点: {type(node).__name__}")

    try:
        return interpret(compiled.tree, 0)
    except RecursionError as error:
        raise TieredExprEvaluationError("表达式递归求值超出栈深度限制") from error


def _normalize_expr_currency(value: object) -> str:
    if value is None or value == "":
        return "USD"
    if type(value) is str and value in SUPPORTED_EXPR_CURRENCIES:
        return value
    raise PricingExprSyntaxError("单价币种仅支持 USD 或 CNY")


def normalize_tiered_expr_rule(
    raw: object,
    *,
    max_multiplier: float = MAX_CHAINED_MULTIPLIER,
    currency: str | None = None,
) -> dict[str, Any]:
    """Validate and normalize a ``tiered_expr`` rule dict (single expr or tiers)."""
    if type(raw) is str:
        norm_currency = _normalize_expr_currency(currency)
        compiled = compile_expr(raw)
        return {
            "mode": "tiered_expr",
            "currency": norm_currency,
            "expr": compiled.original,
            "tiers": [],
            "default_expr": None,
        }
    if type(raw) is not dict:
        raise PricingExprSyntaxError("表达式规则必须是对象或字符串")

    norm_currency = _normalize_expr_currency(raw.get("currency") if "currency" in raw else currency)

    raw_expr = raw.get("expr")
    if raw_expr is None:
        raw_expr = raw.get("formula") or raw.get("expression")
    raw_tiers = raw.get("tiers")
    raw_default = raw.get("default_expr")
    if raw_default is None:
        raw_default = raw.get("default")

    tiers: list[dict[str, str]] = []
    if raw_tiers is not None:
        if type(raw_tiers) is not list:
            raise PricingExprSyntaxError("tiers 必须是数组")
        if len(raw_tiers) > MAX_TIERS:
            raise PricingExprSyntaxError(f"阶梯表达式最多支持 {MAX_TIERS} 个档位")
        for idx, tier in enumerate(raw_tiers):
            if type(tier) is not dict:
                raise PricingExprSyntaxError(f"第 {idx + 1} 个档位必须是对象")
            cond_raw = tier.get("condition")
            if cond_raw is None:
                cond_raw = tier.get("when")
            if cond_raw is None:
                cond_raw = tier.get("match")
            formula_raw = tier.get("expr")
            if formula_raw is None:
                formula_raw = tier.get("formula")
            if formula_raw is None:
                formula_raw = tier.get("expression")
            if type(formula_raw) is not str or not formula_raw.strip():
                raise PricingExprSyntaxError(f"第 {idx + 1} 个档位缺少计价表达式")
            compiled_formula = compile_expr(formula_raw)
            cond_clean = ""
            if cond_raw is not None and str(cond_raw).strip():
                if type(cond_raw) is not str:
                    raise PricingExprSyntaxError(f"第 {idx + 1} 个档位条件必须是字符串")
                compiled_cond = compile_expr(cond_raw)
                cond_clean = compiled_cond.original
            name_raw = tier.get("name") if tier.get("name") is not None else tier.get("label")
            name = str(name_raw or f"tier_{idx + 1}").strip()[:64] or f"tier_{idx + 1}"
            tiers.append({
                "name": name,
                "condition": cond_clean,
                "expr": compiled_formula.original,
            })

    expr_clean: str | None = None
    if raw_expr is not None and str(raw_expr).strip():
        if type(raw_expr) is not str:
            raise PricingExprSyntaxError("expr 必须是字符串")
        expr_clean = compile_expr(raw_expr).original

    default_clean: str | None = None
    if raw_default is not None and str(raw_default).strip():
        if type(raw_default) is not str:
            raise PricingExprSyntaxError("default_expr 必须是字符串")
        default_clean = compile_expr(raw_default).original

    if expr_clean is None and not tiers:
        raise PricingExprSyntaxError("tiered_expr 必须提供 expr 或至少一个 tiers 档位")

    cluster_mult_raw = raw.get("cluster_multiplier", 1.0)
    if isinstance(cluster_mult_raw, bool) or type(cluster_mult_raw) not in (int, float):
        raise PricingExprSyntaxError("cluster_multiplier 必须是数字")
    cluster_mult = float(cluster_mult_raw)
    if not math.isfinite(cluster_mult) or not (0.0 <= cluster_mult <= max_multiplier):
        raise PricingExprSyntaxError(f"cluster_multiplier 必须在 0 到 {int(max_multiplier)} 之间")

    result: dict[str, Any] = {
        "mode": "tiered_expr",
        "currency": norm_currency,
        "expr": expr_clean,
        "tiers": tiers,
        "default_expr": default_clean,
    }
    if cluster_mult != 1.0:
        result["cluster_multiplier"] = round(cluster_mult, 6)
    return result


def _compile_rule(rule: dict[str, Any]) -> tuple[
    CompiledExpr | None,
    list[tuple[str, CompiledExpr | None, CompiledExpr]],
    CompiledExpr | None,
    frozenset[str],
]:
    used: set[str] = set()
    compiled_expr: CompiledExpr | None = None
    if rule.get("expr"):
        compiled_expr = compile_expr(rule["expr"])
        used.update(compiled_expr.used_variables)
    compiled_tiers: list[tuple[str, CompiledExpr | None, CompiledExpr]] = []
    for idx, tier in enumerate(rule.get("tiers") or ()):
        name = str(tier.get("name") or f"tier_{idx + 1}")
        cond_src = tier.get("condition") or ""
        cond_compiled = compile_expr(cond_src) if cond_src.strip() else None
        if cond_compiled is not None:
            used.update(cond_compiled.used_variables)
        expr_compiled = compile_expr(tier["expr"])
        used.update(expr_compiled.used_variables)
        compiled_tiers.append((name, cond_compiled, expr_compiled))
    compiled_default: CompiledExpr | None = None
    if rule.get("default_expr"):
        compiled_default = compile_expr(rule["default_expr"])
        used.update(compiled_default.used_variables)
    return compiled_expr, compiled_tiers, compiled_default, frozenset(used)


def extract_rule_variables(rule: dict[str, Any]) -> frozenset[str]:
    """Return all variable identifiers referenced across a tiered_expr rule."""
    normalized = normalize_tiered_expr_rule(rule)
    _, _, _, used = _compile_rule(normalized)
    return used


def build_expr_variables(
    *,
    used_variables: frozenset[str],
    other_input_tokens: int,
    cached_input_tokens: int = 0,
    cache_creation_tokens: int = 0,
    cache_creation_1h_tokens: int = 0,
    output_tokens: int = 0,
    service_tier: str = "",
    model: str = "",
    extra_vars: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Opt-in variable normalization: ``p`` absorbs ``cr``/``cc``/``cc1h`` unless referenced."""
    other = max(0, int(other_input_tokens))
    cached = max(0, int(cached_input_tokens))
    cc = max(0, int(cache_creation_tokens))
    cc1h = max(0, int(cache_creation_1h_tokens))
    out = max(0, int(output_tokens))

    creation_total = cc + cc1h
    total_in = other + cached + creation_total

    has_cr = "cr" in used_variables or "cached" in used_variables
    has_cc = "cc" in used_variables or "cache_creation" in used_variables
    has_cc1h = "cc1h" in used_variables

    p_val = other
    if not has_cr:
        p_val += cached
    if not has_cc:
        p_val += cc
    if not has_cc1h:
        p_val += cc1h

    cr_val = cached if has_cr else 0
    cc_val = cc if has_cc else 0
    cc1h_val = cc1h if has_cc1h else 0

    tier_str = str(service_tier or "").strip()
    model_str = str(model or "").strip()

    env: dict[str, Any] = {
        "p": p_val,
        "c": out,
        "cr": cr_val,
        "cc": cc_val,
        "cc1h": cc1h_val,
        "len": total_in,
        "r": 0,
        "total": total_in + out,
        "img": 0,
        "audio": 0,
        "ws": 0,
        "ai": 0,
        "ao": 0,
        "ii": 0,
        "io": 0,
        "vi": 0,
        "vo": 0,
        "input": p_val,
        "output": out,
        "cached": cr_val,
        "cache_creation": cc_val,
        "tier": tier_str,
        "service_tier": tier_str,
        "model": model_str,
        "cache_ttl": "",
        "modality": "",
    }
    if isinstance(extra_vars, dict):
        for key, value in extra_vars.items():
            if key in NUMERIC_VARIABLES and type(value) in (int, float) and math.isfinite(float(value)):
                env[key] = max(0.0, float(value))
            elif key in STRING_VARIABLES and type(value) is str and len(value) <= MAX_STR_LEN:
                env[key] = value
    return env


def evaluate_tiered_expr_rule(
    rule: dict[str, Any],
    *,
    other_input_tokens: int,
    cached_input_tokens: int = 0,
    cache_creation_tokens: int = 0,
    cache_creation_1h_tokens: int = 0,
    output_tokens: int = 0,
    service_tier: str = "",
    model: str = "",
    extra_vars: dict[str, Any] | None = None,
) -> tuple[float, str]:
    """Evaluate a ``tiered_expr`` rule and return ``(microusd_float, matched_tier)``.

    New API ``quotaConversion`` computes microUSD-scale units directly
    (so ``cost_usd = raw_result / 1_000_000.0 * cluster_multiplier``).
    Never silently returns 0 on error — raises ``TieredExprEvaluationError``.
    """
    normalized = normalize_tiered_expr_rule(rule)
    compiled_expr, compiled_tiers, compiled_default, used_vars = _compile_rule(normalized)
    env = build_expr_variables(
        used_variables=used_vars,
        other_input_tokens=other_input_tokens,
        cached_input_tokens=cached_input_tokens,
        cache_creation_tokens=cache_creation_tokens,
        cache_creation_1h_tokens=cache_creation_1h_tokens,
        output_tokens=output_tokens,
        service_tier=service_tier,
        model=model,
        extra_vars=extra_vars,
    )

    matched_tier = ""
    raw_value: Any = None
    tier_sink: list[str] = []
    if compiled_tiers:
        matched = False
        for name, cond_compiled, expr_compiled in compiled_tiers:
            if cond_compiled is None or bool(eval_compiled_expr(cond_compiled, env)):
                raw_value = eval_compiled_expr(expr_compiled, env, tier_sink=tier_sink)
                matched_tier = tier_sink[-1] if tier_sink else name
                matched = True
                break
        if not matched:
            if compiled_default is not None:
                raw_value = eval_compiled_expr(compiled_default, env, tier_sink=tier_sink)
                matched_tier = tier_sink[-1] if tier_sink else "default"
            elif compiled_expr is not None:
                raw_value = eval_compiled_expr(compiled_expr, env, tier_sink=tier_sink)
                matched_tier = tier_sink[-1] if tier_sink else "expr"
            else:
                raise TieredExprEvaluationError("没有任何档位条件匹配当前调用且未配置默认表达式")
    else:
        if compiled_expr is None:
            raise TieredExprEvaluationError("tiered_expr 缺少可执行的计价表达式")
        raw_value = eval_compiled_expr(compiled_expr, env, tier_sink=tier_sink)
        matched_tier = tier_sink[-1] if tier_sink else "expr"

    numeric = _check_number(raw_value, context="表达式最终结果")
    if numeric < 0.0:
        raise TieredExprEvaluationError("表达式计算结果为负数")

    cluster_mult = float(normalized.get("cluster_multiplier", 1.0))
    scaled = _check_number(numeric * cluster_mult, context="倍率调整后费用")
    if scaled < 0.0:
        raise TieredExprEvaluationError("倍率调整后费用为负数")
    return scaled, matched_tier


_DEFAULT_SAMPLE_SCENARIOS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("标准短上下文 (10K 输入 / 2K 输出)", {
        "other_input_tokens": 10_000, "cached_input_tokens": 0,
        "cache_creation_tokens": 0, "cache_creation_1h_tokens": 0,
        "output_tokens": 2_000, "service_tier": "",
    }),
    ("命中缓存 (8K 未命中 / 20K 缓存读 / 4K 缓存写 / 2K 输出)", {
        "other_input_tokens": 8_000, "cached_input_tokens": 20_000,
        "cache_creation_tokens": 4_000, "cache_creation_1h_tokens": 0,
        "output_tokens": 2_000, "service_tier": "",
    }),
    ("超长上下文 (250K 输入 / 4K 输出)", {
        "other_input_tokens": 250_000, "cached_input_tokens": 0,
        "cache_creation_tokens": 0, "cache_creation_1h_tokens": 0,
        "output_tokens": 4_000, "service_tier": "",
    }),
)


def validate_tiered_expr_rule(
    raw: object,
    *,
    usd_cny_rate: float = 7.2,
    sample: dict[str, Any] | None = None,
    max_multiplier: float = MAX_SINGLE_MULTIPLIER,
    currency: str | None = None,
) -> dict[str, Any]:
    """Validate a tiered_expr rule and return sample USD/CNY previews."""
    rate = float(usd_cny_rate) if type(usd_cny_rate) in (int, float) and math.isfinite(float(usd_cny_rate)) and usd_cny_rate > 0 else 7.2
    try:
        normalized = normalize_tiered_expr_rule(raw, max_multiplier=max_multiplier, currency=currency)
        _, _, _, used_vars = _compile_rule(normalized)
    except PricingExprError as error:
        return {
            "valid": False,
            "error": error.reason,
            "code": error.code,
            "used_variables": [],
            "currency": "USD",
            "usd_cny_rate": round(rate, 6),
            "samples": [],
        }

    rule_currency = str(normalized.get("currency") or "USD")
    scenarios: list[tuple[str, dict[str, Any]]] = []
    if isinstance(sample, dict) and sample:
        def _nonneg_int(val: Any) -> int:
            if isinstance(val, bool) or type(val) not in (int, float):
                return 0
            num = float(val)
            if not math.isfinite(num) or num < 0 or num > 1e12:
                return 0
            return int(num)

        custom_case = {
            "other_input_tokens": _nonneg_int(sample.get("p", sample.get("other_input_tokens", 10_000))),
            "cached_input_tokens": _nonneg_int(sample.get("cr", sample.get("cached_input_tokens", 0))),
            "cache_creation_tokens": _nonneg_int(sample.get("cc", sample.get("cache_creation_tokens", 0))),
            "cache_creation_1h_tokens": _nonneg_int(sample.get("cc1h", sample.get("cache_creation_1h_tokens", 0))),
            "output_tokens": _nonneg_int(sample.get("c", sample.get("output_tokens", 2_000))),
            "service_tier": str(sample.get("tier", sample.get("service_tier", "")) or "")[:64],
        }
        scenarios.append(("自定义试算样本", custom_case))
    scenarios.extend(_DEFAULT_SAMPLE_SCENARIOS)

    previews: list[dict[str, Any]] = []
    try:
        for label, case in scenarios:
            raw_micro, matched_tier = evaluate_tiered_expr_rule(normalized, **case)
            if rule_currency == "CNY":
                cost_cny = raw_micro / 1_000_000.0
                cost_usd = cost_cny / rate
                cost_microusd = int(round(cost_usd * 1_000_000.0))
            else:
                cost_usd = raw_micro / 1_000_000.0
                cost_cny = cost_usd * rate
                cost_microusd = int(round(raw_micro))
            total_in = (
                case["other_input_tokens"]
                + case["cached_input_tokens"]
                + case["cache_creation_tokens"]
                + case["cache_creation_1h_tokens"]
            )
            previews.append({
                "label": label,
                "input": {
                    "p": case["other_input_tokens"],
                    "cr": case["cached_input_tokens"],
                    "cc": case["cache_creation_tokens"],
                    "cc1h": case["cache_creation_1h_tokens"],
                    "c": case["output_tokens"],
                    "len": total_in,
                    "tier": case["service_tier"],
                },
                "cost_microusd": cost_microusd,
                "cost_usd": round(cost_usd, 8),
                "cost_cny": round(cost_cny, 8),
                "matched_tier": matched_tier,
            })
    except PricingExprError as error:
        return {
            "valid": False,
            "error": error.reason,
            "code": error.code,
            "used_variables": sorted(used_vars),
            "currency": rule_currency,
            "usd_cny_rate": round(rate, 6),
            "samples": [],
        }

    return {
        "valid": True,
        "error": None,
        "code": None,
        "used_variables": sorted(used_vars),
        "currency": rule_currency,
        "usd_cny_rate": round(rate, 6),
        "samples": previews,
        "normalized": normalized,
    }


_LINEAR_LOWER_VARS = frozenset({"p", "cr", "cc", "c"})


def _ast_nonneg_number(node: ast.AST) -> float | None:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd):
        return _ast_nonneg_number(node.operand)
    if isinstance(node, ast.Constant) and not isinstance(node.value, bool) and type(node.value) in (int, float):
        val = float(node.value)
        if math.isfinite(val) and val >= 0.0:
            return val
    return None


def _unwrap_tier_call(node: ast.AST) -> ast.AST | None:
    if (
        isinstance(node, ast.Call)
        and not node.keywords
        and isinstance(node.func, ast.Name)
        and node.func.id == "tier"
        and len(node.args) == 2
        and isinstance(node.args[0], ast.Constant)
        and type(node.args[0].value) is str
    ):
        return node.args[1]
    return None


def _extract_linear_4var_rates(
    node: ast.AST,
    *,
    scale: float,
    max_price: float,
) -> dict[str, float | None] | None:
    """Extract lossless linear coefficients for ``{p, cr, cc, c}`` or return ``None``."""
    coeffs: dict[str, float] = {}
    has_term = False

    def _collect(sub: ast.AST) -> bool:
        nonlocal has_term
        if isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.Add):
            return _collect(sub.left) and _collect(sub.right)
        zero_const = _ast_nonneg_number(sub)
        if zero_const is not None:
            if zero_const != 0.0:
                return False
            has_term = True
            return True
        if isinstance(sub, ast.Name) and sub.id in _LINEAR_LOWER_VARS:
            coeffs[sub.id] = coeffs.get(sub.id, 0.0) + 1.0
            has_term = True
            return True
        if isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.Mult):
            left_num = _ast_nonneg_number(sub.left)
            right_num = _ast_nonneg_number(sub.right)
            if isinstance(sub.left, ast.Name) and sub.left.id in _LINEAR_LOWER_VARS and right_num is not None:
                coeffs[sub.left.id] = coeffs.get(sub.left.id, 0.0) + right_num
                has_term = True
                return True
            if isinstance(sub.right, ast.Name) and sub.right.id in _LINEAR_LOWER_VARS and left_num is not None:
                coeffs[sub.right.id] = coeffs.get(sub.right.id, 0.0) + left_num
                has_term = True
                return True
        return False

    if not _collect(node) or not has_term:
        return None

    def _scaled(val: float | None) -> float | None:
        if val is None:
            return None
        out = round(val * scale, 6)
        if not math.isfinite(out) or out < 0.0 or out > max_price:
            return -1.0
        return out

    inp = _scaled(coeffs.get("p", 0.0))
    out = _scaled(coeffs.get("c", 0.0))
    cached = _scaled(coeffs["cr"]) if "cr" in coeffs else None
    creation = _scaled(coeffs["cc"]) if "cc" in coeffs else None
    if any(v is not None and v < 0.0 for v in (inp, out, cached, creation)):
        return None
    return {
        "input": inp,
        "input_cached": cached,
        "cache_creation": creation,
        "output": out,
    }


def _extract_len_lte_threshold(node: ast.AST) -> int | None:
    if (
        isinstance(node, ast.Compare)
        and len(node.ops) == 1
        and isinstance(node.ops[0], ast.LtE)
        and len(node.comparators) == 1
        and isinstance(node.left, ast.Name)
        and node.left.id == "len"
    ):
        num = _ast_nonneg_number(node.comparators[0])
        if num is not None and num >= 1.0 and num == int(num) and int(num) <= 10**9:
            return int(num)
    return None


def lower_newapi_billing_expr(
    source: str,
    *,
    currency: str = "USD",
    unit_scale: float = 1.0,
    max_price: float = 1_000_000.0,
) -> tuple[dict[str, Any], str]:
    """Validate a New API ``billing_expr`` and losslessly lower simple patterns.

    Returns ``(rule_dict, lowering_kind)`` where ``lowering_kind`` is one of
    ``"per_token"``, ``"per_tier"``, or ``"tiered_expr"``.
    Raises ``PricingExprError`` when the expression is invalid or unsupported.
    """
    norm_currency = _normalize_expr_currency(currency)
    scale = float(unit_scale)
    if not math.isfinite(scale) or scale < 0.0 or scale > 10_000.0:
        raise PricingExprSyntaxError("汇率换算系数无效")

    compiled = compile_expr(source)
    body = compiled.tree.body

    # Case 1: Single tier("name", linear_4var) -> lossless per_token rule
    single_inner = _unwrap_tier_call(body)
    if single_inner is not None:
        rates = _extract_linear_4var_rates(single_inner, scale=scale, max_price=max_price)
        if rates is not None:
            inp = rates["input"]
            cached = rates["input_cached"]
            creation = rates["cache_creation"]
            out = rates["output"]
            return (
                {
                    "mode": "per_token",
                    "currency": norm_currency,
                    "input": inp,
                    "input_cached": cached,
                    "cache_creation": creation,
                    "output": out,
                    "multiplier": 1.0,
                    "free": (
                        inp == 0.0
                        and out == 0.0
                        and (cached is None or cached == 0.0)
                        and (creation is None or creation == 0.0)
                    ),
                    "configured": {
                        "input": inp is not None,
                        "input_cached": cached is not None,
                        "cache_creation": creation is not None,
                        "output": out is not None,
                    },
                },
                "per_token",
            )

    # Case 2: Ascending len <= T1 ? tier(...) : ... : tier(...) -> lossless per_tier rule
    if isinstance(body, ast.IfExp):
        thresholds: list[int] = []
        branches: list[dict[str, float | None]] = []
        cursor: ast.AST = body
        ok_chain = True
        while isinstance(cursor, ast.IfExp):
            thresh = _extract_len_lte_threshold(cursor.test)
            branch_inner = _unwrap_tier_call(cursor.body)
            branch_rates = (
                _extract_linear_4var_rates(branch_inner, scale=scale, max_price=max_price)
                if branch_inner is not None
                else None
            )
            if thresh is None or branch_rates is None:
                ok_chain = False
                break
            if thresholds and thresh <= thresholds[-1]:
                ok_chain = False
                break
            thresholds.append(thresh)
            branches.append(branch_rates)
            cursor = cursor.orelse
            if len(thresholds) > 32:
                ok_chain = False
                break
        if ok_chain and thresholds:
            final_inner = _unwrap_tier_call(cursor)
            final_rates = (
                _extract_linear_4var_rates(final_inner, scale=scale, max_price=max_price)
                if final_inner is not None
                else None
            )
            if final_rates is not None:
                branches.append(final_rates)
                # Only lower to per_tier when every branch consistently includes/omits cr and cc;
                # mixed usage across branches must stay tiered_expr for 100% lossless semantics.
                cached_flags = {b["input_cached"] is not None for b in branches}
                create_flags = {b["cache_creation"] is not None for b in branches}
                if len(cached_flags) == 1 and len(create_flags) == 1:
                    base_rates = branches[0]
                    context_tiers: list[dict[str, Any]] = []
                    for idx, thresh in enumerate(thresholds):
                        tier_rates = branches[idx + 1]
                        context_tiers.append({
                            "threshold_tokens": thresh,
                            "input": tier_rates["input"],
                            "input_cached": tier_rates["input_cached"],
                            "cache_creation": tier_rates["cache_creation"],
                            "output": tier_rates["output"],
                        })
                    return (
                        {
                            "mode": "per_tier",
                            "currency": norm_currency,
                            "base": {
                                "input": base_rates["input"],
                                "input_cached": base_rates["input_cached"],
                                "cache_creation": base_rates["cache_creation"],
                                "output": base_rates["output"],
                            },
                            "context_tiers": context_tiers,
                            "service_tiers": [],
                            "multiplier": 1.0,
                        },
                        "per_tier",
                    )

    # Case 3: Keep full tiered_expr (e.g., cc1h, ai, strict '<', service_tier, complex math)
    if abs(scale - 1.0) > 1e-12:
        scale_str = f"{round(scale, 6):g}"
        expr_text = f"({compiled.stripped}) * {scale_str}"
    else:
        expr_text = compiled.original
    validation = validate_tiered_expr_rule({"expr": expr_text, "currency": norm_currency})
    if not validation["valid"]:
        raise PricingExprSyntaxError(validation["error"] or "表达式验证失败")
    return validation["normalized"], "tiered_expr"
