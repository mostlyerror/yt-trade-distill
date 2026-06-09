"""Orchestrate the pipeline: ingest -> distill -> generate -> report."""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .distill import distill
from .generate_backtest import generate_backtest
from .generate_pine import generate_pine
from .generate_tape_spec import write_tape_artifacts
from .ingest import _slug, ingest
from .llm import get_llm
from .report import generate_report
from .split import swing_readme, swing_subset


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="yt-trade-distill",
        description="Distill a YouTube trading channel into a mechanical, "
                    "backtestable strategy spec + TradingView Pine Script.",
    )
    p.add_argument("channel_url", help="YouTube channel URL (e.g. https://www.youtube.com/@SomeTrader)")
    p.add_argument("--videos", type=int, default=40,
                   help="Max videos to analyze, newest-first (default: 40). Use a big number for the whole channel.")
    p.add_argument("--model", default=None, help="LLM model override (default: env YTD_MODEL or 'sonnet').")
    p.add_argument("--out", default="out", help="Output directory (default: ./out).")
    p.add_argument("--data", default="data", help="Transcript cache directory (default: ./data).")
    p.add_argument("--no-cache", action="store_true", help="Ignore cached strategy.json and re-distill.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = p.parse_args(argv)

    llm = get_llm(model=args.model)

    print(f"▶ Ingesting up to {args.videos} videos from {args.channel_url}")
    channel, videos = ingest(args.channel_url, args.videos, workdir=args.data)
    if not videos:
        print("✗ No videos with captions found. Nothing to distill.", file=sys.stderr)
        return 1
    print(f"✓ {len(videos)} videos with transcripts for '{channel}'")

    out_dir = os.path.join(args.out, _slug(channel))
    os.makedirs(out_dir, exist_ok=True)
    spec_path = os.path.join(out_dir, "strategy.json")

    if os.path.exists(spec_path) and not args.no_cache:
        print(f"▶ Reusing cached spec {spec_path} (pass --no-cache to rebuild)")
        spec = json.load(open(spec_path, encoding="utf-8"))
    else:
        print("▶ Distilling (map per-video, then merge)…")
        spec, extractions = distill(channel, videos, llm)
        json.dump(spec, open(spec_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        json.dump(extractions, open(os.path.join(out_dir, "extractions.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)

    pine_path = os.path.join(out_dir, "strategy.pine")
    bt_path = os.path.join(out_dir, "backtest.py")
    report_path = os.path.join(out_dir, "report.md")
    open(pine_path, "w", encoding="utf-8").write(generate_pine(spec))
    open(bt_path, "w", encoding="utf-8").write(generate_backtest(spec))
    open(report_path, "w", encoding="utf-8").write(generate_report(spec))
    wrote_tape = write_tape_artifacts(spec, out_dir)

    # Free-data swing-only variant: the daily-chart system with the intraday/L2
    # overlay stripped out, so it runs on free OHLCV.
    swing = swing_subset(spec)
    swing_dir = os.path.join(out_dir, "swing")
    os.makedirs(swing_dir, exist_ok=True)
    json.dump(swing, open(os.path.join(swing_dir, "strategy.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    open(os.path.join(swing_dir, "strategy.pine"), "w", encoding="utf-8").write(generate_pine(swing))
    open(os.path.join(swing_dir, "backtest.py"), "w", encoding="utf-8").write(generate_backtest(swing))
    open(os.path.join(swing_dir, "report.md"), "w", encoding="utf-8").write(generate_report(swing))
    open(os.path.join(swing_dir, "README.md"), "w", encoding="utf-8").write(swing_readme(swing))

    print("\n✓ Done. Outputs:")
    print(f"  • {spec_path}      — structured strategy (the distillation)")
    print(f"  • {report_path}        — human-readable summary with source quotes")
    print(f"  • {pine_path}      — paste into TradingView → Pine Editor → Strategy Tester")
    print(f"  • {bt_path}        — python backtest.py <ohlcv.csv>  (needs pandas, numpy)")
    if wrote_tape:
        print(f"  • {os.path.join(out_dir, 'tape_features.json')}  — Level-2/order-flow target spec")
        print(f"  • {os.path.join(out_dir, 'tape_engine_stub.py')} — data-agnostic real-time engine scaffold")
    print(f"  • {swing_dir}/  — FREE-DATA swing-only variant (strategy.pine, backtest.py, README)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
