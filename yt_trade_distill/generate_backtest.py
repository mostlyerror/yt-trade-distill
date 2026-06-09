"""Stage 3b — Generate a standalone Python backtest from strategy.json.

Same transpiler as the Pine generator (so the two can't disagree), but targets a
self-contained pandas/numpy script: load an OHLCV CSV, compute the indicators,
run a simple bar-by-bar simulation with risk-based sizing + stop/TP, print stats.

The output has NO dependency on this package — it's a portable script you can run
anywhere with `pip install pandas numpy`.
"""
from __future__ import annotations

import json

from .generate_pine import _num
from .transpile import Transpiler

_HELPERS = '''\
import sys
import json
import numpy as np
import pandas as pd

# ---- indicator helpers (match the Pine ta.* semantics closely) ----
def _ema(s, n): return s.ewm(span=n, adjust=False).mean()
def _sma(s, n): return s.rolling(n).mean()
def _rsi(s, n):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)
def _atr(df, n):
    h, l, c = df["High"], df["Low"], df["Close"]
    pc = c.shift()
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()
def _vwap(df):
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    return (tp * df["Volume"]).cumsum() / df["Volume"].cumsum()
def _macd(c):
    line = _ema(c, 12) - _ema(c, 26); sig = _ema(line, 9); return line, sig, line - sig
def _bb(c, n=20, k=2.0):
    basis = _sma(c, n); dev = k * c.rolling(n).std(); return basis, basis + dev, basis - dev
def _stoch(df, n=14, d=3):
    ll = df["Low"].rolling(n).min(); hh = df["High"].rolling(n).max()
    k = 100 * (df["Close"] - ll) / (hh - ll); return k, k.rolling(d).mean()
def _crossover(a, b):  return (a > b) & (a.shift() <= b.shift())
def _crossunder(a, b): return (a < b) & (a.shift() >= b.shift())
'''


def _sig(exprs: list[str], default: str) -> str:
    return " | ".join(f"({e})" for e in exprs) if exprs else default


