"""Shared operand/condition transpiler for both code generators.

One engine, two targets (`pine` and `py`), so the Pine Script strategy and the
Python backtest can never disagree about what a rule means. Given the `machine`
predicates from the spec, it accumulates the indicator declarations actually
referenced and emits boolean expressions. Operands outside the vocabulary are
collected in `.unsupported` so the caller can document them instead of guessing.
"""
from __future__ import annotations

import re

from .schema import VALID_OPS

_NUM = re.compile(r"^-?\d+(\.\d+)?$")
_EMA = re.compile(r"^ema_(\d+)$")
_SMA = re.compile(r"^sma_(\d+)$")
_RSI = re.compile(r"^rsi_(\d+)$")
_ATR = re.compile(r"^atr_(\d+)$")

_PRICE = {"close", "open", "high", "low", "volume", "hl2", "ohlc4"}

# Reference names for the multi-output indicator "groups", per target language.
_GROUP_REFS = {
    "vwap":  {"vwap":        {"pine": "vwap_v",     "py": "vwap"}},
    "macd":  {"macd_line":   {"pine": "macdLine",   "py": "macd_line"},
              "macd_signal": {"pine": "macdSignal", "py": "macd_signal"},
              "macd_hist":   {"pine": "macdHist",   "py": "macd_hist"}},
    "bb":    {"bb_basis":    {"pine": "bbBasis",    "py": "bb_basis"},
              "bb_upper":    {"pine": "bbUpper",    "py": "bb_upper"},
              "bb_lower":    {"pine": "bbLower",    "py": "bb_lower"}},
    "stoch": {"stoch_k":     {"pine": "kStoch",     "py": "stoch_k"},
              "stoch_d":     {"pine": "dStoch",     "py": "stoch_d"}},
}


class Transpiler:
    def __init__(self, lang: str) -> None:
        assert lang in ("pine", "py")
        self.lang = lang
        self.series: dict[str, str] = {}  # var -> declaration expr (insertion-ordered)
        self.groups: set[str] = set()
        self.unsupported: list[str] = []

    # ---- operand resolution -------------------------------------------------
    def operand(self, token) -> str | None:
        if token is None:
            return None
        if isinstance(token, (int, float)):
            return repr(token)
        t = str(token).strip().lower()
        if _NUM.match(t):
            return t
        if t in _PRICE:
            return self._price(t)
        for rx, fam, pine_fn, py_fn in (
            (_EMA, "ema", "ta.ema(close, {n})", "_ema(df['Close'], {n})"),
            (_SMA, "sma", "ta.sma(close, {n})", "_sma(df['Close'], {n})"),
            (_RSI, "rsi", "ta.rsi(close, {n})", "_rsi(df['Close'], {n})"),
            (_ATR, "atr", "ta.atr({n})",        "_atr(df, {n})"),
        ):
            m = rx.match(t)
            if m:
                n = m.group(1)
                var = f"{fam}_{n}"
                self.series.setdefault(
                    var, (pine_fn if self.lang == "pine" else py_fn).format(n=n)
                )
                return self._ref(var)
        if t == "vwap":
            self.groups.add("vwap")
            return self._group_ref("vwap", "vwap")
        if t in ("macd_line", "macd_signal", "macd_hist"):
            self.groups.add("macd")
            return self._group_ref("macd", t)
        if t in ("bb_upper", "bb_lower", "bb_basis"):
            self.groups.add("bb")
            return self._group_ref("bb", t)
        if t in ("stoch_k", "stoch_d"):
            self.groups.add("stoch")
            return self._group_ref("stoch", t)
        self.unsupported.append(str(token))
        return None

    def _price(self, t: str) -> str:
        if self.lang == "pine":
            return t  # close/open/high/low/volume/hl2/ohlc4 are all pine builtins
        return {
            "close": "df['Close']", "open": "df['Open']", "high": "df['High']",
            "low": "df['Low']", "volume": "df['Volume']",
            "hl2": "((df['High']+df['Low'])/2)",
            "ohlc4": "((df['Open']+df['High']+df['Low']+df['Close'])/4)",
        }[t]

    def _ref(self, var: str) -> str:
        return var if self.lang == "pine" else f"df['{var}']"

    def _group_ref(self, group: str, token: str) -> str:
        name = _GROUP_REFS[group][token][self.lang]
        return name if self.lang == "pine" else f"df['{name}']"

    # ---- expression building ------------------------------------------------
    def condition_expr(self, cond: dict) -> str | None:
        """Translate one {machine:{left,op,right}} predicate, or None if unmappable."""
        machine = (cond or {}).get("machine")
        if not isinstance(machine, dict):
            return None
        op = machine.get("op")
        if op not in VALID_OPS:
            return None
        left = self.operand(machine.get("left"))
        right = self.operand(machine.get("right"))
        if left is None or right is None:
            return None
        return self._binop(left, op, right)

    def _binop(self, left: str, op: str, right: str) -> str:
        if op == "cross_over":
            fn = "ta.crossover" if self.lang == "pine" else "_crossover"
            return f"{fn}({left}, {right})"
        if op == "cross_under":
            fn = "ta.crossunder" if self.lang == "pine" else "_crossunder"
            return f"{fn}({left}, {right})"
        return f"({left} {op} {right})"

    def rule_expr(self, conditions: list[dict], logic: str) -> tuple[str | None, list[str]]:
        """Combine a rule's conditions. Returns (expr_or_None, [unmapped_texts])."""
        exprs, unmapped = [], []
        for c in conditions or []:
            e = self.condition_expr(c)
            if e:
                exprs.append(e)
            else:
                unmapped.append(c.get("text", "(unspecified)"))
        if not exprs:
            return None, unmapped
        if self.lang == "pine":
            joiner = " and " if logic != "any" else " or "
        else:
            joiner = " & " if logic != "any" else " | "
        return joiner.join(f"({e})" for e in exprs), unmapped

    # ---- declarations -------------------------------------------------------
    def declarations(self) -> list[str]:
        return self._pine_decls() if self.lang == "pine" else self._py_decls()

    def _pine_decls(self) -> list[str]:
        lines = [f"{var} = {expr}" for var, expr in self.series.items()]
        if "vwap" in self.groups:
            lines.append("vwap_v = ta.vwap")
        if "macd" in self.groups:
            lines.append("[macdLine, macdSignal, macdHist] = ta.macd(close, 12, 26, 9)")
        if "bb" in self.groups:
            lines.append("[bbBasis, bbUpper, bbLower] = ta.bb(close, 20, 2.0)")
        if "stoch" in self.groups:
            lines.append("kStoch = ta.stoch(close, high, low, 14)")
            lines.append("dStoch = ta.sma(kStoch, 3)")
        return lines

    def _py_decls(self) -> list[str]:
        lines = [f"df['{var}'] = {expr}" for var, expr in self.series.items()]
        if "vwap" in self.groups:
            lines.append("df['vwap'] = _vwap(df)")
        if "macd" in self.groups:
            lines.append("df['macd_line'], df['macd_signal'], df['macd_hist'] = _macd(df['Close'])")
        if "bb" in self.groups:
            lines.append("df['bb_basis'], df['bb_upper'], df['bb_lower'] = _bb(df['Close'])")
        if "stoch" in self.groups:
            lines.append("df['stoch_k'], df['stoch_d'] = _stoch(df)")
        return lines
