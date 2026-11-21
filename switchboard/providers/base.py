from __future__ import annotations

import base64
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path

from ..catalog import ModelInfo
from ..toolbox import ToolBox

DEFAULT_SYSTEM = (
    "You are working inside Tazrivo, a personal AI control plane. Be direct and complete. "
    "When you produce code or files, put each file in its own fenced code block and write the file name "
    "on the fence line, e.g. ```python main.py. Use the available tools when they help; never invent tool results."
)


class ProviderError(Exception):
    """kind: rate_limit | credit | auth | unavailable | bad_request | not_found | refused | error"""

    def __init__(self, message: str, kind: str = "error", retryable: bool = False, retry_at: float | None = None):
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable
        self.retry_at = retry_at  # epoch seconds when a plan/daily limit resets, if known

    @property
    def failover_ok(self) -> bool:
        return self.kind in ("rate_limit", "credit", "auth", "unavailable", "not_found", "refused")


@dataclass
class RunRequest:
    prompt: str
    model: ModelInfo
    effort: str | None = None  # native effort value for this model
    max_tokens: int = 8192
    system: str | None = DEFAULT_SYSTEM
    history: list[dict] = field(default_factory=list)
    images: list[Path] = field(default_factory=list)
    workdir: Path | None = None
    allow_edits: bool = False
    max_tool_rounds: int = 25
    # Plan CLIs reach Tazrivo's tools (MCP servers on every machine) through this gateway.
    tool_servers: list[str] = field(default_factory=list)
    task_rel: str | None = None  # task folder relative to the output folder
    read_dirs: list[str] = field(default_factory=list)  # extra folders plan CLIs may read (earlier tasks)


@dataclass
class RunResult:
    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    requests: int = 1
    stop_reason: str = ""
    files: list[tuple[str, bytes]] = field(default_factory=list)
    notional_cost_usd: float | None = None
    handoff: dict | None = None  # set when the task continues in a plan's own app (see providers/handoff.py)


def image_data_url(path: Path) -> tuple[str, str]:
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    return mime, base64.standard_b64encode(Path(path).read_bytes()).decode("ascii")


class Provider:
    kind = "base"

    def __init__(self, name: str, cfg: dict, config):
        self.name = name
        self.cfg = cfg
        self.config = config

    def available(self) -> bool:
        raise NotImplementedError

    async def run(self, req: RunRequest, toolbox: ToolBox | None = None) -> RunResult:
        raise NotImplementedError

    async def list_models(self) -> list[str]:
        return []

    def describe(self) -> dict:
        return {"name": self.name, "kind": self.kind, "available": self.available()}
