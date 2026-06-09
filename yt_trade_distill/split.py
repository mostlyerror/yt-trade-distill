"""Split a full strategy spec into a FREE-DATA swing-only variant.

A trader like @TheOneLanceB runs two things at once: a daily-chart SWING system
(structure breakouts, capitulation reversals) and an intraday TAPE overlay
(Level 2, icebergs, aggressor flow). The swing system needs only daily OHLCV —
which is free — while the tape overlay needs realtime/L2 data that costs money
and, for icebergs, a professional license.

`swing_subset` isolates the part you can actually deploy on free data:
  • keep entry rules that have ≥1 bar-mechanizable condition (those run on OHLCV)
  • keep bar-based exits/filters/sizing/risk
  • keep `trades`-fidelity tape features (e.g. relative-volume capitulation) as
    `free_data_features` — they ARE computable on free daily bars
  • drop everything needing trades+quotes / MBP-10 / MBO into
    `excluded_intraday_features`, recorded so nothing silently disappears
"""
from __future__ import annotations

from .transpile import Transpiler

_FREE_FIDELITY = "trades"  # the only tape fidelity satisfiable by free daily bars


def _rule_is_bar_deployable(rule: dict) -> bool:
    """True if any of the rule's conditions transpile to real bar logic."""
    expr, _ = Transpiler("pine").rule_expr(rule.get("conditions", []), rule.get("logic", "all"))
    return expr is not None


def swing_subset(spec: dict) -> dict:
    swing: dict = {"meta": {**(spec.get("meta") or {}), "variant": "swing_free_data"}}
    swing["philosophy_summary"] = spec.get("philosophy_summary", "")
    for k in ("markets", "instruments", "timeframes", "indicators",
              "position_sizing", "risk_management", "discretionary_notes"):
        if k in spec:
            swing[k] = spec[k]

    swing["entry_rules"] = [r for r in spec.get("entry_rules", []) if _rule_is_bar_deployable(r)]
    swing["filters"] = spec.get("filters", [])

    # Exits: keep everything except tape-only signal exits (no bar machine).
    ex = dict(spec.get("exit_rules") or {})
    bar = Transpiler("pine")
    ex["signal_exit"] = [c for c in (ex.get("signal_exit") or []) if bar.condition_expr(c)]
    swing["exit_rules"] = ex

    feats = spec.get("tape_features") or []
    swing["free_data_features"] = [
        f for f in feats if (f.get("data_requirement") or "trades") == _FREE_FIDELITY
    ]
    swing["excluded_intraday_features"] = [
        {"primitive": f.get("primitive"), "gates": f.get("gates"),
         "data_requirement": f.get("data_requirement"), "description": f.get("description")}
        for f in feats if (f.get("data_requirement") or "trades") != _FREE_FIDELITY
    ]
    return swing


def swing_readme(swing: dict) -> str:
    chan = swing.get("meta", {}).get("channel", "channel")
    n_entry = len(swing.get("entry_rules", []))
    n_free = len(swing.get("free_data_features", []))
    n_excl = len(swing.get("excluded_intraday_features", []))
    excl = "\n".join(
        f"  - {f['primitive']} ({f.get('data_requirement')}): {(f.get('description') or '')[:90]}"
        for f in swing.get("excluded_intraday_features", [])
    ) or "  (none)"
    return f"""# {chan} — swing strategy (free data)

The daily-chart swing system only, isolated from the intraday Level-2 / tape
overlay. Runs on FREE daily OHLCV — no paid feed, no order-book data.

- {n_entry} bar-mechanized entry rule(s)
- {n_free} free-data feature(s) (trades fidelity, e.g. relative-volume capitulation)
- {n_excl} intraday/L2 feature(s) EXCLUDED (need paid realtime / MBO):
{excl}

## Run the backtest on free data

```
pip install pandas numpy yfinance
python backtest.py AAPL 2y        # pulls free daily bars via yfinance
python backtest.py data.csv       # or your own OHLCV CSV
```

## TradingView

Paste `strategy.pine` into the Pine Editor → Strategy Tester (daily chart).
"""
