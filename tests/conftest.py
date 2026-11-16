import socket
import sys
from pathlib import Path

import pytest
import yaml

FIXTURES = Path(__file__).parent / "fixtures"

MOCK_MODELS = [
    {"provider": "mock", "id": "mock-large", "label": "Mock Large", "q": 9, "price": [1, 4],
     "efforts": ["low", "medium", "high", "xhigh", "max"], "features": ["tools", "vision"]},
    {"provider": "mock", "id": "mock-small", "label": "Mock Small", "q": 6.5, "price": [0.1, 0.4], "features": ["tools"]},
]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_home(path: Path, *, role="hub", name="hub", port=8765, hub_url=None, providers=None, models=None,
              credits=None, mcp_servers=None, router=None, token="test-token") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    cfg = {
        "node": {"name": name, "role": role, "hub_url": hub_url or f"http://127.0.0.1:{port}", "mirror_outputs": True},
        "server": {"host": "127.0.0.1", "port": port},
        "output_dir": str(path / "out"),
        # Only offline providers in tests: never touch a real API.
        "providers": {n: {"disabled": True} for n in ("anthropic", "openai", "deepseek", "xai", "gemini", "github",
                                                     "openrouter", "mistral", "ollama", "claude-code", "codex",
                                                     "gemini-cli", "copilot-cli", "grok-build")} | (providers or {"mock": {"kind": "mock"}}),
        "mcp_servers": mcp_servers or {},
        "router": router or {},
        "notify": {"ntfy_topic": ""},
    }
    (path / "config.yaml").write_text(yaml.safe_dump(cfg))
    (path / "models.yaml").write_text(yaml.safe_dump({"models": models if models is not None else MOCK_MODELS}))
    (path / "credits.yaml").write_text(yaml.safe_dump({"sources": credits or []}))
    (path / "secrets.env").write_text(f"SWITCHBOARD_TOKEN={token}\n")
    return path


@pytest.fixture
def echo_server_cfg():
    return {"command": sys.executable, "args": [str(FIXTURES / "echo_mcp.py")]}
