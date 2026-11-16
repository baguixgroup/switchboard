"""Loading config.yaml, credits.yaml, models.yaml and secrets.env from the config folder."""
from __future__ import annotations

import copy
import os
import re
import shutil
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from . import paths

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def default_text(name: str) -> str:
    return resources.files("switchboard").joinpath("defaults", name).read_text(encoding="utf-8")


def load_default(name: str) -> dict:
    return yaml.safe_load(default_text(name)) or {}


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        values[key.strip()] = val
    return values


def write_env_value(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out, done = [], False
    for line in lines:
        if line.split("=", 1)[0].strip() == key:
            out.append(f"{key}={value}")
            done = True
        else:
            out.append(line)
    if not done:
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


class Config:
    def __init__(self, home: Path | None = None):
        self.home = Path(home) if home else paths.config_dir()
        self.path = self.home / "config.yaml"
        self.user_raw: dict = {}
        if self.path.exists():
            self.user_raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        self.raw = deep_merge(load_default("config.yaml"), self.user_raw)
        self.secrets = read_env_file(self.home / "secrets.env")

    # ---- basic accessors -------------------------------------------------
    @property
    def exists(self) -> bool:
        return self.path.exists()

    @property
    def node(self) -> dict:
        return self.raw.get("node") or {}

    @property
    def node_name(self) -> str:
        return self.node.get("name") or "my-machine"

    @property
    def role(self) -> str:
        return self.node.get("role") or "hub"

    @property
    def hub_url(self) -> str:
        if self.role == "hub":
            port = self.server.get("port", 8765)
            return f"http://127.0.0.1:{port}"
        return (self.node.get("hub_url") or "http://127.0.0.1:8765").rstrip("/")

    @property
    def server(self) -> dict:
        return self.raw.get("server") or {}

    @property
    def router(self) -> dict:
        return self.raw.get("router") or {}

    @property
    def permissions(self) -> dict:
        return self.raw.get("permissions") or {}

    @property
    def providers(self) -> dict:
        return self.raw.get("providers") or {}

    @property
    def mcp_servers(self) -> dict:
        return self.raw.get("mcp_servers") or {}

    @property
    def devices(self) -> dict:
        return self.raw.get("devices") or {}

    @property
    def servers(self) -> dict:
        return self.raw.get("servers") or {}

    @property
    def notify(self) -> dict:
        return self.raw.get("notify") or {}

    @property
    def output_dir(self) -> Path:
        val = self.raw.get("output_dir") or "auto"
        p = paths.default_output_dir() if val == "auto" else paths.expand(str(val))
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def db_path(self) -> Path:
        d = self.home / "data"
        d.mkdir(parents=True, exist_ok=True)
        return d / "switchboard.db"

    @property
    def token(self) -> str:
        return self.secret("SWITCHBOARD_TOKEN") or ""

    # ---- secrets -----------------------------------------------------------
    def secret(self, name: str | None) -> str | None:
        if not name:
            return None
        return os.environ.get(name) or self.secrets.get(name) or None

    def substitute(self, value: Any) -> Any:
        """Replace ${VAR} with secrets and {output_dir} with this machine's output folder."""
        if isinstance(value, str):
            value = value.replace("{output_dir}", str(self.output_dir))
            return _VAR.sub(lambda m: self.secret(m.group(1)) or "", value)
        if isinstance(value, list):
            return [self.substitute(v) for v in value]
        if isinstance(value, dict):
            return {k: self.substitute(v) for k, v in value.items()}
        return value

    # ---- other files -------------------------------------------------------
    def credits_raw(self) -> dict:
        p = self.home / "credits.yaml"
        if p.exists():
            return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return load_default("credits.yaml")

    def save_credits(self, data: dict) -> None:
        p = self.home / "credits.yaml"
        if p.exists():
            shutil.copyfile(p, p.with_suffix(".yaml.bak"))
        p.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")

    def models_override(self) -> list[dict]:
        p = self.home / "models.yaml"
        if p.exists():
            return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("models") or []
        return []

    def save_models_override(self, models: list[dict]) -> None:
        p = self.home / "models.yaml"
        if p.exists():
            shutil.copyfile(p, p.with_suffix(".yaml.bak"))
        p.write_text(yaml.safe_dump({"models": models}, sort_keys=False, allow_unicode=True), encoding="utf-8")

    def set_section(self, key: str, value: Any) -> None:
        """Update one top-level section of config.yaml (keeps a .bak copy; comments are not preserved)."""
        if self.path.exists():
            shutil.copyfile(self.path, self.path.with_suffix(".yaml.bak"))
        self.user_raw[key] = value
        self.path.write_text(yaml.safe_dump(self.user_raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
        self.raw = deep_merge(load_default("config.yaml"), self.user_raw)
