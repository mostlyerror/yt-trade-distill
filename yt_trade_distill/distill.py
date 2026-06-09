"""Stage 2 — Distill. Transcripts -> one canonical strategy.json via map/reduce.

map:    each video -> structured extraction (parallel-friendly, cheap)
reduce: merge extractions -> one spec. Done HIERARCHICALLY (in batches, then
        merge the batch-results) so a 200-video channel never overflows context.

Why map/reduce instead of one giant prompt? Stuffing every transcript into a
single call loses detail and can't reliably compare video #3 against video #170.
Merging structured JSON is lossless and lets the model detect contradictions.
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from .ingest import Video
from .llm import LLM
from .prompts import extract_prompt, reduce_prompt

# Conservative char budgets. ~4 chars/token, so 48k chars ≈ 12k tokens of input,
# leaving ample room in a 200k-context model for instructions + output.
_CHUNK_CHARS = 48_000
_REDUCE_BATCH = 12  # per-video extractions merged per reduce call
_MAP_WORKERS = int(os.environ.get("YTD_MAP_WORKERS", "6"))


# Top-level keys that identify one of our objects (spec / partial / per-video
# extraction). Used to pick the real object out of a decorated LLM response.
_SPEC_KEYS = frozenset({
    "philosophy_summary", "markets", "instruments", "timeframes", "indicators",
    "entry_rules", "exit_rules", "position_sizing", "filters", "risk_management",
    "tape_features", "discretionary_notes", "contradictions", "content_type",
})


def _iter_json_objects(s: str):
    """Yield every top-level JSON object in `s`, in order, via a real parser.

    `raw_decode` parses one complete value from a start index and reports where
    it ended, so we can step past it to the next candidate. Because it's an
    actual JSON parser, braces inside strings never fool us, and nested objects
    are consumed as part of their parent (only top-level objects are yielded).
    """
    dec = json.JSONDecoder()
    i, n = 0, len(s)
    while i < n:
        j = s.find("{", i)
        if j == -1:
            break
        try:
            obj, end = dec.raw_decode(s, j)
        except json.JSONDecodeError:
            i = j + 1
            continue
        if isinstance(obj, dict):
            yield obj
        i = end


def extract_json(text: str) -> dict:
    """Pull the strategy/extraction object out of an LLM response.

    Robust to how `claude -p` decorates output despite "JSON only": code fences,
    a preamble object, trailing commentary, or several fenced blocks. We collect
    every top-level object and keep the one that actually looks like our schema
    (most known keys; largest on a tie). So a stray `{"note": ...}` can't shadow
    the real spec, trailing prose is ignored, and a single clean object is
    returned unchanged. (The old find-first-brace-to-last-brace slice broke
    whenever more than one object was present — see _test_extract_json.py.)
    """
    objs = list(_iter_json_objects(text))
    if not objs:
        raise ValueError(f"No JSON object in LLM output (got {text[:120]!r}...)")

    def score(o: dict) -> tuple[int, int]:
        return (len(_SPEC_KEYS & o.keys()), len(json.dumps(o, ensure_ascii=False)))

    return max(objs, key=score)


def _chunk(text: str, size: int = _CHUNK_CHARS) -> list[str]:
    if len(text) <= size:
        return [text]
    chunks, buf, count = [], [], 0
    for para in text.split("\n"):
        if count + len(para) > size and buf:
            chunks.append("\n".join(buf))
            buf, count = [], 0
        buf.append(para)
        count += len(para) + 1
    if buf:
        chunks.append("\n".join(buf))
    return chunks


def _merge_chunk_extractions(parts: list[dict], video: Video) -> dict:
    """Combine multiple chunk-extractions from the SAME video into one record.

    Long videos get chunked; we shallow-concatenate list fields and keep the
    first non-empty scalar. The reduce pass does the real semantic merge later,
    so this only needs to avoid dropping anything.
    """
    if len(parts) == 1:
        merged = parts[0]
    else:
        merged = {}
        for p in parts:
            for k, v in p.items():
                if isinstance(v, list):
                    merged.setdefault(k, [])
                    if isinstance(merged[k], list):
                        merged[k].extend(v)
                elif isinstance(v, dict):
                    merged.setdefault(k, {})
                    if isinstance(merged[k], dict):
                        merged[k] = {**v, **merged[k]}
                elif v and not merged.get(k):
                    merged[k] = v
    merged["_video"] = {
        "id": video.id,
        "title": video.title,
        "upload_date": video.upload_date,
        "content_type": merged.get("content_type") or "other",
    }
    return merged


def build_video_index(extractions: list[dict]) -> dict:
    """Deterministic {video_id: {title, upload_date, content_type, url}} map.

    Built from the per-video extractions (NOT by the LLM), this is the authority
    the report joins each rule's `sources` against — so a rule's provenance line
    (date + content_type + title) never depends on the model getting metadata
    right. The model only ever supplies the list of supporting video_ids.
    """
    index: dict[str, dict] = {}
    for e in extractions:
        v = e.get("_video") or {}
        vid = v.get("id")
        if not vid:
            continue
        index[vid] = {
            "title": v.get("title", ""),
            "upload_date": v.get("upload_date"),
            "content_type": v.get("content_type") or e.get("content_type") or "other",
            "url": f"https://www.youtube.com/watch?v={vid}",
        }
    return index


def _map_one(i, v, llm):
    """Extract one video (chunk loop + merge). Returns a merged dict or None."""
    print(f"  [map {i}] {v.title[:60]}")
    chunks = _chunk(v.text)
    parts: list[dict] = []
    for j, c in enumerate(chunks):
        prompt = extract_prompt(v.title, v.id, v.upload_date, c)
        try:
            parts.append(extract_json(llm.complete(prompt, system=None)))
        except (ValueError, json.JSONDecodeError) as e:
            print(f"        chunk {j} skipped: {e}")
    if parts:
        return _merge_chunk_extractions(parts, v)
    return None


def map_videos(videos: list[Video], llm: LLM) -> list[dict]:
    results: list[dict | None] = [None] * len(videos)
    with ThreadPoolExecutor(max_workers=_MAP_WORKERS) as pool:
        futures = {
            pool.submit(_map_one, idx + 1, v, llm): idx
            for idx, v in enumerate(videos)
        }
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                results[idx] = fut.result()
            except Exception as e:
                print(f"        video {idx + 1} failed: {e}")
                results[idx] = None
    return [r for r in results if r is not None]


def _reduce_once(channel: str, partials: list[dict], llm: LLM, is_final: bool) -> dict:
    payload = json.dumps(partials, ensure_ascii=False)
    raw = llm.complete(reduce_prompt(channel, payload, is_final), system=None)
    return extract_json(raw)


def reduce_extractions(channel: str, extractions: list[dict], llm: LLM) -> dict:
    """Hierarchical reduce: batch -> partial specs -> final spec."""
    if not extractions:
        raise ValueError("Nothing to reduce — no usable extractions.")
    if len(extractions) <= _REDUCE_BATCH:
        return _reduce_once(channel, extractions, llm, is_final=True)

    partials: list[dict] = []
    batches = [extractions[i : i + _REDUCE_BATCH] for i in range(0, len(extractions), _REDUCE_BATCH)]
    for i, batch in enumerate(batches, 1):
        print(f"  [reduce L1 {i}/{len(batches)}]")
        partials.append(_reduce_once(channel, batch, llm, is_final=False))
    print(f"  [reduce L2 final] merging {len(partials)} partials")
    return _reduce_once(channel, partials, llm, is_final=True)


def distill(channel: str, videos: list[Video], llm: LLM) -> tuple[dict, list[dict]]:
    """Run the full map/reduce. Returns (canonical_spec, per_video_extractions)."""
    extractions = map_videos(videos, llm)
    spec = reduce_extractions(channel, extractions, llm)

    # Provenance layer: a deterministic video roster the report joins rule
    # `sources` against, plus a content-type breakdown of what was analyzed.
    video_index = build_video_index(extractions)
    spec["video_index"] = video_index
    breakdown: dict[str, int] = {}
    for info in video_index.values():
        ct = info.get("content_type") or "other"
        breakdown[ct] = breakdown.get(ct, 0) + 1

    spec.setdefault("meta", {})
    spec["meta"]["channel"] = channel
    spec["meta"]["videos_analyzed"] = len(extractions)
    spec["meta"]["content_type_breakdown"] = breakdown
    return spec, extractions
