"""Stage 3c — Emit the microstructure / Level-2 layer from strategy.json.

Bar data (Pine, pandas) is blind to the order book, so tape-reading rules are
distilled into `tape_features` instead of being faked into bar logic. This
module turns that section into two artifacts:

  • tape_features.json    — a clean, runtime-oriented spec: each named
                            microstructure primitive, what it gates, the data
                            fidelity it needs, and TODO thresholds to validate.
  • tape_engine_stub.py   — a DATA-AGNOSTIC scaffold: a Feed protocol plus one
                            stub per referenced primitive. No provider wired in;
                            you drop in Databento / a broker L2 feed later.

This is the honest bridge for discretionary tape edge: it does not pretend to
know the trader's unstated thresholds — it names them and asks you to validate.
"""
from __future__ import annotations

import json

from .schema import TAPE_PRIMITIVES

_FIDELITY_RANK = {"trades": 0, "trades+quotes": 1, "MBP-10": 2, "MBO": 3}


def _max_fidelity(features: list[dict]) -> str:
    best, name = -1, "trades"
    for f in features:
        req = f.get("data_requirement") or TAPE_PRIMITIVES.get(f.get("primitive", ""), "trades")
        if _FIDELITY_RANK.get(req, 0) > best:
            best, name = _FIDELITY_RANK.get(req, 0), req
    return name


def tape_spec(spec: dict) -> dict:
    """Structured, runtime-oriented view of the tape_features section."""
    features = spec.get("tape_features") or []
    by_gate: dict[str, list] = {}
    for f in features:
        by_gate.setdefault(f.get("gates", "unspecified"), []).append(f)
    return {
        "channel": spec.get("meta", {}).get("channel", "channel"),
        "data_requirement": _max_fidelity(features),
        "feature_count": len(features),
        "primitives_used": sorted({f.get("primitive", "?") for f in features}),
        "features_by_gate": by_gate,
        "features": features,
    }


def _stub_signature(primitive: str, params: dict) -> str:
    args = ", ".join(f"{k}=None" for k in params) or ""
    extra = ", " + args if args else ""
    return f"def {primitive}(book{extra}) -> bool:"


def tape_engine_stub(spec: dict) -> str:
    features = spec.get("tape_features") or []
    if not features:
        return "# No tape_features distilled for this channel.\n"

    # Union of params per primitive, carrying the TODO text forward.
    prim_params: dict[str, dict] = {}
    for f in features:
        p = prim_params.setdefault(f.get("primitive", "unknown"), {})
        for k, v in (f.get("params") or {}).items():
            if k not in p:
                p[k] = (v or {}).get("todo", "") if isinstance(v, dict) else ""

    channel = spec.get("meta", {}).get("channel", "channel")
    fidelity = _max_fidelity(features)

    out: list[str] = []
    out.append('"""Real-time tape/Level-2 engine scaffold — auto-generated, DATA-AGNOSTIC.')
    out.append("")
    out.append(f'Distilled from the "{channel}" YouTube channel.')
    out.append(f"Minimum market-data fidelity required: {fidelity}.")
    out.append("")
    out.append("This is a SKELETON. Each primitive below is a hypothesis with TODO")
    out.append("thresholds you must set and validate on real data. Wire a feed into Feed.")
    out.append('"""')
    out.append("from __future__ import annotations")
    out.append("from typing import Protocol")
    out.append("")
    out.append("")
    out.append("class Book(Protocol):")
    out.append('    """Live order-book + tape state your feed adapter maintains."""')
    out.append("    def best_bid(self) -> float: ...")
    out.append("    def best_ask(self) -> float: ...")
    out.append("    def bid_size(self, levels: int = 1) -> float: ...")
    out.append("    def ask_size(self, levels: int = 1) -> float: ...")
    out.append("    def recent_trades(self, window_s: float) -> list: ...  # (price, size, aggressor) tuples")
    out.append("    # For MBO-level features (icebergs): per-order add/cancel/execute history.")
    out.append("    def level_events(self, price: float, window_s: float) -> list: ...")
    out.append("")
    out.append("")
    out.append("class Feed(Protocol):")
    out.append('    """Plug in Databento MBO/MBP, or a broker L2 API (DAS/IBKR/Lightspeed)."""')
    out.append("    def subscribe(self, symbol: str) -> None: ...")
    out.append("    def on_event(self, callback) -> None: ...  # callback(book: Book)")
    out.append("")
    for prim, params in prim_params.items():
        out.append("")
        out.append(_stub_signature(prim, params))
        out.append(f'    """{prim} — needs {TAPE_PRIMITIVES.get(prim, "?")} data.')
        for k, todo in params.items():
            out.append(f"    TODO param {k}: {todo}")
        out.append('    """')
        out.append("    raise NotImplementedError")
    out.append("")
    out.append("")
    out.append("# --- how the distilled features compose (gates) ---")
    for f in features:
        out.append(f"# [{f.get('gates','?')}/{f.get('direction','na')}] "
                   f"{f.get('primitive','?')}: {f.get('description','')}")
    out.append("")
    return "\n".join(out)


def write_tape_artifacts(spec: dict, out_dir: str) -> bool:
    """Write tape_features.json + tape_engine_stub.py if there are any. Returns True if written."""
    import os
    features = spec.get("tape_features") or []
    if not features:
        return False
    json.dump(tape_spec(spec), open(os.path.join(out_dir, "tape_features.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    open(os.path.join(out_dir, "tape_engine_stub.py"), "w", encoding="utf-8").write(tape_engine_stub(spec))
    return True
