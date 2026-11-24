"""Local authorization foundation. Protected mode deliberately enables no egress.

This is not a PII sanitizer. Child workloads stay closed until later safety stages.
Legacy, explicitly adult installations retain their existing behavior.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass


class SecurityDenied(PermissionError):
    """Content-free policy failure, safe to return without request data."""


def protected_mode(config) -> bool:
    section = config.raw.get("security", {})
    if not isinstance(section, dict) or set(section) - {"enabled"} or type(section.get("enabled", False)) is not bool:
        raise ValueError("security.enabled must be a boolean")
    return section.get("enabled", False)


def require_execution_enabled(config) -> None:
    if protected_mode(config):
        raise SecurityDenied("execution_not_enabled_in_protected_mode")


@dataclass(frozen=True)
class Principal:
    subject: str
    role: str
    session_id: str | None = None
    device: str | None = None


@dataclass(frozen=True)
class OutboundIntent:
    destination: str
    purpose: str
    action: str = "provider_request"
    policy_version: int = 1


@dataclass(frozen=True)
class OutboundDecision:
    allowed: bool
    reason: str
    policy_version: int = 1


def outbound_decision(principal: Principal | None, intent: OutboundIntent | None = None) -> OutboundDecision:
    # No caller-controlled allowlist/override before full-context sanitization exists.
    return OutboundDecision(False, "identity_required" if principal is None else "outbound_not_enabled")


class LocalSecurity:
    def __init__(self, config, store, *, now=time.time):
        self.enabled = protected_mode(config)
        self.store = store
        self.now = now
        self._parent_token = ""
        self._authority = ""
        if not self.enabled:
            return
        token = config.secret("SWITCHBOARD_PARENT_TOKEN") or ""
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token) or hmac.compare_digest(token.encode(), config.token.encode()):
            raise ValueError("Protected mode requires a distinct SWITCHBOARD_PARENT_TOKEN of at least 32 characters")
        self._parent_token = token
        self._authority = hashlib.sha256(token.encode()).hexdigest()
        store._exec("""CREATE TABLE IF NOT EXISTS security_sessions (
            id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL,
            subject TEXT NOT NULL, role TEXT NOT NULL, device TEXT,
            created REAL NOT NULL, expires REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0,
            authority TEXT NOT NULL
        )""")

    def authenticate(self, authorization: str) -> Principal:
        if not self.enabled or not authorization.startswith("Bearer "):
            raise SecurityDenied("credential_required")
        token = authorization[7:]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", token):
            raise SecurityDenied("invalid_credential")
        if hmac.compare_digest(token, self._parent_token):
            return Principal("local-parent", "parent")
        rows = self.store._exec(
            "SELECT id,subject,role,device,expires,revoked,authority FROM security_sessions WHERE token_hash=?",
            (hashlib.sha256(token.encode()).hexdigest(),),
        )
        if not rows or rows[0]["revoked"] or rows[0]["expires"] <= self.now() or rows[0]["authority"] != self._authority:
            raise SecurityDenied("invalid_or_expired_credential")
        row = rows[0]
        if row["role"] not in ("parent", "child", "device"):
            raise SecurityDenied("invalid_credential")
        return Principal(row["subject"], row["role"], row["id"], row["device"])

    @staticmethod
    def authorize(principal: Principal, method: str, path: str) -> None:
        if method == "GET" and path == "/api/security/me":
            return
        if principal.role == "parent":
            if path in ("/api/status", "/api/settings", "/api/security/policy") and method == "GET":
                return
            if path == "/api/security/sessions" and method in ("GET", "POST"):
                return
            if re.fullmatch(r"/api/security/sessions/[a-f0-9]{32}", path) and method == "DELETE":
                return
        if principal.role == "device" and method == "POST" and path == "/api/agent/heartbeat":
            return
        # Includes pairing, generic tasks, Laith, tools, live voice and unknown routes.
        raise SecurityDenied("operation_not_authorized")

    def _require_parent(self, principal: Principal) -> None:
        # Recheck revocation/expiry for direct service calls as well as HTTP admission.
        if principal.role != "parent":
            raise SecurityDenied("parent_required")
        if principal.session_id:
            rows = self.store._exec("SELECT expires,revoked,role,authority FROM security_sessions WHERE id=?", (principal.session_id,))
            if not rows or rows[0]["revoked"] or rows[0]["expires"] <= self.now() or rows[0]["role"] != "parent" or rows[0]["authority"] != self._authority:
                raise SecurityDenied("invalid_or_expired_credential")

    def issue(self, principal: Principal, *, subject: str, role: str, seconds: int = 900, device: str | None = None) -> dict:
        self._require_parent(principal)
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", subject) or role not in ("parent", "child", "device"):
            raise ValueError("Use an opaque subject and a supported role")
        if type(seconds) is not int or not 60 <= seconds <= 86400:
            raise ValueError("Session lifetime must be between 60 and 86400 seconds")
        if (role == "device" and (not isinstance(device, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", device))) or (role != "device" and device is not None):
            raise ValueError("Only device sessions require a node binding")
        key, token = secrets.token_hex(16), secrets.token_urlsafe(32)
        created = self.now()
        self.store._exec("INSERT INTO security_sessions VALUES(?,?,?,?,?,?,?,0,?)",
                         (key, hashlib.sha256(token.encode()).hexdigest(), subject, role, device, created, created + seconds, self._authority))
        return {"id": key, "token": token, "subject": subject, "role": role, "device": device, "expires": created + seconds}

    def sessions(self, principal: Principal) -> list[dict]:
        self._require_parent(principal)
        return [dict(row) for row in self.store._exec(
            "SELECT id,subject,role,device,created,expires,revoked FROM security_sessions ORDER BY created DESC")]

    def revoke(self, principal: Principal, key: str) -> None:
        self._require_parent(principal)
        self.store._exec("UPDATE security_sessions SET revoked=1 WHERE id=?", (key,))

    @staticmethod
    def require_node(principal: Principal, node: str) -> None:
        if principal.role != "device" or principal.device != node:
            raise SecurityDenied("device_binding_mismatch")
