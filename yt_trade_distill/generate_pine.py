"""Stage 3a — Generate a TradingView Pine Script v6 strategy from strategy.json.

Deterministic (no LLM): the same spec always yields the same script. Mechanizable
rules become real Pine; everything else becomes a `// TODO discretionary:` comment
so the trader can see exactly what the system could and couldn't automate.

Paste the output into TradingView's Pine Editor → Add to chart → Strategy Tester
gives you the visual backtest (trade markers, equity curve, win rate, drawdown).
"""
from __future__ import annotations

from .transpile import Transpiler


def _q(s: str) -> str:
    return str(s).replace('"', "'")


def _stop_block(spec: dict, t: Transpiler, notes: list[str]) -> tuple[list[str], bool, str | None]:
    """Emit stop-distance pine. Returns (lines, has_stop, sl_kind)."""
    sl = (spec.get("exit_rules") or {}).get("stop_loss") or {}
    kind = (sl.get("type") or "none").lower()
    lines: list[str] = []
    if kind == "atr":
        mult = _num(sl.get("value"), 1.5)
        length = int((sl.get("params") or {}).get("atr_length", 14))
        atr_ref = t.operand(f"atr_{length}")  # registers the ATR series
        lines.append(f"slMult = {mult}")
        lines.append(f"slDistEntry = slMult * {atr_ref}")
        lines.append(f"longStop = strategy.position_avg_price - slMult * {atr_ref}")
        lines.append(f"shortStop = strategy.position_avg_price + slMult * {atr_ref}")
        return lines, True, "atr"
    if kind == "fixed_pct":
        pct = _num(sl.get("value"), 1.0) / 100.0
        lines.append(f"slPerc = {pct}")
        lines.append("slDistEntry = close * slPerc")
        lines.append("longStop = strategy.position_avg_price * (1 - slPerc)")
        lines.append("shortStop = strategy.position_avg_price * (1 + slPerc)")
        return lines, True, "fixed_pct"
    if kind not in ("none", ""):
        notes.append(f"stop_loss: '{sl.get('type')}' ({sl.get('value','')}) — not auto-mechanized")
    return lines, False, None


def _tp_block(spec: dict, sl_kind: str | None, notes: list[str]) -> list[str]:
    tps = (spec.get("exit_rules") or {}).get("take_profit") or []
    if not tps:
        return []
    tp = tps[0]
    kind = (tp.get("type") or "").lower()
    if kind == "rr":
        rr = _num(tp.get("value"), 2.0)
        return [
            f"rr = {rr}",
            "longTP = strategy.position_avg_price + rr * slDistEntry",
            "shortTP = strategy.position_avg_price - rr * slDistEntry",
        ]
    if kind == "fixed_pct":
        pct = _num(tp.get("value"), 2.0) / 100.0
        return [
            f"tpPerc = {pct}",
            "longTP = strategy.position_avg_price * (1 + tpPerc)",
            "shortTP = strategy.position_avg_price * (1 - tpPerc)",
        ]
    notes.append(f"take_profit: '{tp.get('type')}' ({tp.get('value','')}) — not auto-mechanized")
    return []


def _num(v, default: float) -> float:
    try:
        return float(str(v).strip().replace("%", "").split()[0])
    except (TypeError, ValueError, IndexError):
        return default


