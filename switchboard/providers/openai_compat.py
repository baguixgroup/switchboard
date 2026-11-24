"""OpenAI and every OpenAI-compatible API: DeepSeek, xAI, Gemini, GitHub Models, OpenRouter, Mistral, Ollama."""
from __future__ import annotations

import base64
import json
import socket
from urllib.parse import urlparse

import httpx
import openai

from ..toolbox import ToolBox
from .base import Provider, ProviderError, RunRequest, RunResult, image_data_url


def _map_error(exc: Exception) -> ProviderError:
    msg = str(exc)
    low = msg.lower()
    if isinstance(exc, openai.RateLimitError):
        if "insufficient_quota" in low or "quota" in low and "exceeded your current" in low:
            return ProviderError(f"Out of credit: {msg}", "credit")
        return ProviderError(f"Rate limited: {msg}", "rate_limit", retryable=True)
    if isinstance(exc, openai.AuthenticationError):
        return ProviderError(f"Authentication failed: {msg}", "auth")
    if isinstance(exc, openai.PermissionDeniedError):
        return ProviderError(f"Permission denied: {msg}", "auth")
    if isinstance(exc, openai.NotFoundError):
        return ProviderError(f"Model or endpoint not found: {msg}", "not_found")
    if isinstance(exc, openai.BadRequestError):
        return ProviderError(f"Bad request: {msg}", "bad_request")
    if isinstance(exc, openai.APIStatusError):
        if exc.status_code == 402 or "insufficient balance" in low:
            return ProviderError(f"Out of credit: {msg}", "credit")
        if exc.status_code >= 500:
            return ProviderError(f"Provider error {exc.status_code}: {msg}", "unavailable", retryable=True)
        return ProviderError(f"API error {exc.status_code}: {msg}", "error")
    if isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError)):
        return ProviderError(f"Cannot reach provider: {msg}", "unavailable", retryable=True)
    return ProviderError(msg)


class OpenAICompatProvider(Provider):
    kind = "openai_compat"

    @property
    def api_key(self) -> str | None:
        return self.config.secret(self.cfg.get("api_key_env")) or self.cfg.get("api_key")

    @property
    def base_url(self) -> str:
        return self.cfg.get("base_url") or "https://api.openai.com/v1"

    def available(self) -> bool:
        from ..security import protected_mode
        if protected_mode(self.config):
            return False
        if not self.api_key:
            return False
        if self.cfg.get("local"):
            u = urlparse(self.base_url)
            try:
                with socket.create_connection((u.hostname or "localhost", u.port or 80), timeout=0.3):
                    return True
            except OSError:
                return False
        return True

    def client(self) -> openai.AsyncOpenAI:
        from ..security import require_execution_enabled
        require_execution_enabled(self.config)
        return openai.AsyncOpenAI(api_key=self.api_key, base_url=self.base_url,
                                  default_headers=self.cfg.get("headers") or None, timeout=600, max_retries=2)

    async def list_models(self) -> list[str]:
        try:
            page = await self.client().models.list()
            return sorted(m.id.removeprefix("models/") for m in page.data)
        except Exception as exc:
            raise _map_error(exc) from exc

    async def run(self, req: RunRequest, toolbox: ToolBox | None = None) -> RunResult:
        if "image_gen" in req.model.features:
            return await self._image(req)
        client = self.client()
        messages: list[dict] = []
        if req.system:
            messages.append({"role": "system", "content": req.system})
        messages += req.history
        if req.images:
            content: list[dict] = [{"type": "text", "text": req.prompt}]
            for img in req.images:
                mime, data = image_data_url(img)
                content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}})
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "user", "content": req.prompt})

        kwargs: dict = {"model": req.model.id}
        tokens_param = self.cfg.get("max_tokens_param", "max_tokens")
        kwargs[tokens_param if tokens_param in ("max_tokens", "max_completion_tokens") else "max_tokens"] = req.max_tokens
        if req.effort:
            param = self.cfg.get("effort_param", "reasoning_effort")
            if param == "reasoning_effort":
                kwargs["reasoning_effort"] = req.effort
            elif param:
                kwargs["extra_body"] = {param: req.effort}

        tools = None
        if toolbox:
            tools = [{"type": "function", "function": {"name": s.name, "description": s.description,
                                                         "parameters": s.schema}} for s in toolbox.specs()]
        in_tok = out_tok = calls = 0
        text, stop = "", ""
        for _ in range(max(1, req.max_tool_rounds)):
            try:
                resp = await client.chat.completions.create(messages=messages, **({"tools": tools} if tools else {}),
                                                            **kwargs)
            except Exception as exc:
                raise _map_error(exc) from exc
            calls += 1
            if resp.usage:
                in_tok += resp.usage.prompt_tokens or 0
                out_tok += resp.usage.completion_tokens or 0
            choice = resp.choices[0]
            msg = choice.message
            stop = choice.finish_reason or ""
            if msg.tool_calls and toolbox:
                messages.append(msg.model_dump(exclude_none=True))
                for tc in msg.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except ValueError:
                        out, err = "Invalid JSON arguments; send a JSON object.", True
                    else:
                        out, err = await toolbox.call(tc.function.name, args)
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": ("ERROR: " if err else "") + out})
                continue
            text = msg.content or ""
            break
        else:
            text = (text or "") + "\n\n[Stopped: tool-call round limit reached]"
        return RunResult(text=text, provider=self.name, model=req.model.id, input_tokens=in_tok,
                         output_tokens=out_tok, cost_usd=req.model.cost(in_tok, out_tok), requests=calls,
                         stop_reason=stop)

    async def _image(self, req: RunRequest) -> RunResult:
        client = self.client()
        try:
            resp = await client.images.generate(model=req.model.id, prompt=req.prompt, n=1)
        except Exception as exc:
            raise _map_error(exc) from exc
        files: list[tuple[str, bytes]] = []
        for i, item in enumerate(resp.data or []):
            if getattr(item, "b64_json", None):
                files.append((f"image-{i + 1}.png", base64.b64decode(item.b64_json)))
            elif getattr(item, "url", None):
                async with httpx.AsyncClient(timeout=120) as http:
                    r = await http.get(item.url)
                    r.raise_for_status()
                    files.append((f"image-{i + 1}.png", r.content))
        revised = next((getattr(d, "revised_prompt", None) for d in resp.data or [] if getattr(d, "revised_prompt", None)), None)
        text = f"Generated {len(files)} image(s)." + (f"\n\nRevised prompt: {revised}" if revised else "")
        return RunResult(text=text, provider=self.name, model=req.model.id, cost_usd=req.model.cost(0, 0) * max(1, len(files)),
                         files=files, stop_reason="image")
