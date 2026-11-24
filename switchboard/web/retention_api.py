"""Parent-only metadata inventory and exact reviewed deletion of registered roots."""
import asyncio
import sqlite3
from dataclasses import asdict

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr

from .. import paths
from ..retention import RetentionFailure
from ..retention_inventory import legacy_inventory
from ..security import SecurityDenied
from .privacy_api import _body


class RootIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    root: StrictStr = Field(pattern=r"^[a-f0-9]{32}$")


class DeleteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_id: StrictStr = Field(pattern=r"^[a-f0-9]{32}$")
    digest: StrictStr = Field(pattern=r"^[a-f0-9]{64}$")
    confirm: StrictBool


class EmptyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")


async def run(function, *args):
    try: return await asyncio.to_thread(function, *args)
    except SecurityDenied: raise
    except RetentionFailure as exc: raise HTTPException(409,str(exc)) from None
    except (OSError, ValueError, sqlite3.Error): raise HTTPException(409,"retention_unavailable") from None


def register(app, engine):
    if not engine.security.enabled: return
    retention=engine.retention

    @app.get("/api/retention/policy")
    async def policy():
        return {**asdict(retention.policy),"scope":"registered_vault_only","legacy_migration_enabled":False,
                "child_delivery_enabled":False,"provider_deletion_guaranteed":False}

    @app.get("/api/retention/inventory")
    async def inventory(request: Request):
        output=engine.config.raw.get("output_dir") or "auto"
        output=paths.default_output_dir() if output=="auto" else paths.expand(str(output))
        report=await run(legacy_inventory,engine.config.home,output)
        report["managed_vault_enabled"]=retention.policy.enabled
        return report

    @app.post("/api/retention/delete/preview")
    async def preview(request: Request):
        body=await _body(request,RootIn)
        try: return await retention.preview_delete(request.state.principal,body.root)
        except RetentionFailure as exc: raise HTTPException(409,str(exc)) from None

    @app.post("/api/retention/delete/confirm")
    async def confirm(request: Request):
        body=await _body(request,DeleteIn)
        return await run(retention.confirm_delete,request.state.principal,body.preview_id,body.digest,body.confirm)

    @app.post("/api/retention/receipt")
    async def receipt(request: Request):
        body=await _body(request,RootIn)
        return await run(retention.receipt,request.state.principal,body.root)

    @app.post("/api/retention/maintain")
    async def maintain(request: Request):
        await _body(request,EmptyIn)
        return await run(retention.maintain,request.state.principal)
