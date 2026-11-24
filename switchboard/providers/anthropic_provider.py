"""Claude through the official Anthropic SDK (manual tool loop, effort, refusal fallbacks)."""
from __future__ import annotations

import asyncio

import anthropic

from ..toolbox import ToolBox
from .base import Provider, ProviderError, RunRequest, RunResult, image_data_url

FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _map_error(exc: Exception) -> ProviderError:
    msg = getattr(exc, "message", None) or str(exc)
    if isinstance(exc, anthropic.RateLimitError):
        return ProviderError(f"Rate limited: {msg}", "rate_limit", retryable=True)
    if isinstance(exc, anthropic.AuthenticationError):
        return ProviderError(f"Authentication failed: {msg}", "auth")
    if isinstance(exc, anthropic.PermissionDeniedError):
        return ProviderError(f"Permission denied: {msg}", "auth")
    if isinstance(exc, anthropic.NotFoundError):
        return ProviderError(f"Model not found: {msg}", "not_found")
    if isinstance(exc, anthropic.BadRequestError):
        low = msg.lower()
        if "credit balance" in low or "billing" in low:
            return ProviderError(f"Out of credit: {msg}", "credit")
        return ProviderError(f"Bad request: {msg}", "bad_request")
    if isinstance(exc, anthropic.APIStatusError):
        if exc.status_code >= 500:
            return ProviderError(f"Anthropic error {exc.status_code}: {msg}", "unavailable", retryable=True)
        return ProviderError(f"Anthropic error {exc.status_code}: {msg}")
    if isinstance(exc, anthropic.APIConnectionError):
        return ProviderError(f"Cannot reach Anthropic: {msg}", "unavailable", retryable=True)
    return ProviderError(msg)


class AnthropicProvider(Provider):
    kind = "anthropic"

    @property
    def api_key(self) -> str | None:
        return self.config.secret(self.cfg.get("api_key_env", "ANTHROPIC_API_KEY"))

    def available(self) -> bool:
        from ..security import protected_mode
        if protected_mode(self.config):
            return False
        return bool(self.api_key)

    def client(self) -> anthropic.AsyncAnthropic:
        from ..security import require_execution_enabled
        require_execution_enabled(self.config)
        return anthropic.AsyncAnthropic(api_key=self.api_key, base_url=self.cfg.get("base_url") or None, max_retries=2)

    async def list_models(self) -> list[str]:
        try:
            ids = []
            async for m in self.client().models.list():
                ids.append(m.id)
            return sorted(ids)
        except Exception as exc:
            raise _map_error(exc) from exc

    async def run(self, req: RunRequest, toolbox: ToolBox | None = None) -> RunResult:
        client = self.client()
        use_fallbacks = "fallbacks" in req.model.features
        api = client.beta.messages if use_fallbacks else client.messages

        content: list[dict] | str = req.prompt
        if req.images:
            content = []
            for img in req.images:
                mime, data = image_data_url(img)
                content.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}})
            content.append({"type": "text", "text": req.prompt})
        messages: list = list(req.history) + [{"role": "user", "content": content}]

        params: dict = {"model": req.model.id}
        if req.system:
            params["system"] = req.system
        if req.effort:
            # Thinking is adaptive (on) by default on current Claude models; effort controls its depth.
            params["output_config"] = {"effort": req.effort}
        if use_fallbacks:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"

        tools = None
        if toolbox:
            tools = [{"name": s.name, "description": s.description, "input_schema": s.schema} for s in toolbox.specs()]

        in_tok = out_tok = calls = 0
        texts: list[str] = []
        stop = ""
        served_by = req.model.id
        for _ in range(max(1, req.max_tool_rounds)):
            try:
                if tools:
                    # Non-streaming keeps tool inputs fully validated by the API.
                    resp = await api.create(max_tokens=min(req.max_tokens, 16000), messages=messages, tools=tools, **params)
                else:
                    async with api.stream(max_tokens=max(req.max_tokens, 16000), messages=messages, **params) as stream:
                        resp = await stream.get_final_message()
            except Exception as exc:
                raise _map_error(exc) from exc
            calls += 1
            served_by = getattr(resp, "model", served_by) or served_by
            u = resp.usage
            in_tok += (u.input_tokens or 0) + (getattr(u, "cache_creation_input_tokens", 0) or 0) + \
                (getattr(u, "cache_read_input_tokens", 0) or 0)
            out_tok += u.output_tokens or 0
            stop = resp.stop_reason or ""
            turn_text = "".join(b.text for b in resp.content if b.type == "text")

            if stop == "refusal":
                details = getattr(resp, "stop_details", None)
                cat = getattr(details, "category", None) if details else None
                texts.append(turn_text)
                texts.append(f"\n[Claude declined this request{f' ({cat})' if cat else ''}.]")
                break
            if stop == "pause_turn":
                messages.append({"role": "assistant", "content": resp.content})
                continue
            tool_uses = [b for b in resp.content if b.type == "tool_use"]
            if stop == "tool_use" and tool_uses and toolbox:
                if turn_text:
                    texts.append(turn_text)
                messages.append({"role": "assistant", "content": resp.content})
                outputs = await asyncio.gather(*(toolbox.call(b.name, b.input) for b in tool_uses))
                messages.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": b.id, "content": out, "is_error": err}
                    for b, (out, err) in zip(tool_uses, outputs)
                ]})
                continue
            texts.append(turn_text)
            if stop == "max_tokens":
                texts.append("\n[Output hit the max_tokens limit.]")
            break
        else:
            texts.append("\n[Stopped: tool-call round limit reached]")
        text = "\n\n".join(t for t in texts if t)
        if served_by != req.model.id:
            text += f"\n\n[Served by fallback model {served_by}]"
        return RunResult(text=text, provider=self.name, model=req.model.id, input_tokens=in_tok, output_tokens=out_tok,
                         cost_usd=req.model.cost(in_tok, out_tok), requests=calls, stop_reason=stop)
