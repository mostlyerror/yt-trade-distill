"""Default LLM provider: the local `claude` CLI in headless mode.

`claude -p` runs non-interactively, reads the prompt from stdin, and prints the
response to stdout. It authenticates with whatever you're logged into locally —
including a Claude Max subscription — so there is NO API key and NO per-token
billing here. That is the whole point of using the CLI as the default seam.

Tuning via env: `YTD_LLM_TIMEOUT` (seconds per attempt) and `YTD_LLM_RETRIES`.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.environ.get(name) or "").strip())
    except (ValueError, TypeError):
        return default


class _Heartbeat:
    """Print an elapsed-time tick to stderr while a call is in flight.

    Only kicks in after `first` seconds, so fast calls stay silent. TTY-only —
    the carriage-return redraw would garbage up a redirected logfile. This is
    what turns a slow/contended `claude -p` from "looks hung" into "still
    working… 45s".
    """

    def __init__(self, every: int = 15, first: int = 15) -> None:
        self.every, self.first = every, first
        self._stop = threading.Event()
        self._t: threading.Thread | None = None
        self._t0 = 0.0
        self.active = sys.stderr.isatty()

    def __enter__(self) -> "_Heartbeat":
        if self.active:
            self._t0 = time.monotonic()
            self._t = threading.Thread(target=self._loop, daemon=True)
            self._t.start()
        return self

    def _loop(self) -> None:
        if self._stop.wait(self.first):
            return
        while not self._stop.is_set():
            el = int(time.monotonic() - self._t0)
            print(f"\r  ⏳ claude working… {el}s ", end="", file=sys.stderr, flush=True)
            if self._stop.wait(self.every):
                break

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._t:
            self._t.join(timeout=0.2)
        if self.active:
            print("\r" + " " * 28 + "\r", end="", file=sys.stderr, flush=True)


class ClaudeCLI:
    def __init__(self, model: str = "sonnet", timeout: int | None = None,
                 retries: int | None = None) -> None:
        # `sonnet` is the default for extraction: it's fast and cheap-on-Max, and
        # the map step is high-volume. Override per-run with --model if you want
        # opus for the harder reduce/merge pass.
        self.model = model
        # 900s/attempt: the reduce/merge step is a heavy generation that can
        # legitimately run several minutes on long-form channels — a shorter fuse
        # kills valid in-progress calls. Visibility comes from the heartbeat, not
        # a tight timeout. Tune via env (e.g. lower it for fast map-only runs).
        self.timeout = timeout if timeout is not None else _env_int("YTD_LLM_TIMEOUT", 900)
        # Concurrent `claude -p` calls (the parallel map) occasionally return a
        # transient non-zero exit; a couple of retries with backoff stops one bad
        # call from dropping an entire video's extraction.
        self.retries = retries if retries is not None else _env_int("YTD_LLM_RETRIES", 2)
        # The parallel map disables this and draws its own progress bar instead
        # (6 concurrent heartbeats would just clobber each other).
        self.heartbeat = True

    def complete(self, prompt: str, system: str | None = None) -> str:
        # The CLI has no stable "system prompt over stdin" flag across versions,
        # so we fold the system instructions into the prompt body. Simpler and
        # version-proof.
        full = prompt if system is None else f"{system}\n\n---\n\n{prompt}"
        # `--strict-mcp-config` + an empty `--mcp-config` stops the CLI from booting
        # the user's MCP servers (calendar, figma, playwright, …) on every call.
        # Without this, each invocation pays minutes of server-startup overhead —
        # crippling for a map/reduce over a whole channel.
        # `--tools ""` disables ALL built-in tools. `claude -p` is the full agentic
        # CLI: left with tools, it sometimes "helpfully" writes the spec to a file
        # (e.g. _merged_spec.json) and prints a prose summary instead of the JSON
        # we parse from stdout — which silently breaks the reduce. No tools means
        # it can only answer in text, so stdout is always the JSON we asked for.
        cmd = [
            "claude", "-p", "--model", self.model,
            "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--tools", "",
        ]
        last_err = ""
        for attempt in range(self.retries + 1):
            try:
                with (_Heartbeat() if self.heartbeat else _nullctx()):
                    proc = subprocess.run(
                        cmd, input=full, capture_output=True, text=True, timeout=self.timeout,
                    )
            except FileNotFoundError as e:  # pragma: no cover - environment guard
                raise RuntimeError(
                    "`claude` CLI not found on PATH. Install Claude Code, or set "
                    "YTD_LLM_PROVIDER to a different provider."
                ) from e
            except subprocess.TimeoutExpired:
                last_err = f"timeout after {self.timeout}s"
            else:
                if proc.returncode == 0 and proc.stdout.strip():
                    return proc.stdout.strip()
                last_err = (proc.stderr.strip() or "empty output")[:500]
            if attempt < self.retries:
                wait = 2 * (attempt + 1)  # 2s, 4s backoff
                print(f"  ⚠ claude call failed ({last_err}); retry "
                      f"{attempt + 1}/{self.retries} in {wait}s", file=sys.stderr, flush=True)
                time.sleep(wait)
        raise RuntimeError(f"claude CLI failed after {self.retries + 1} attempts: {last_err}")


class _nullctx:
    def __enter__(self): return self
    def __exit__(self, *exc): return False
