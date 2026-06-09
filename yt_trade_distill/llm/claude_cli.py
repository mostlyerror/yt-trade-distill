"""Default LLM provider: the local `claude` CLI in headless mode.

`claude -p` runs non-interactively, reads the prompt from stdin, and prints the
response to stdout. It authenticates with whatever you're logged into locally —
including a Claude Max subscription — so there is NO API key and NO per-token
billing here. That is the whole point of using the CLI as the default seam.
"""
from __future__ import annotations

import subprocess
import time


class ClaudeCLI:
    def __init__(self, model: str = "sonnet", timeout: int = 900, retries: int = 2) -> None:
        # `sonnet` is the default for extraction: it's fast and cheap-on-Max, and
        # the map step is high-volume. Override per-run with --model if you want
        # opus for the harder reduce/merge pass.
        self.model = model
        self.timeout = timeout
        # Concurrent `claude -p` calls (the parallel map) occasionally return a
        # transient non-zero exit; a couple of retries with backoff stops one bad
        # call from dropping an entire video's extraction.
        self.retries = retries

    def complete(self, prompt: str, system: str | None = None) -> str:
        # The CLI has no stable "system prompt over stdin" flag across versions,
        # so we fold the system instructions into the prompt body. Simpler and
        # version-proof.
        full = prompt if system is None else f"{system}\n\n---\n\n{prompt}"
        # `--strict-mcp-config` + an empty `--mcp-config` stops the CLI from booting
        # the user's MCP servers (calendar, figma, playwright, …) on every call.
        # Without this, each invocation pays minutes of server-startup overhead —
        # crippling for a map/reduce over a whole channel.
        cmd = [
            "claude", "-p", "--model", self.model,
            "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        ]
        last_err = ""
        for attempt in range(self.retries + 1):
            try:
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
                time.sleep(2 * (attempt + 1))  # 2s, 4s backoff
        raise RuntimeError(f"claude CLI failed after {self.retries + 1} attempts: {last_err}")
