"""Tool advisor: which app or MCP server would do this task better, and how to install it per machine."""
from __future__ import annotations

from .inventory import mcp_catalog, tools_catalog


def _install_cmd(tool: dict, plat: str) -> str | None:
    inst = tool.get("install") or {}
    return inst.get(plat) or (inst.get("linux") if plat == "pi" else None)


def suggest(category: str, hinted_servers: list[str], nodes: list[dict], limit: int = 4) -> list[dict]:
    tools = tools_catalog()
    mcps = mcp_catalog()
    online = [n for n in nodes if n.get("online")]
    out: list[dict] = []

    # 1. MCP servers the prompt hints at but no machine runs yet.
    running = {s for n in online for s in (n.get("mcp") or {})}
    for server in hinted_servers:
        if server in running or server not in mcps:
            continue
        entry = mcps[server]
        platforms = entry.get("platforms")
        eligible = [n["node"] for n in online if not platforms or n.get("platform") in platforms or
                    (n.get("platform") == "pi" and "linux" in (platforms or []))]
        out.append({"type": "mcp", "id": server, "name": f"{server} MCP server", "why": entry.get("description", ""),
                    "setup": entry.get("setup"), "repo": entry.get("repo"), "nodes": eligible,
                    "command": f"switchboard mcp add {server}"})

    # 2. Desktop tools that help with this category and are missing everywhere
    #    (one per group, skipped when an equivalent from the same group is already installed).
    def installed_anywhere(tool_id: str) -> bool:
        return any((n.get("tools") or {}).get(tool_id, {}).get("installed") for n in online)

    covered = {t.get("group") for tid, t in tools.items() if t.get("group") and installed_anywhere(tid)}
    tool_suggestions = 0
    for tid, t in tools.items():
        if category not in (t.get("helps") or []) or installed_anywhere(tid) or tool_suggestions >= 2:
            continue
        if t.get("group") and t["group"] in covered:
            continue
        options = []
        for n in online:
            plat = n.get("platform", "linux")
            if (t.get("unsupported") or {}).get(plat):
                continue
            cmd = _install_cmd(t, plat)
            if cmd:
                options.append({"node": n["node"], "platform": plat, "command": cmd, "manual": bool(t.get("manual"))})
        if options:
            out.append({"type": "tool", "id": tid, "name": t.get("name", tid),
                        "why": f"helps with {category.replace('_', ' ')} tasks", "install": options})
            tool_suggestions += 1
            if t.get("group"):
                covered.add(t["group"])
    return out[:limit]


def all_missing(nodes: list[dict]) -> list[dict]:
    """Every catalog tool missing on each online node, with the install command for that node."""
    out = []
    for tid, t in tools_catalog().items():
        rows = []
        for n in nodes:
            if not n.get("online"):
                continue
            plat = n.get("platform", "linux")
            st = (n.get("tools") or {}).get(tid, {})
            if st.get("installed") or (t.get("unsupported") or {}).get(plat):
                continue
            cmd = _install_cmd(t, plat)
            if cmd:
                rows.append({"node": n["node"], "platform": plat, "command": cmd, "manual": bool(t.get("manual"))})
        if rows:
            out.append({"id": tid, "name": t.get("name", tid), "helps": t.get("helps", []), "install": rows})
    return out
