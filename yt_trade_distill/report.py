"""Stage 4 — Render strategy.json into a readable Markdown distillation."""
from __future__ import annotations


def _ev(obj: dict) -> str:
    ev = (obj or {}).get("evidence") or {}
    quote = ev.get("quote", "")
    conf = obj.get("confidence")
    bits = []
    if conf is not None:
        bits.append(f"conf {conf}")
    if quote:
        bits.append(f'“{quote.strip()}”')
    if ev.get("video_id"):
        bits.append(f"[{ev['video_id']}]")
    return f"  _{' · '.join(bits)}_" if bits else ""


def _cond_line(c: dict) -> str:
    text = c.get("text", "")
    m = c.get("machine")
    if isinstance(m, dict) and m.get("op"):
        return f"`{m.get('left')} {m.get('op')} {m.get('right')}` — {text}"
    return f"{text}  ⚠️ _not mechanized_"


def generate_report(spec: dict) -> str:
    meta = spec.get("meta", {})
    L: list[str] = []
    L.append(f"# Distilled strategy — {meta.get('channel', 'channel')}")
    L.append("")
    L.append(f"*Synthesized from {meta.get('videos_analyzed', '?')} videos. "
             "Every rule is traceable to a source quote; review before trading.*")
    L.append("")
    if spec.get("philosophy_summary"):
        L.append("## Philosophy")
        L.append(spec["philosophy_summary"])
        L.append("")

    def kv(label, vals):
        if vals:
            L.append(f"- **{label}:** {', '.join(str(v) for v in vals)}")

    L.append("## Scope")
    kv("Markets", spec.get("markets"))
    kv("Instruments", spec.get("instruments"))
    kv("Timeframes", spec.get("timeframes"))
    L.append("")

    if spec.get("indicators"):
        L.append("## Indicators")
        for ind in spec["indicators"]:
            params = ", ".join(f"{k}={v}" for k, v in (ind.get("params") or {}).items())
            head = f"- **{ind.get('name', '?')}**" + (f" ({params})" if params else "")
            purpose = ind.get("purpose")
            L.append(head + (f" — {purpose}" if purpose else "") + _ev(ind))
        L.append("")

    if spec.get("filters"):
        L.append("## Filters (apply to every entry)")
        for f in spec["filters"]:
            L.append(f"- {_cond_line(f)}" + _ev(f))
        L.append("")

    if spec.get("entry_rules"):
        L.append("## Entry rules")
        for r in spec["entry_rules"]:
            logic = "ALL of" if r.get("logic", "all") != "any" else "ANY of"
            L.append(f"### {r.get('id', 'setup')} ({r.get('side', '?')}) — {logic}:")
            for c in r.get("conditions", []):
                L.append(f"- {_cond_line(c)}")
            L.append(_ev(r).strip())
            L.append("")

    ex = spec.get("exit_rules") or {}
    if ex:
        L.append("## Exits")
        sl = ex.get("stop_loss") or {}
        if sl.get("type"):
            L.append(f"- **Stop:** {sl.get('type')} = {sl.get('value', '')}" + _ev(sl))
        for tp in ex.get("take_profit") or []:
            L.append(f"- **Take profit:** {tp.get('type')} = {tp.get('value', '')} "
                     f"{tp.get('note', '')}".rstrip() + _ev(tp))
        tr = ex.get("trailing") or {}
        if tr.get("type") and tr.get("type") != "none":
            L.append(f"- **Trailing:** {tr.get('type')} = {tr.get('value', '')}" + _ev(tr))
        te = ex.get("time_exit") or {}
        if te.get("rule") and te.get("rule") != "none":
            L.append(f"- **Time exit:** {te.get('rule')}")
        for c in ex.get("signal_exit") or []:
            L.append(f"- **Signal exit:** {_cond_line(c)}")
        L.append("")

    ps = spec.get("position_sizing") or {}
    if ps.get("method"):
        L.append("## Position sizing")
        L.append(f"- **Method:** {ps.get('method')}")
        if ps.get("risk_per_trade"):
            L.append(f"- **Risk per trade:** {ps.get('risk_per_trade')}")
        if ps.get("max_concurrent"):
            L.append(f"- **Max concurrent positions:** {ps.get('max_concurrent')}")
        L.append(_ev(ps).strip())
        L.append("")

    rm = spec.get("risk_management") or {}
    if any(rm.values()):
        L.append("## Risk management")
        for k, v in rm.items():
            if v:
                L.append(f"- **{k.replace('_', ' ').title()}:** {v}")
        L.append("")

    if spec.get("discretionary_notes"):
        L.append("## ⚠️ Discretionary (not mechanizable)")
        L.append("*The trader relies on these, but they can't be coded from what was said:*")
        for d in spec["discretionary_notes"]:
            L.append(f"- {d}")
        L.append("")

    if spec.get("contradictions"):
        L.append("## Contradictions across videos")
        L.append("*Where the trader changed their mind — the more recent view wins the canonical rule above.*")
        for ct in spec["contradictions"]:
            L.append(f"- **{ct.get('topic', '?')}:**")
            for p in ct.get("positions", []):
                L.append(f"  - {p.get('date', '?')} [{p.get('video_id', '?')}]: {p.get('claim', '')}")
        L.append("")

    return "\n".join(L) + "\n"
