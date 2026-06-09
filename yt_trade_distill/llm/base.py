"""The single seam every LLM provider must satisfy.

Keeping the contract to one method (`complete`) is deliberate: the rest of the
pipeline never imports a provider directly, so swapping Claude-CLI for the
Anthropic API, Ollama, or anything else is a new file + an env var — never a
rewrite of the distill logic.
"""
from __future__ import annotations

from typing import Protocol


class LLM(Protocol):
    def complete(self, prompt: str, system: str | None = None) -> str:
        """Return the model's text response for `prompt` (optionally steered by `system`)."""
        ...
