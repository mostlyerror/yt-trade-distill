"""Prompts for the map (per-video extract) and reduce (merge) passes."""
from __future__ import annotations

from .schema import OPERAND_VOCAB, SCHEMA_SHAPE, TAPE_VOCAB, VALID_PATTERNS

EXTRACT_SYSTEM = (
    "You are a quantitative trading analyst. You reverse-engineer a trader's "
    "EXACT mechanical rules from how they talk on YouTube. You are ruthless "
    "about the line between a concrete, codeable rule and vague commentary. "
    "You never invent numbers. You output ONLY JSON."
)


def extract_prompt(video_title: str, video_id: str, upload_date: str | None, transcript: str) -> str:
    return f"""Below is the transcript of ONE YouTube video from a trading channel.

Video title: {video_title}
Video id: {video_id}
Upload date: {upload_date or "unknown"}

Extract every concrete, mechanical trading rule the speaker states or clearly
implies. Follow these rules strictly:

- Only record what is supported by the transcript. If they don't mention
  position sizing, leave it empty — do NOT guess.
- Attach `evidence` to every claim: the video_id above, the title, and a SHORT
  verbatim quote (<= 200 chars) from the transcript that supports it.
- Set `confidence` 0-1: 0.9+ = stated explicitly with a number; 0.5 = implied;
  0.3 = vague/uncertain.
- A rule is "mechanical" only if it could run without human judgment. Anything
  requiring discretion ("look for clean price action", "wait for confirmation",
  "if it feels right") goes in `discretionary_notes`, NOT in entry/exit rules.
- For each condition, ALSO emit a `machine` predicate IF and ONLY IF it maps
  cleanly onto this operand vocabulary. Otherwise omit `machine` (keep `text`).

Operand vocabulary for `machine` predicates:
{OPERAND_VOCAB}

A `machine` predicate takes ONE of two forms:
  1. Comparison: {{"left": <operand>, "op": <op>, "right": <operand>}}
     Valid ops: "<", ">", "<=", ">=", "==", "cross_over", "cross_under"
  2. Pattern:    {{"pattern": <pattern_name>}}
     Valid patterns: {", ".join(VALID_PATTERNS)}

Structure operands (usable as <operand> in a comparison):
  swing_high_<n>, swing_low_<n>  — the last confirmed pivot high/low using a
  lookback of <n> bars (e.g. swing_high_5). Default to n=5 unless the trader
  specifies a different pivot lookback.

Map common price-action phrasing to predicates (emit machine ONLY when it maps
this cleanly; otherwise keep text only):
  "close above the prior candle's high"            -> {{"pattern": "close_above_prev_high"}}
  "close below the prior candle's low"             -> {{"pattern": "close_below_prev_low"}}
  "bullish engulfing entry"                        -> {{"pattern": "bullish_engulfing"}}
  "bearish engulfing entry"                        -> {{"pattern": "bearish_engulfing"}}
  "hammer / pin bar (bullish)"                      -> {{"pattern": "hammer"}}
  "shooting star (bearish)"                         -> {{"pattern": "shooting_star"}}
  "break and close above the swing high"           -> {{"left": "close", "op": "cross_over", "right": "swing_high_5"}}
  "price above the most recent swing low (uptrend intact)" -> {{"left": "close", "op": ">", "right": "swing_low_5"}}
  "massive / unusual volume spike (vs average) — capitulation, climax, breakout"
        -> {{"left": "rvol_50", "op": ">", "right": <multiple the trader states, e.g. 3>}}
        (rvol_<n> = volume / N-bar average volume, computable on plain daily bars.
         If the trader gives no multiple, leave it as a tape_feature todo instead.)

ORDER-FLOW / LEVEL-2 / TAPE rules — populate `tape_features`:
Bar data cannot see the order book, so any rule about Level 2 depth, time & sales,
the tape, icebergs/hidden liquidity, bids/offers refreshing, aggressive
buyers/sellers, absorption, sweeps, or relative volume must be captured as a
`tape_features` entry (NOT as a `machine` predicate, and in addition to leaving
the human description in discretionary_notes).
Map each such rule to the CLOSEST primitive below and pick its data_requirement:
{TAPE_VOCAB}
For every threshold the trader does NOT quantify, emit a param as
{{"value": null, "todo": "<the precise question a quant must answer to set it>"}}.
Never invent a numeric threshold the trader didn't state — leave value null with a todo.
Set `gates` to one of: entry | exit | filter | sizing | timing, and `direction`
to long/short/na. Attach evidence + confidence like everything else.

Return JSON in EXACTLY this shape (omit/empty anything not present in the video):
{SCHEMA_SHAPE}

TRANSCRIPT:
\"\"\"
{transcript}
\"\"\"

Output ONLY the JSON object."""


REDUCE_SYSTEM = (
    "You are a quantitative trading analyst consolidating per-video rule "
    "extractions into ONE coherent, mechanical strategy spec for a single "
    "trader. You preserve specificity, resolve overlap, and surface conflicts. "
    "You output ONLY JSON."
)


def reduce_prompt(channel: str, partials_json: str, is_final: bool) -> str:
    scope = (
        "These are the FULL set of per-video extractions for the channel."
        if is_final else
        "These are extractions from a SUBSET of the channel's videos (one batch)."
    )
    return f"""Channel: {channel}

{scope} Merge them into one strategy spec.

Merge rules:
- DEDUPLICATE rules that say the same thing; keep the clearest wording and the
  strongest evidence quote.
- When two videos CONFLICT on the same parameter (e.g. different stop logic),
  the MORE RECENT video (later upload_date) wins the canonical field, AND you
  record both sides in `contradictions[]` with their dates. A trader's
  philosophy evolves — reflect their current thinking but keep the history.
- Keep every `machine` predicate you can; never weaken a concrete rule into prose.
- Merge `tape_features` the same way: dedupe by (primitive, gates, direction),
  keep the richest param `todo`s, preserve every distinct order-flow rule.
- Preserve `evidence` and `confidence` on the surviving rules.
- Write a tight `philosophy_summary` capturing the trader's actual edge/approach.
- Do not invent rules that no video supports.

Return JSON in EXACTLY this shape:
{SCHEMA_SHAPE}

PER-VIDEO EXTRACTIONS (JSON array):
{partials_json}

Output ONLY the merged JSON object."""
