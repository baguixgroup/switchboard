"""Fixed text-only destinations; no tools, fallback, redirects or SDK retries."""
import json

import httpx

from .privacy import PrivacyFailure

ANTHROPIC_ENDPOINT = "https://api.anthropic.com/v1/messages"
GEMINI_ROOT = "https://generativelanguage.googleapis.com/v1beta/models/"


def endpoint_for(policy):
    if policy.provider == "openai":
        return "https://api.openai.com/v1/chat/completions"
    if policy.provider == "anthropic":
        return ANTHROPIC_ENDPOINT
    if policy.provider == "gemini":
        return GEMINI_ROOT + policy.model + ":generateContent"
    raise PrivacyFailure("provider_request_failed")


def credential_name(policy):
    return {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY"}[policy.provider]


async def bounded_post(endpoint, payload, headers):
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=8), trust_env=False, follow_redirects=False) as client:
            async with client.stream("POST", endpoint, content=payload, headers={
                    "Content-Type": "application/json", "Accept-Encoding": "identity", **headers}) as response:
                if response.status_code != 200:
                    raise PrivacyFailure("provider_request_failed")
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise PrivacyFailure("provider_response_rejected")
                data = bytearray()
                async for part in response.aiter_bytes():
                    if len(data) + len(part) > 64000:
                        raise PrivacyFailure("provider_response_rejected")
                    data.extend(part)
        return json.loads(data)
    except PrivacyFailure:
        raise
    except Exception:
        raise PrivacyFailure("provider_request_failed") from None


class AnthropicTextTransport:
    async def send(self, payload, key):
        value = await bounded_post(ANTHROPIC_ENDPOINT, payload, {"x-api-key": key, "anthropic-version": "2026-10-21"})
        try:
            blocks = value["content"]
            if value["stop_reason"] != "end_turn" or not isinstance(blocks, list) or not blocks:
                raise ValueError()
            if any(set(b) - {"type", "text", "citations"} or b.get("type") != "text" or not isinstance(b.get("text"), str) or b.get("citations") not in (None, []) for b in blocks):
                raise ValueError()
            text = "\n".join(b["text"] for b in blocks)
            if not 0 < len(text) <= 8000:
                raise ValueError()
            return text
        except Exception:
            raise PrivacyFailure("provider_response_rejected") from None


class GeminiTextTransport:
    def __init__(self, policy):
        self.endpoint = endpoint_for(policy)

    async def send(self, payload, key):
        value = await bounded_post(self.endpoint, payload, {"x-goog-api-key": key})
        try:
            candidates = value["candidates"]
            if len(candidates) != 1 or candidates[0]["finishReason"] != "STOP":
                raise ValueError()
            parts = candidates[0]["content"]["parts"]
            if not isinstance(parts, list) or not parts or any(set(p) - {"text", "thought", "thoughtSignature"} or not isinstance(p.get("text"), str) or p.get("thought") not in (None, False) for p in parts):
                raise ValueError()
            text = "\n".join(p["text"] for p in parts)
            if not 0 < len(text) <= 8000:
                raise ValueError()
            return text
        except Exception:
            raise PrivacyFailure("provider_response_rejected") from None


def build_payload(policy, messages, system):
    if policy.provider == "openai":
        return {"model": policy.model, "messages": messages, "max_completion_tokens": policy.max_tokens, "store": False, "stream": False}
    if policy.provider == "anthropic":
        return {"model": policy.model, "system": system, "messages": messages[1:], "max_tokens": policy.max_tokens, "stream": False}
    return {"systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]} for m in messages[1:]],
            "generationConfig": {"maxOutputTokens": policy.max_tokens}}


def payload_fields(policy, body, system):
    """Validate every transport field before extracting text for the final check."""
    try:
        if policy.provider == "openai":
            if set(body) != {"model", "messages", "max_completion_tokens", "store", "stream"} or body["store"] is not False or body["stream"] is not False:
                raise ValueError()
            if body["model"] != policy.model or type(body["max_completion_tokens"]) is not int or body["max_completion_tokens"] != policy.max_tokens:
                raise ValueError()
            messages = body["messages"]
            if messages[0] != {"role": "system", "content": system}:
                raise ValueError()
            fields = [system]
            messages = messages[1:]
        elif policy.provider == "anthropic":
            if set(body) != {"model", "messages", "system", "max_tokens", "stream"} or body["stream"] is not False:
                raise ValueError()
            if body["model"] != policy.model or body["system"] != system or type(body["max_tokens"]) is not int or body["max_tokens"] != policy.max_tokens:
                raise ValueError()
            fields, messages = [system], body["messages"]
        else:
            if set(body) != {"systemInstruction", "contents", "generationConfig"} or body["systemInstruction"] != {"parts": [{"text": system}]} or body["generationConfig"] != {"maxOutputTokens": policy.max_tokens} or type(body["generationConfig"]["maxOutputTokens"]) is not int:
                raise ValueError()
            fields = [system]
            messages = []
            for m in body["contents"]:
                if set(m) != {"role", "parts"} or m["role"] not in ("user", "model") or len(m["parts"]) != 1 or set(m["parts"][0]) != {"text"}:
                    raise ValueError()
                messages.append({"role": "assistant" if m["role"] == "model" else "user", "content": m["parts"][0]["text"]})
        if not isinstance(messages, list) or not 1 <= len(messages) <= 5 or messages[-1]["role"] != "user":
            raise ValueError()
        for m in messages:
            if set(m) != {"role", "content"} or m["role"] not in ("user", "assistant") or not isinstance(m["content"], str):
                raise ValueError()
            fields.append(m["content"])
        return tuple([policy.model] + fields)
    except Exception:
        raise PrivacyFailure("privacy_check_failed") from None
