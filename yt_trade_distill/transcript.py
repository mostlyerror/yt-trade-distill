"""Turn a raw YouTube .vtt subtitle file into clean, readable transcript text.

Auto-generated YouTube captions are noisy: they carry timing cues, inline word
timestamps (`<00:00:01.480>`), `<c>` styling tags, and — worst — a "rolling"
duplication where each cue repeats the tail of the previous one. We strip the
markup and collapse the rolling duplicates so the LLM sees clean prose.
"""
from __future__ import annotations

import re

_TAG_RE = re.compile(r"<[^>]+>")
_CUE_NUM_RE = re.compile(r"^\d+$")
_HEADER_PREFIXES = ("WEBVTT", "Kind:", "Language:", "NOTE")


def clean_vtt(vtt_text: str) -> str:
    raw_lines: list[str] = []
    for line in vtt_text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(_HEADER_PREFIXES):
            continue
        if "-->" in line:  # timing line, possibly with align/position settings
            continue
        if _CUE_NUM_RE.match(line):  # bare cue index
            continue
        line = _TAG_RE.sub("", line).strip()
        if line:
            raw_lines.append(line)

    # Collapse the rolling-window duplication of auto-captions. A new line is
    # dropped if it's identical to, or fully contained in, the line we just kept.
    out: list[str] = []
    for line in raw_lines:
        if out:
            prev = out[-1]
            if line == prev or line in prev:
                continue
            # Common pattern: prev is a prefix of line (cue grew by a few words).
            if line.startswith(prev):
                out[-1] = line
                continue
        out.append(line)

    return "\n".join(out)
