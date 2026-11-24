from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict
from typing import Literal

from ..privacy import PrivacyFailure
from .privacy_api import SendIn, _body


class ToolIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: Literal["calculate", "text.statistics", "attachment.extract"]
    arguments: dict


def register(app, engine):
    if not engine.security.enabled:
        return

    @app.get("/api/privacy/tools")
    async def policy():
        return {"enabled": engine.local_tools.policy.enabled, "allowed": engine.local_tools.policy.allowed,
                "external_transfer": False, "child_use_enabled": False}

    @app.post("/api/privacy/tools/preview")
    async def preview(request: Request):
        body = await _body(request, ToolIn)
        try:
            return await engine.local_tools.preview(request.state.principal, **body.model_dump())
        except PrivacyFailure as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/privacy/tools/run")
    async def run(request: Request):
        body = await _body(request, SendIn)
        try:
            return await engine.local_tools.run(request.state.principal, **body.model_dump())
        except PrivacyFailure as exc:
            raise HTTPException(400, str(exc)) from None
