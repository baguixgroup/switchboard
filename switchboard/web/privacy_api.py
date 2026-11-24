"""Bounded parent review endpoints; validation errors never echo raw input."""
import json
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, ValidationError

from ..privacy import PrivacyFailure


class HistoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: StrictStr = Field(min_length=1, max_length=8000)


class ContextItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["memory", "retrieval", "reference", "tool_result"]
    text: StrictStr = Field(min_length=1, max_length=8000)


class PreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: StrictStr = Field(min_length=1, max_length=8000)
    history: list[HistoryItem] = Field(default_factory=list, max_length=8)
    context: list[ContextItem] = Field(default_factory=list, max_length=8)


class SendIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    payload_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirm: StrictBool


def _unique_object(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError()


async def _body(request, model):
    try:
        raw = bytearray()
        async for part in request.stream():
            if len(raw) + len(part) > 64000:
                raise ValueError()
            raw.extend(part)
        value = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        return model.model_validate(value)
    except (ValueError, TypeError, ValidationError, RecursionError):
        raise HTTPException(400, "invalid_text_request") from None


def register(app, engine):
    if not engine.security.enabled:
        return

    @app.post("/api/privacy/preview")
    async def preview(request: Request):
        body = await _body(request, PreviewIn)
        try:
            return await engine.text_gateway.preview(request.state.principal, **body.model_dump())
        except PrivacyFailure as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/privacy/send")
    async def send(request: Request):
        body = await _body(request, SendIn)
        try:
            return await engine.text_gateway.send(request.state.principal, **body.model_dump())
        except PrivacyFailure as exc:
            raise HTTPException(400, str(exc)) from None
