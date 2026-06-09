"""The canonical strategy spec — the contract between distillation and codegen.

Two ideas make this "specific enough to build a bot from":

1. EVERY claim carries `evidence` (video id + verbatim quote) and `confidence`
   (0-1). Nothing is asserted without a source. Vague channel talk lands in
   `discretionary_notes`, never as a hallucinated number.

2. Conditions carry an optional `machine` predicate in a tiny fixed grammar.
   When present, the Pine/Python generators transpile it to real, compiling
   code. When absent, the rule degrades to a documented comment — honest about
   what could and couldn't be mechanized.

The `machine` predicate grammar
-------------------------------
A condition's `machine` field takes ONE of two forms:

  1. Comparison: {"left": <operand>, "op": <op>, "right": <operand>}
  2. Pattern:    {"pattern": <pattern_name>}

  <op>      one of:  "<"  ">"  "<="  ">="  "=="  "cross_over"  "cross_under"
  <operand> a number (e.g. 30, 1.5) OR one of the canonical tokens below.
  <pattern> one of VALID_PATTERNS — a self-contained price-action predicate
            (candlestick / structure break) needing no left/op/right.

Canonical operand vocabulary (case-insensitive)
-----------------------------------------------
  price:        close open high low volume hl2 ohlc4
  moving avg:   ema_<n>  sma_<n>            e.g. ema_20, sma_200
  oscillators:  rsi_<n>                     e.g. rsi_14
  volatility:   atr_<n>  bb_upper bb_lower bb_basis
  momentum:     macd_line macd_signal macd_hist
  stochastic:   stoch_k stoch_d
  volume-px:    vwap
  structure:    swing_high_<n>  swing_low_<n>   e.g. swing_high_5, swing_low_5

Anything outside this vocabulary is allowed in the human `text` of a condition,
but should be left out of `machine` so the generators don't emit broken code.
"""
from __future__ import annotations

# The operand tokens the transpiler understands. Kept here so prompts and codegen
# can't drift apart — both import from this single source of truth.
OPERAND_VOCAB = """
price:      close, open, high, low, volume, hl2, ohlc4
moving avg: ema_<n>, sma_<n>            (e.g. ema_20, sma_200)
oscillator: rsi_<n>                     (e.g. rsi_14)
volatility: atr_<n>, bb_upper, bb_lower, bb_basis
momentum:   macd_line, macd_signal, macd_hist
stochastic: stoch_k, stoch_d
volume-px:  vwap
structure:  swing_high_<n>, swing_low_<n>   (e.g. swing_high_5, swing_low_5)
numbers:    any literal number (30, 1.5, 0.02)
""".strip()

VALID_OPS = ("<", ">", "<=", ">=", "==", "cross_over", "cross_under")

# Self-contained price-action predicates usable as {"pattern": <name>}.
VALID_PATTERNS = (
    "bullish_engulfing",
    "bearish_engulfing",
    "hammer",
    "shooting_star",
    "close_above_prev_high",
    "close_below_prev_low",
)

# The exact JSON shape the model must return (shown to it verbatim in the prompt).
SCHEMA_SHAPE = """
{
  "philosophy_summary": "2-4 sentence plain-English summary of how this trader thinks about markets and edge.",
  "markets": ["e.g. crypto, US equities, forex, futures"],
  "instruments": ["specific tickers/pairs they trade, if named"],
  "timeframes": ["e.g. 5m, 1h, daily — chart timeframes they actually use"],
  "indicators": [
    {"name": "EMA", "params": {"length": 20}, "purpose": "trend filter",
     "evidence": {"video_id": "...", "title": "...", "quote": "verbatim words"},
     "confidence": 0.0}
  ],
  "entry_rules": [
    {"id": "long_pullback", "side": "long",
     "logic": "all",                      // "all" = AND, "any" = OR across conditions
     "conditions": [
       {"text": "price pulls back to the 20 EMA in an uptrend",
        "machine": {"left": "close", "op": "<=", "right": "ema_20"}},
       {"text": "RSI above 50 (trend intact)",
        "machine": {"left": "rsi_14", "op": ">", "right": 50}},
       {"text": "candle closes above the prior candle's high (price-action trigger)",
        "machine": {"pattern": "close_above_prev_high"}},
       {"text": "price breaks and closes above the recent swing high",
        "machine": {"left": "close", "op": "cross_over", "right": "swing_high_5"}}
     ],
     "evidence": {"video_id": "...", "title": "...", "quote": "..."},
     "confidence": 0.0}
  ],
  "exit_rules": {
    "stop_loss":  {"type": "atr|fixed_pct|structure|none", "value": "1.5", "params": {"atr_length": 14},
                   "evidence": {"video_id": "...", "title": "...", "quote": "..."}, "confidence": 0.0},
    "take_profit": [
      {"type": "rr|fixed_pct|target|structure", "value": "2.0", "note": "2R target",
       "evidence": {"video_id": "...", "title": "...", "quote": "..."}, "confidence": 0.0}
    ],
    "trailing":   {"type": "atr|pct|none", "value": "", "evidence": {}, "confidence": 0.0},
    "time_exit":  {"rule": "close at session end / after N bars / none", "evidence": {}, "confidence": 0.0},
    "signal_exit": [
      {"text": "exit long when price closes below 20 EMA",
       "machine": {"left": "close", "op": "cross_under", "right": "ema_20"}}
    ]
  },
  "position_sizing": {"method": "fixed_risk|fixed_fractional|fixed_units|martingale|kelly|unknown",
                      "risk_per_trade": "e.g. 1% of account", "max_concurrent": 1,
                      "evidence": {"video_id": "...", "title": "...", "quote": "..."}, "confidence": 0.0},
  "filters": [
    {"name": "trend filter", "text": "only long above the 200 EMA",
     "machine": {"left": "close", "op": ">", "right": "sma_200"},
     "evidence": {"video_id": "...", "title": "...", "quote": "..."}, "confidence": 0.0}
  ],
  "risk_management": {"max_daily_loss": "", "max_drawdown": "", "max_trades_per_day": "", "notes": ""},
  "discretionary_notes": ["Rules the trader uses that are real but NOT mechanizable from what they said."],
  "contradictions": [
    {"topic": "stop placement",
     "positions": [{"video_id": "...", "date": "YYYYMMDD", "claim": "..."}]}
  ]
}
""".strip()
