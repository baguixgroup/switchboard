"""Set this computer up as the hub or as an agent. Shared by `switchboard init`, `switchboard setup`
(terminal) and the setup wizard (browser), so all three write the same files."""
from __future__ import annotations

import contextlib
import os
import re
import secrets
import socket
from pathlib import Path

import httpx

from .config import Config, default_text, read_env_file, write_env_value
from .paths import platform_tag

SECRETS_TEMPLATE = """# Tazrivo secrets for this machine. Never commit or share this file.
# The token must be identical on the hub and every agent.
SWITCHBOARD_TOKEN={token}

# API keys: only needed on the HUB (it makes all model calls). Add the ones you have.
# ANTHROPIC_API_KEY=
# OPENAI_API_KEY=
# DEEPSEEK_API_KEY=
# XAI_API_KEY=
# GEMINI_API_KEY=
# GITHUB_TOKEN=            # GitHub Models (needs the models:read scope)
# OPENROUTER_API_KEY=
# MISTRAL_API_KEY=

# Tool credentials used by MCP servers (on the machine that runs them)
# HOSTINGER_API_TOKEN=
# NTFY_TOKEN=              # only for a protected ntfy topic
"""
PLACEHOLDER = "PASTE-THE-HUB-TOKEN-HERE"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


class SetupError(Exception):
    """A problem the person can fix (shown as-is in the wizard)."""


def default_name() -> str:
    return re.sub(r"[^a-z0-9-]", "-", socket.gethostname().split(".")[0].lower()).strip("-")[:40] or "my-computer"


def clean_name(name: str | None) -> str:
    n = re.sub(r"[^a-z0-9-]+", "-", (name or "").strip().lower()).strip("-")[:40]
    return n or default_name()


def clean_hub_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url:
        raise SetupError("Enter the hub's address, for example 192.168.1.50")
    if "://" not in url:
        url = "http://" + url
    if not re.match(r"^https?://[^/\s]+$", url):
        raise SetupError("That doesn't look like an address. Use the hub's IP or name, for example 192.168.1.50")
    if not re.search(r":\d+$", url.split("://", 1)[1]):
        url += ":8765"
    return url


def current(home: Path | None = None) -> dict:
    cfg = Config(home)
    if not cfg.exists:
        return {"configured": False}
    return {"configured": True, "role": cfg.role, "name": cfg.node_name, "hub_url": cfg.hub_url,
            "has_token": bool(cfg.token and cfg.token != PLACEHOLDER)}


def recommended_role() -> str:
    """Raspberry Pis and other Linux boxes make good always-on hubs; PCs and Macs usually join one."""
    return "hub" if platform_tag() in ("pi", "linux") else "agent"


def check_hub(url: str, timeout: float = 5) -> dict:
    url = clean_hub_url(url)
    try:
        r = httpx.get(url + "/healthz", timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPError, ValueError):
        raise SetupError(f"No Tazrivo hub answered at {url}. Check the address and that the hub is on.") from None
    return {"url": url, "name": data.get("node", "")}


def pair(url: str, code: str, name: str, timeout: float = 10) -> str:
    """Swap a pairing code from the hub's dashboard for the hub's token."""
    url = clean_hub_url(url)
    try:
        r = httpx.post(url + "/api/pair", json={"code": code, "name": name}, timeout=timeout)
    except httpx.HTTPError:
        raise SetupError(f"Couldn't reach the hub at {url}.") from None
    if r.status_code == 404:
        raise SetupError("That hub is an older version without pairing codes. Paste its token instead.")
    if r.status_code != 200:
        with contextlib.suppress(ValueError):
            raise SetupError(r.json().get("detail") or "The hub refused the code.")
        raise SetupError("The hub refused the code.")
    return r.json()["token"]


def verify_token(url: str, token: str, timeout: float = 10) -> None:
    try:
        r = httpx.get(clean_hub_url(url) + "/api/status", headers={"Authorization": f"Bearer {token}"}, timeout=timeout)
    except httpx.HTTPError:
        raise SetupError("Couldn't reach the hub.") from None
    if r.status_code == 401:
        raise SetupError("The hub didn't accept that token.")


def _set(text: str, key: str, value: str) -> str:
    """Change one `  key: value` line of the node: section (keeps the rest of the file and its comments)."""
    new, n = re.subn(rf"^(  {key}: )\S+", lambda m: m.group(1) + value, text, count=1, flags=re.M)
    if n:
        return new
    return re.sub(r"^(node:[^\n]*\n)", rf"\g<1>  {key}: {value}\n", text, count=1, flags=re.M)


def write_config(role: str, name: str, hub_url: str | None = None, token: str | None = None,
                 home: Path | None = None) -> dict:
    """Create or update config.yaml / credits.yaml / secrets.env. Existing settings and API keys are kept."""
    if role not in ("hub", "agent"):
        raise SetupError("Choose hub or agent")
    cfg = Config(home)
    cfg.home.mkdir(parents=True, exist_ok=True)
    written = []
    text = cfg.path.read_text(encoding="utf-8") if cfg.exists else default_text("config.yaml")
    text = _set(text, "name", name)
    text = _set(text, "role", role)
    text = _set(text, "hub_url", hub_url if (role == "agent" and hub_url) else "http://127.0.0.1:8765")
    cfg.path.write_text(text, encoding="utf-8")
    written.append(str(cfg.path))
    credits = cfg.home / "credits.yaml"
    if not credits.exists():
        credits.write_text(default_text("credits.yaml"), encoding="utf-8")
        written.append(str(credits))
    sec = cfg.home / "secrets.env"
    existing = read_env_file(sec).get("SWITCHBOARD_TOKEN", "") if sec.exists() else ""
    if not token:
        token = existing if (existing and existing != PLACEHOLDER) else (secrets.token_urlsafe(32) if role == "hub" else PLACEHOLDER)
    if not sec.exists():
        sec.write_text(SECRETS_TEMPLATE.format(token=token), encoding="utf-8")
        written.append(str(sec))
    elif token != existing:
        write_env_value(sec, "SWITCHBOARD_TOKEN", token)
    with contextlib.suppress(OSError):
        os.chmod(sec, 0o600)
    return {"written": written, "token": token, "role": role, "name": name}


def dashboard_url(role: str, home: Path | None = None) -> str:
    cfg = Config(home)
    if role == "hub":
        return f"http://localhost:{cfg.server.get('port', 8765)}"
    port = cfg.node.get("local_dashboard_port", 8766)
    return f"http://localhost:{port}" if port else cfg.hub_url


def apply(role: str, name: str, hub_url: str | None = None, token: str | None = None, code: str | None = None,
          home: Path | None = None, start: bool = True) -> dict:
    """Everything the wizard's Finish button does: pair, write files, (re)register the background programs."""
    name = clean_name(name)
    if role == "agent":
        hub_url = clean_hub_url(hub_url or "")
        check_hub(hub_url)
        if code:
            token = pair(hub_url, code, name)
        elif token:
            verify_token(hub_url, token.strip())
            token = token.strip()
        else:
            raise SetupError("Enter the pairing code shown on the hub (or its token).")
    res = write_config(role, name, hub_url, token, home)
    msgs: list[str] = []
    if start:
        from . import service

        with contextlib.suppress(Exception):
            service.uninstall("hub")  # switching role: remove the old start-at-login entries first
        msgs.append(service.install(role))
    res.update(messages=msgs, dashboard_url=dashboard_url(role, home))
    if role == "agent":
        res.pop("token", None)  # the agent's copy is the hub's secret; no need to show it
    return res