def generate_pine(spec: dict) -> str:
    t = Transpiler("pine")
    notes: list[str] = []

    # --- entry signals, grouped by side ---
    long_rules, short_rules = [], []
    for r in spec.get("entry_rules") or []:
        expr, unmapped = t.rule_expr(r.get("conditions", []), r.get("logic", "all"))
        for u in unmapped:
            notes.append(f"entry[{r.get('id', '?')}] condition: {u}")
        if expr:
            (long_rules if r.get("side") == "long" else short_rules).append(expr)

    # --- filters (AND-gate every entry) ---
    filt_exprs = []
    for f in spec.get("filters") or []:
        e = t.condition_expr(f)
        if e:
            filt_exprs.append(e)
        else:
            notes.append(f"filter: {f.get('text', '(unspecified)')}")

    # --- signal exits ---
    exit_exprs = []
    for c in (spec.get("exit_rules") or {}).get("signal_exit", []) or []:
        e = t.condition_expr(c)
        if e:
            exit_exprs.append(e)
        else:
            notes.append(f"signal_exit: {c.get('text', '(unspecified)')}")

    stop_lines, has_stop, sl_kind = _stop_block(spec, t, notes)
    tp_lines = _tp_block(spec, sl_kind, notes)
    has_tp = bool(tp_lines)

    filt_clause = " and ".join(f"({e})" for e in filt_exprs) or "true"
    long_sig = " or ".join(long_rules) if long_rules else "false"
    short_sig = " or ".join(short_rules) if short_rules else "false"

    meta = spec.get("meta", {})
    channel = meta.get("channel", "channel")

    out: list[str] = []
    out.append("//@version=6")
    out.append(f'// Auto-distilled from the "{_q(channel)}" YouTube channel by yt-trade-distill.')
    out.append(f"// Videos analyzed: {meta.get('videos_analyzed', '?')}")
    out.append("// Review every rule against the source quotes in report.md before trading real size.")
    summary = spec.get("philosophy_summary", "")
    if summary:
        out.append(f"// Philosophy: {_q(summary)}")
    out.append("")
    out.append(f'strategy("{_q(channel)} — distilled", overlay=true, '
               "default_qty_type=strategy.percent_of_equity, default_qty_value=100, "
               "commission_type=strategy.commission.percent, commission_value=0.05, "
               "calc_on_every_tick=false)")
    out.append("")
    out.append("// === Inputs ===")
    out.append('allowLong  = input.bool(true,  "Allow longs")')
    out.append('allowShort = input.bool(true,  "Allow shorts")')
    out.append('riskPerc   = input.float(1.0,  "Risk % of equity per trade", minval=0.01) / 100.0')
    out.append("")

    decls = t.declarations()  # called AFTER all operands are resolved
    if decls:
        out.append("// === Indicators ===")
        out.extend(decls)
        out.append("")

    out.append("// === Signals ===")
    out.append(f"filterOK   = {filt_clause}")
    out.append(f"longSignal  = ({long_sig}) and filterOK and allowLong")
    out.append(f"shortSignal = ({short_sig}) and filterOK and allowShort")
    out.append("")

    if stop_lines:
        out.append("// === Risk / exits ===")
        out.extend(stop_lines)
        out.extend(tp_lines)
        # Risk-based position sizing: size so a stop-out loses exactly riskPerc of equity.
        out.append("qtyLong  = slDistEntry > 0 ? (strategy.equity * riskPerc) / slDistEntry : na")
        out.append("qtyShort = slDistEntry > 0 ? (strategy.equity * riskPerc) / slDistEntry : na")
        out.append("")
        out.append("if longSignal and strategy.position_size == 0")
        out.append('    strategy.entry("Long", strategy.long, qty=qtyLong)')
        out.append("if shortSignal and strategy.position_size == 0")
        out.append('    strategy.entry("Short", strategy.short, qty=qtyShort)')
        out.append("")
        limit_long = "limit=longTP" if has_tp else ""
        limit_short = "limit=shortTP" if has_tp else ""
        out.append("if strategy.position_size > 0")
        out.append(f'    strategy.exit("Long Exit", "Long", stop=longStop{", " + limit_long if limit_long else ""})')
        out.append("if strategy.position_size < 0")
        out.append(f'    strategy.exit("Short Exit", "Short", stop=shortStop{", " + limit_short if limit_short else ""})')
    else:
        out.append("// === Entries (no mechanical stop found — using default equity sizing) ===")
        out.append("if longSignal and strategy.position_size == 0")
        out.append('    strategy.entry("Long", strategy.long)')
        out.append("if shortSignal and strategy.position_size == 0")
        out.append('    strategy.entry("Short", strategy.short)')
    out.append("")

    if exit_exprs:
        out.append("// === Signal-based exits ===")
        out.append(f"longExitSig  = {' or '.join(exit_exprs)}")
        out.append("if strategy.position_size > 0 and longExitSig")
        out.append('    strategy.close("Long", comment="signal exit")')
        out.append("if strategy.position_size < 0 and longExitSig")
        out.append('    strategy.close("Short", comment="signal exit")')
        out.append("")

    discretionary = list(notes) + list(spec.get("discretionary_notes") or [])
    if discretionary:
        out.append("// === NOT mechanized — requires trader discretion / outside the supported vocabulary ===")
        for d in discretionary:
            out.append(f"// TODO discretionary: {_q(d)}")

    return "\n".join(out) + "\n"
