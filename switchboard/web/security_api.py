"""Local-only security administration; never returns stored bearer tokens."""
from dataclasses import asdict

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from ..security import outbound_decision


class SessionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    role: Literal["parent", "child", "device"]
    seconds: int = Field(default=900, ge=60, le=86400, strict=True)
    device: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,64}$")


def register(app, engine):
    security = engine.security
    if not security.enabled:
        return

    @app.get("/api/security/me")
    async def me(request: Request):
        return asdict(request.state.principal)

    @app.get("/api/security/policy")
    async def policy(request: Request):
        return {"protected": True, "phase": "registered-retention-and-deletion", "outbound": asdict(outbound_decision(request.state.principal)),
                "outbound_scope": "legacy_execution",
                "child_chat_enabled": False, "pii_sanitization_implemented": True,
                "text_gateway_enabled": engine.text_gateway.policy.enabled,
                "text_provider": engine.text_gateway.policy.provider,
                "local_tools_enabled": engine.local_tools.policy.enabled,
                "child_safety_review_enabled": engine.text_gateway.safety.policy.enabled,
                "managed_retention_enabled": engine.retention.policy.enabled,
                "legacy_deletion_enabled": False,
                "privacy_assurance": "limited_local_detection_requires_parent_review"}

    @app.post("/api/security/sessions", status_code=201)
    async def issue(request: Request, body: SessionIn, response: Response):
        response.headers["Cache-Control"] = "no-store"
        try:
            return security.issue(request.state.principal, **body.model_dump())
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.get("/api/security/sessions")
    async def sessions(request: Request):
        return security.sessions(request.state.principal)

    @app.delete("/api/security/sessions/{key}")
    async def revoke(key: str, request: Request):
        security.revoke(request.state.principal, key)
        return {"revoked": True}
