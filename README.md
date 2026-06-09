# yt-trade-distill

Turn a **YouTube trading channel** into a **mechanical, backtestable strategy spec** —
specific enough to (try to) build a bot from. Input: a channel URL. Output: a
structured `strategy.json`, a human-readable `report.md` (every rule traced to a
verbatim source quote), a **TradingView Pine Script** strategy, and a standalone
Python backtest.

```
python -m yt_trade_distill "https://www.youtube.com/@SomeTrader" --videos 40
```

## How it works

```
channel URL
   │  yt-dlp  (no API key)
   ▼
transcripts ──► MAP: per-video extraction (LLM) ──► REDUCE: merge to one spec (LLM)
                                                          │
                          ┌───────────────────────────────┼───────────────────────┐
                          ▼                                ▼                        ▼
                   strategy.pine                      backtest.py               report.md
              (TradingView Strategy Tester)     (pandas, offline)        (read + audit the rules)
```

1. **Ingest** (`ingest.py`) — `yt-dlp` lists the channel's uploads and downloads
   captions; VTT is cleaned to plain text. Cached under `data/`.
2. **Distill** (`distill.py`) — each transcript is extracted into structured rules
   (the *map*), then all extractions are merged into one canonical spec (the
   *reduce*, hierarchical so a whole channel fits). Every rule carries an
   **evidence quote** + **confidence**; contradictions across videos are recorded,
   with the **more recent video winning** the canonical rule.
3. **Generate** (`generate_pine.py`, `generate_backtest.py`) — deterministic
   codegen (no LLM). `machine` predicates become real Pine/Python; anything else
   becomes an honest `// TODO discretionary:` comment — never a hallucinated number.
4. **Report** (`report.py`) — the readable distillation.

## LLM provider (swappable)

Extraction runs through one seam: `llm.complete(prompt) -> text`. The default
provider shells out to the local **`claude` CLI** in headless mode
(`claude -p`), which uses your existing Claude login (e.g. **Claude Max**) — no
API key, no per-token billing. It's launched with `--strict-mcp-config` so it
does **not** boot your MCP servers on every call (that startup is the difference
between seconds and minutes per call).

Swap providers via env vars (no code changes for callers):

```
YTD_LLM_PROVIDER=claude_cli   # default
YTD_MODEL=sonnet              # or opus, haiku
```

Adding an Anthropic-API or Ollama provider = one new file in `llm/` + a branch in
`llm/__init__.py:get_llm`.

## The `machine` predicate vocabulary

A condition is auto-mechanized only if it maps onto these operands; otherwise it
stays in the spec as prose and is documented, not guessed:

- price: `close open high low volume hl2 ohlc4`
- moving avg: `ema_<n>` `sma_<n>` · oscillator: `rsi_<n>`
- volatility: `atr_<n>` `bb_upper bb_lower bb_basis`
- momentum: `macd_line macd_signal macd_hist` · stochastic: `stoch_k stoch_d`
- volume-price: `vwap`
- **structure:** `swing_high_<n>` `swing_low_<n>` (confirmed pivots, e.g. `swing_high_5`)

Ops: `< > <= >= == cross_over cross_under`.

A condition's `machine` field is one of two forms:
- **comparison:** `{"left": <operand>, "op": <op>, "right": <operand>}`
- **pattern:** `{"pattern": <name>}` where name ∈ `bullish_engulfing bearish_engulfing
  hammer shooting_star close_above_prev_high close_below_prev_low`

> **Indicator- and structure-based channels** (EMA/RSI/MACD crossovers, VWAP,
> Bollinger, swing breakouts, candle-pattern entries) generate a runnable Pine
> strategy. Genuinely discretionary edge ("clean price action", visual
> zone-drawing) is preserved as traceable `// TODO discretionary:` comments —
> never fabricated into mechanical thresholds.

## Level-2 / order-flow layer (`tape_features`)

Bar data (Pine, pandas) is blind to the order book, so tape-reading rules —
Level 2 depth, time & sales, icebergs, aggressor flow — are distilled into
`tape_features` instead. Each rule is mapped to a named microstructure primitive
tagged with the **data fidelity** it needs:

- `trades` — time & sales only · `trades+quotes` — + NBBO (enables aggressor side)
- `MBP-10` — aggregated depth · `MBO` — full order-by-order (required for icebergs)

Primitives: `order_flow_imbalance trade_aggressor_ratio depth_imbalance absorption
iceberg_detection iceberg_exhaustion liquidity_sweep bid_ladder_lift large_print
relative_volume spread_filter`. Unstated thresholds become `{"value": null,
"todo": ...}` — named, not guessed. `tape_engine_stub.py` is the data-agnostic
runtime scaffold to fill in against a feed (Databento MBO, or a broker L2 API).

## Outputs (under `out/<channel-slug>/`)

| file | what |
|------|------|
| `strategy.json` | the structured distillation (the contract) |
| `report.md` | readable rules + source quotes + contradictions |
| `strategy.pine` | paste into TradingView → Pine Editor → Strategy Tester |
| `backtest.py` | `python backtest.py ohlcv.csv` (needs `pandas`, `numpy`) |
| `tape_features.json` | Level-2 / order-flow rules mapped to named microstructure primitives + the data fidelity each needs + TODO thresholds *(only if the channel reads the tape)* |
| `tape_engine_stub.py` | data-agnostic real-time engine scaffold (Feed protocol + one stub per primitive) |
| `swing/` | **free-data swing-only variant** — daily-chart system with the intraday/L2 overlay stripped out. `backtest.py` pulls free daily bars via yfinance (`python backtest.py NVDA 5y`). |
| `extractions.json` | raw per-video extractions (debug/audit) |

## Install / run

No hard Python deps for the pipeline itself (it shells to `yt-dlp` and `claude`).

```
brew install yt-dlp          # ingestion
# `claude` CLI must be installed and logged in
python -m yt_trade_distill "<channel-url>" --videos 40
# optional, to run the generated backtest:
pip install pandas numpy
python out/<slug>/backtest.py path/to/ohlcv.csv
```

## Roadmap

- ~~Structure primitives (pivots, candlestick patterns)~~ — **done.**
- ~~Parallelize the map pass~~ — **done** (`YTD_MAP_WORKERS`, default 6).
- **More structure primitives** — supply/demand zone width, break-and-retest
  sequences, multi-timeframe confirmation (these stay discretionary for now).
- **Relevance filter** — skip non-strategy videos (Q&As, vlogs) before extraction.
- Additional LLM providers (Anthropic API, Ollama).
