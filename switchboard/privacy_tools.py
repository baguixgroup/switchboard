"""Parent-approved local tools. No arbitrary command, path, MCP or network target."""
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass

from .privacy import PrivacyFailure
from .privacy_gateway import serialize, text_policy
from .privacy_sandbox import RestrictedWorker
from .security import Principal, SecurityDenied, protected_mode

TOOLS = ("calculate", "text.statistics", "attachment.extract")
log = logging.getLogger("switchboard.privacy")


@dataclass(frozen=True)
class ToolPolicy:
    enabled: bool = False
    allowed: tuple[str, ...] = ()


def tool_policy(config):
    raw = config.raw.get("security", {}).get("local_tools", {})
    if not isinstance(raw, dict) or set(raw) - {"enabled", "allowed"}:
        raise ValueError("Invalid local tool policy")
    enabled, allowed = raw.get("enabled", False), raw.get("allowed", [])
    if type(enabled) is not bool or not isinstance(allowed, list) or any(t not in TOOLS for t in allowed) or len(set(allowed)) != len(allowed):
        raise ValueError("Invalid local tool policy")
    if enabled and not protected_mode(config):
        raise ValueError("Local tools require protected mode")
    return ToolPolicy(enabled, tuple(allowed))


def validate_arguments(tool, args):
    if tool not in TOOLS or not isinstance(args, dict):
        raise PrivacyFailure("invalid_text_request")
    if tool == "calculate":
        if set(args) != {"expression"} or not isinstance(args["expression"], str) or not 0 < len(args["expression"]) <= 256:
            raise PrivacyFailure("invalid_text_request")
    elif tool == "text.statistics":
        if set(args) != {"text"} or not isinstance(args["text"], str) or not 0 < len(args["text"]) <= 8000:
            raise PrivacyFailure("invalid_text_request")
    else:
        if set(args) != {"format", "data_b64"} or args["format"] not in ("text", "markdown", "csv", "json") or not isinstance(args["data_b64"], str) or len(args["data_b64"]) > 21848:
            raise PrivacyFailure("invalid_text_request")
        try:
            data = base64.b64decode(args["data_b64"], validate=True)
            if not 0 < len(data) <= 16384:
                raise ValueError()
        except Exception:
            raise PrivacyFailure("invalid_text_request") from None


@dataclass(frozen=True)
class ToolPreview:
    owner: Principal
    policy: ToolPolicy
    payload: bytes
    digest: str
    expires: float


class LocalTools:
    def __init__(self, config, security, gateway):
        self.config, self.security, self.gateway = config, security, gateway
        self.policy = tool_policy(config)
        self.worker = RestrictedWorker()
        self.pending = {}
        self.timers = {}
        self.slot = asyncio.Lock()
        self.now = time.time
        self.closed = False

    def check(self, principal):
        self.security._require_parent(principal)
        if self.closed or self.gateway._closed or not self.security.enabled or not self.policy.enabled or tool_policy(self.config) != self.policy or text_policy(self.config) != self.gateway.policy:
            raise SecurityDenied("local_tools_not_authorized")

    def forget(self, key):
        self.pending.pop(key, None)
        timer = self.timers.pop(key, None)
        if timer: timer.cancel()

    async def preview(self, principal, *, tool, arguments):
        self.check(principal)
        if tool not in self.policy.allowed:
            raise SecurityDenied("local_tool_not_authorized")
        validate_arguments(tool, arguments)
        for key, value in list(self.pending.items()):
            if value.expires <= self.now(): self.forget(key)
        if len(self.pending) >= 16:
            raise PrivacyFailure("preview_capacity_reached")
        payload = serialize({"tool": tool, "arguments": arguments})
        if len(payload) > 32000:
            raise PrivacyFailure("invalid_text_request")
        key, digest, expires = secrets.token_hex(16), hashlib.sha256(payload).hexdigest(), self.now() + 120
        self.pending[key] = ToolPreview(principal, self.policy, payload, digest, expires)
        self.timers[key] = asyncio.get_running_loop().call_later(120, self.forget, key)
        log.info("local_tool_preview_created tool=%s", tool)
        # File contents, raw text and even filenames never echo in this receipt.
        return {"preview_id": key, "payload_digest": digest, "expires": expires, "tool": tool,
                "input_bytes": len(payload), "destination": "restricted_local_worker", "external_transfer": False}

    async def run(self, principal, *, preview_id, payload_digest, confirm):
        self.check(principal)
        item = self.pending.get(preview_id)
        if item and item.expires <= self.now():
            self.forget(preview_id); raise SecurityDenied("tool_preview_not_authorized")
        if not item or item.owner != principal or confirm is not True or not isinstance(payload_digest, str) or not re.fullmatch(r"[a-f0-9]{64}", payload_digest) or not hmac.compare_digest(item.digest, payload_digest):
            raise SecurityDenied("tool_preview_not_authorized")
        if item.policy != self.policy or hashlib.sha256(item.payload).hexdigest() != item.digest:
            self.forget(preview_id); raise SecurityDenied("tool_preview_not_authorized")
        if self.slot.locked(): raise PrivacyFailure("privacy_gateway_busy")
        self.forget(preview_id)
        async with self.slot:
            self.check(principal)
            result = await self.worker.run(json.loads(item.payload))
            self.check(principal)
            clean = await self.gateway._minimize((result,))
            await self.gateway._final_check(clean.texts, payload=False)
            self.check(principal)
            log.info("local_tool_completed")
            return {"text": clean.texts[0], "redactions": dict(clean.categories), "persisted": False,
                    "external_transfer": False, "requires_separate_provider_review": True}

    def close(self):
        self.closed = True
        for key in list(self.pending): self.forget(key)
