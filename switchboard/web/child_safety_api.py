"""Parent-only local policy review; never a child chat or notification API."""
from dataclasses import asdict

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field, StrictStr
from typing import Literal

from .privacy_api import _body
from ..child_safety import VERSION


class EvaluationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: list[StrictStr] = Field(min_length=1, max_length=17)
    candidate: StrictStr = Field(min_length=1, max_length=8000)


class ResolveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_id: StrictStr = Field(pattern=r"^[a-f0-9]{32}$")
    action: Literal["acknowledge", "dismiss"]


def register(app, engine):
    if not engine.security.enabled:
        return
    safety = engine.text_gateway.safety

    @app.get("/api/safety/policy")
    async def policy(request: Request):
        safety.guard(request.state.principal)
        return {**asdict(safety.policy), "policy_version": VERSION, "child_delivery_enabled": False,
                "assurance": "limited_patterns_parent_review_required", "raw_evidence_retained": False,
                "review_ttl_seconds": 900, "review_capacity": 32, "notifications_enabled": False}

    @app.post("/api/safety/evaluate")
    async def evaluate(request: Request):
        body = await _body(request, EvaluationIn)
        p = request.state.principal
        result = await safety.gate(p, tuple(body.input), stage="input")
        if result is None:
            result = await safety.gate(p, (body.candidate,), stage="response")
        if result:
            # This endpoint never calls a provider, including response simulations.
            return {**result, "external_transfer": False, "simulation": True}
        return {"delivery": "parent_review_required", "pattern_match": False, "simulation": True,
                "external_transfer": False, "persisted": False, "child_delivery_enabled": False,
                "warning": "No detected pattern is not proof of safety. Candidate withheld from child delivery."}

    @app.get("/api/safety/reviews")
    async def reviews(request: Request):
        return {"reviews": safety.incidents(request.state.principal), "notification_sent": False}

    @app.post("/api/safety/reviews/resolve")
    async def resolve(request: Request):
        body = await _body(request, ResolveIn)
        return safety.review(request.state.principal, body.review_id, body.action)
