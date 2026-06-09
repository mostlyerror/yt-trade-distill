"""Provider factory. `get_llm()` is the only thing the pipeline imports."""
from __future__ import annotations

import os

from .base import LLM
from .claude_cli import ClaudeCLI

__all__ = ["LLM", "get_llm"]


def get_llm(model: str | None = None) -> LLM:
    """Return the configured LLM provider.

    Selection is env-driven so future providers slot in without touching callers:
      YTD_LLM_PROVIDER  -> "claude_cli" (default)
      YTD_MODEL         -> default model for the chosen provider
    """
    provider = os.environ.get("YTD_LLM_PROVIDER", "claude_cli")
    model = model or os.environ.get("YTD_MODEL", "sonnet")

    if provider == "claude_cli":
        return ClaudeCLI(model=model)

    # Future: "anthropic_api" (needs ANTHROPIC_API_KEY), "ollama", etc.
    raise ValueError(
        f"Unknown YTD_LLM_PROVIDER={provider!r}. Supported: claude_cli."
    )