def generate_backtest(spec: dict) -> str:
    t = Transpiler("py")
    long_rules, short_rules = [], []
    for r in spec.get("entry_rules") or []:
        expr, _ = t.rule_expr(r.get("conditions", []), r.get("logic", "all"))
        if expr:
            (long_rules if r.get("side") == "long" else short_rules).append(expr)

    filt_exprs = [e for f in (spec.get("filters") or []) if (e := t.condition_expr(f))]
    exit_exprs = [e for c in ((spec.get("exit_rules") or {}).get("signal_exit") or [])
                  if (e := t.condition_expr(c))]

    # Stop distance + TP, mirroring the Pine generator's logic.
    sl = (spec.get("exit_rules") or {}).get("stop_loss") or {}
    sl_kind = (sl.get("type") or "none").lower()
    tps = (spec.get("exit_rules") or {}).get("take_profit") or []
    tp = tps[0] if tps else {}
    tp_kind = (tp.get("type") or "").lower()

    if sl_kind == "atr":
        length = int((sl.get("params") or {}).get("atr_length", 14))
        atr_ref = t.operand(f"atr_{length}")
        sldist_expr = f"{_num(sl.get('value'), 1.5)} * {atr_ref}"
    elif sl_kind == "fixed_pct":
        sldist_expr = f"df['Close'] * {_num(sl.get('value'), 1.0) / 100.0}"
    else:
        sldist_expr = "df['Close'] * 0.02  # no mechanical stop found; default 2%"

    if tp_kind == "rr":
        tp_mult, tp_mode = _num(tp.get("value"), 2.0), "rr"
    elif tp_kind == "fixed_pct":
        tp_mult, tp_mode = _num(tp.get("value"), 2.0) / 100.0, "pct"
    else:
        tp_mult, tp_mode = 0.0, "none"

    decls = t.declarations()  # AFTER all operands resolved
    long_sig = _sig(long_rules, "pd.Series(False, index=df.index)")
    short_sig = _sig(short_rules, "pd.Series(False, index=df.index)")
    filt = _sig(filt_exprs, "pd.Series(True, index=df.index)")
    exit_sig = _sig(exit_exprs, "pd.Series(False, index=df.index)")

    channel = spec.get("meta", {}).get("channel", "channel")
    decl_block = "\n".join(decls) if decls else "# (no indicators referenced)"

    return f'''\
"""Backtest auto-distilled from the "{channel}" YouTube channel.

Usage:  python backtest.py path/to/ohlcv.csv
CSV must have columns: Date, Open, High, Low, Close, Volume
"""
{_HELPERS}

RISK_PER_TRADE = 0.01      # fraction of equity risked per trade
START_EQUITY   = 10_000.0
COMMISSION     = 0.0005    # 5 bps per side


def load(path):
    df = pd.read_csv(path)
    df.columns = [c.capitalize() for c in df.columns]
    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date")
    return df.sort_index()


def run(df):
    # --- indicators ---
{_indent(decl_block, 4)}
    df["slDist"] = {sldist_expr}

    longSignal  = ({long_sig}) & ({filt})
    shortSignal = ({short_sig}) & ({filt})
    exitSignal  = {exit_sig}

    equity = START_EQUITY
    pos = 0          # +1 long, -1 short
    entry = stop = tp = qty = 0.0
    trades, curve = [], []
    tp_mode, tp_mult = "{tp_mode}", {tp_mult}

    for i in range(len(df)):
        o, h, l, c = (df["Open"].iat[i], df["High"].iat[i],
                      df["Low"].iat[i], df["Close"].iat[i])
        if pos != 0:
            exit_px = None
            if pos > 0:
                if l <= stop: exit_px = stop
                elif tp and h >= tp: exit_px = tp
                elif bool(exitSignal.iat[i]): exit_px = c
            else:
                if h >= stop: exit_px = stop
                elif tp and l <= tp: exit_px = tp
                elif bool(exitSignal.iat[i]): exit_px = c
            if exit_px is not None:
                pnl = (exit_px - entry) * qty * pos
                pnl -= COMMISSION * (abs(entry) + abs(exit_px)) * qty
                equity += pnl
                trades.append(pnl)
                pos = 0

        if pos == 0 and i < len(df) - 1:
            sld = float(df["slDist"].iat[i])
            go_long = bool(longSignal.iat[i]) and sld > 0
            go_short = bool(shortSignal.iat[i]) and sld > 0
            if go_long or go_short:
                pos = 1 if go_long else -1
                entry = c
                qty = (equity * RISK_PER_TRADE) / sld
                stop = entry - pos * sld
                if tp_mode == "rr":
                    tp = entry + pos * tp_mult * sld
                elif tp_mode == "pct":
                    tp = entry * (1 + pos * tp_mult)
                else:
                    tp = 0.0
        curve.append(equity)

    return _stats(trades, curve)


def _stats(trades, curve):
    curve = pd.Series(curve)
    wins = [t for t in trades if t > 0]
    peak = curve.cummax()
    dd = ((curve - peak) / peak).min() if len(curve) else 0.0
    gross_win = sum(t for t in trades if t > 0)
    gross_loss = -sum(t for t in trades if t < 0)
    return {{
        "trades": len(trades),
        "win_rate": round(len(wins) / len(trades), 3) if trades else 0.0,
        "net_profit": round(curve.iloc[-1] - START_EQUITY, 2) if len(curve) else 0.0,
        "return_pct": round((curve.iloc[-1] / START_EQUITY - 1) * 100, 2) if len(curve) else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else float("inf"),
        "max_drawdown_pct": round(dd * 100, 2),
    }}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python backtest.py path/to/ohlcv.csv")
    stats = run(load(sys.argv[1]))
    print(json.dumps(stats, indent=2))
'''


def _indent(block: str, n: int) -> str:
    pad = " " * n
    return "\n".join(pad + line if line else line for line in block.splitlines())
