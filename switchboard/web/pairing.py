"""Pairing: join a computer to this hub with a short code instead of copying the long token.

The dashboard (Machines -> Connect a computer) shows a 6-digit code that lasts 10 minutes and works
once. The setup wizard on the new computer sends it here and gets the token back. Five wrong codes
cancel the current code, so it can't be guessed (1 in 200,000 per code).
"""
from __future__ import annotations

import hmac
import logging
import secrets
import time

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

log = logging.getLogger("switchboard.pairing")
CODE_TTL = 600
MAX_FAILS = 5


class PairIn(BaseModel):
    code: str = Field(max_length=32)
    name: str = Field(default="", max_length=64)


class Pairing:
    def __init__(self, now=time.time):
        self.now = now
        self.code: str | None = None
        self.expires = 0.0
        self.fails = 0

    def new_code(self) -> dict:
        self.code = f"{secrets.randbelow(1_000_000):06d}"
        self.expires = self.now() + CODE_TTL
        self.fails = 0
        return self.current()

    def current(self) -> dict:
        if not self.code or self.now() >= self.expires:
            return {"code": None, "expires_in": 0}
        return {"code": self.code, "expires_in": int(self.expires - self.now())}

    def redeem(self, supplied: str) -> bool:
        supplied = "".join(ch for ch in supplied if ch.isdigit())
        if not self.code or self.now() >= self.expires:
            self.code = None
            return False
        if hmac.compare_digest(supplied, self.code):
            self.code = None  # one use
            return True
        self.fails += 1
        if self.fails >= MAX_FAILS:
            log.warning("Pairing code cancelled after %s wrong tries", self.fails)
            self.code = None
        return False


DOWNLOAD_KINDS = {"macos": (".dmg",), "windows": (".exe", ".msi"), "pi": (".deb",)}


def downloads_dir(cfg):
    return cfg.home / "downloads"


def available_downloads(cfg) -> dict:
    """Installers the hub can hand out itself: put Switchboard-*.dmg / -Setup.exe / .deb files in <config>/downloads."""
    d = downloads_dir(cfg)
    out: dict = {}
    if not d.is_dir():
        return out
    files = sorted((f for f in d.iterdir() if f.is_file() and not f.name.startswith(".")),
                   key=lambda f: f.stat().st_mtime, reverse=True)
    for kind, exts in DOWNLOAD_KINDS.items():
        for f in files:
            if f.suffix.lower() in exts and (kind != "pi" or "arm64" in f.name or "aarch64" in f.name or kind not in out):
                out[kind] = {"name": f.name, "url": f"/downloads/{f.name}", "size": f.stat().st_size}
                break
    return out


def register(app: FastAPI, engine, auth) -> None:
    cfg = engine.config
    pairing = Pairing()
    app.state.pairing = pairing
    dep = [Depends(auth)]

    @app.get("/api/pair/code", dependencies=dep)
    async def pair_code() -> dict:
        """The current code, if any (the dashboard shows it)."""
        return pairing.current()

    @app.post("/api/pair/code", dependencies=dep)
    async def pair_new_code() -> dict:
        return pairing.new_code()

    @app.get("/downloads/{name}")
    async def download(name: str):
        """Installers are not secret: a computer that hasn't joined yet needs to fetch them (LAN only)."""
        from fastapi.responses import FileResponse

        d = downloads_dir(cfg)
        f = d / name
        if "/" in name or "\\" in name or name.startswith(".") or not f.is_file() \
                or f.suffix.lower() not in {e for exts in DOWNLOAD_KINDS.values() for e in exts}:
            raise HTTPException(404)
        return FileResponse(f, filename=name)

    @app.post("/api/pair")
    async def pair(body: PairIn) -> dict:
        """Called by the setup wizard on a new computer. No token needed: the code is the proof."""
        if not cfg.token:
            raise HTTPException(409, "This hub has no token yet, so computers can't join it")
        if not pairing.redeem(body.code):
            raise HTTPException(403, "That code is wrong or has expired. Get a new one from the hub's dashboard.")
        log.info("Computer %r joined with a pairing code", body.name or "?")
        return {"token": cfg.token, "hub": cfg.node_name}
