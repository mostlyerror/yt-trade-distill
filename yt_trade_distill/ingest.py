"""Stage 1 — Ingest. Channel URL -> list of cleaned, cached transcripts.

We shell out to `yt-dlp` (no Google API key needed):
  1. `--flat-playlist --dump-single-json` to enumerate the channel's uploads.
  2. per-video `--write-subs --write-auto-subs --skip-download` to grab captions.

Everything is cached under data/<channel-slug>/ so re-runs are cheap and you can
resume after an interruption. Videos with no captions are recorded and skipped.
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
from dataclasses import dataclass, field

from .transcript import clean_vtt


@dataclass
class Video:
    id: str
    title: str
    url: str
    upload_date: str | None = None  # YYYYMMDD
    text: str = ""
    has_captions: bool = field(default=False)


def _slug(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s).strip().lower()
    return re.sub(r"[\s_-]+", "-", s) or "channel"


def _run(cmd: list[str], timeout: int = 240) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def normalize_channel_url(url: str) -> str:
    """Point a bare channel URL at its /videos tab so we enumerate uploads.

    Playlist, watch, and already-tabbed URLs are left untouched.
    """
    url = url.strip()
    if "list=" in url or "/playlist" in url or "/watch" in url:
        return url
    for tab in ("/videos", "/streams", "/shorts", "/featured", "/community"):
        if url.rstrip("/").endswith(tab):
            return url
    return url.rstrip("/") + "/videos"


def _list_attempt(url: str, cap: int) -> subprocess.CompletedProcess:
    cmd = [
        "yt-dlp", "--no-update", "--flat-playlist", "--dump-single-json",
        "--playlist-end", str(cap), url,
    ]
    return _run(cmd, timeout=300)


def list_videos(channel_url: str, cap: int) -> tuple[str, list[Video]]:
    """Return (channel_name, [Video...]) newest-first, capped at `cap` items.

    Tries the /videos uploads tab first, then falls back to the bare channel URL
    (some channels don't expose a videos tab to yt-dlp).
    """
    candidates = [normalize_channel_url(channel_url)]
    bare = channel_url.strip()
    if bare not in candidates:
        candidates.append(bare)

    proc = None
    for url in candidates:
        proc = _list_attempt(url, cap)
        if proc.returncode == 0 and proc.stdout.strip():
            break
    if proc is None or proc.returncode != 0 or not proc.stdout.strip():
        err = (proc.stderr.strip()[-500:] if proc else "no output")
        raise RuntimeError(f"yt-dlp could not list {channel_url!r}: {err}")
    data = json.loads(proc.stdout)
    channel = data.get("channel") or data.get("uploader") or data.get("title") or "channel"
    videos: list[Video] = []
    for e in data.get("entries") or []:
        if not e or not e.get("id"):
            continue
        videos.append(Video(
            id=e["id"],
            title=e.get("title", ""),
            url=e.get("url") or f"https://www.youtube.com/watch?v={e['id']}",
        ))
    return channel, videos


def _fetch_one(video: Video, raw_dir: str) -> Video:
    """Download captions + metadata for a single video into raw_dir."""
    out_tmpl = os.path.join(raw_dir, "%(id)s.%(ext)s")
    cmd = [
        "yt-dlp", "--no-update", "--skip-download",
        "--write-subs", "--write-auto-subs",
        "--sub-langs", "en.*", "--sub-format", "vtt",
        "--write-info-json",
        "-o", out_tmpl, video.url,
    ]
    _run(cmd, timeout=240)  # non-zero is fine (e.g. no subs); we detect via files

    info_path = os.path.join(raw_dir, f"{video.id}.info.json")
    if os.path.exists(info_path):
        try:
            info = json.load(open(info_path, encoding="utf-8"))
            video.upload_date = info.get("upload_date") or video.upload_date
            video.title = info.get("title") or video.title
        except (json.JSONDecodeError, OSError):
            pass

    vtts = sorted(glob.glob(os.path.join(raw_dir, f"{video.id}*.vtt")))
    # Prefer a manually-authored caption track over the auto one when both exist.
    manual = [v for v in vtts if ".orig." not in v and "-auto" not in v and not _looks_auto(v)]
    chosen = (manual or vtts or [None])[0]
    if chosen:
        try:
            video.text = clean_vtt(open(chosen, encoding="utf-8").read())
            video.has_captions = bool(video.text.strip())
        except OSError:
            video.has_captions = False
    return video


def _looks_auto(path: str) -> bool:
    # yt-dlp tags auto-captions with the bare lang code; manual subs often carry a
    # region (en-US) or a publisher name. This is a heuristic, not a guarantee.
    name = os.path.basename(path)
    return bool(re.search(r"\.en\.vtt$", name))


def ingest(channel_url: str, cap: int, workdir: str = "data") -> tuple[str, list[Video]]:
    """Enumerate + transcribe a channel, caching cleaned transcripts to disk."""
    channel, videos = list_videos(channel_url, cap)
    slug = _slug(channel)
    base = os.path.join(workdir, slug)
    raw_dir = os.path.join(base, "raw")
    tx_dir = os.path.join(base, "transcripts")
    os.makedirs(raw_dir, exist_ok=True)
    os.makedirs(tx_dir, exist_ok=True)

    kept: list[Video] = []
    for i, v in enumerate(videos, 1):
        cache = os.path.join(tx_dir, f"{v.id}.json")
        if os.path.exists(cache):
            cached = json.load(open(cache, encoding="utf-8"))
            v.text = cached.get("text", "")
            v.title = cached.get("title", v.title)
            v.upload_date = cached.get("upload_date")
            v.has_captions = bool(v.text.strip())
            print(f"  [{i}/{len(videos)}] cached: {v.title[:60]}")
        else:
            print(f"  [{i}/{len(videos)}] fetching: {v.title[:60]}")
            _fetch_one(v, raw_dir)
            json.dump(
                {"id": v.id, "title": v.title, "upload_date": v.upload_date, "text": v.text},
                open(cache, "w", encoding="utf-8"), ensure_ascii=False, indent=2,
            )
        if v.has_captions:
            kept.append(v)
        else:
            print(f"        (no captions — skipped)")

    return channel, kept
