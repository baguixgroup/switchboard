from __future__ import annotations

from .base import DEFAULT_SYSTEM, Provider, ProviderError, RunRequest, RunResult

KINDS = {}


def _kinds() -> dict:
    if not KINDS:
        from .anthropic_provider import AnthropicProvider
        from .cli_bridge import CLIProvider
        from .handoff import HandoffProvider
        from .mock import MockProvider
        from .openai_compat import OpenAICompatProvider

        KINDS.update({"anthropic": AnthropicProvider, "openai_compat": OpenAICompatProvider, "cli": CLIProvider, "handoff": HandoffProvider,
                      "mock": MockProvider})
    return KINDS


def build_providers(config) -> dict[str, Provider]:
    out: dict[str, Provider] = {}
    for name, cfg in (config.providers or {}).items():
        if not cfg or cfg.get("disabled"):
            continue
        cls = _kinds().get(cfg.get("kind", "openai_compat"))
        if cls:
            out[name] = cls(name, cfg, config)
    return out


__all__ = ["DEFAULT_SYSTEM", "Provider", "ProviderError", "RunRequest", "RunResult", "build_providers"]
