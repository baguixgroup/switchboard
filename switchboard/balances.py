"""Live balance readers for providers that expose one (DeepSeek, OpenRouter).

OpenAI, Anthropic, xAI and Google do not expose prepaid balances or promo-credit expiry via
their APIs; for those, add the grant to credits.yaml and Tazrivo tracks spend locally.
"""
from __future__ import annotations

import httpx


async def fetch_balance(name: str, cfg: dict, config) -> dict | None:
    kind = cfg.get("balance")
    key = config.secret(cfg.get("api_key_env")) or cfg.get("api_key")
    if not kind or not key:
        return None
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=20) as http:
        if kind == "deepseek":
            r = await http.get(cfg.get("base_url", "https://api.deepseek.com").rstrip("/") + "/user/balance", headers=headers)
            r.raise_for_status()
            data = r.json()
            out: dict = {"available": data.get("is_available"), "raw": data}
            for info in data.get("balance_infos") or []:
                cur = (info.get("currency") or "").upper()
                total = float(info.get("total_balance") or 0)
                if cur == "USD":
                    out["usd"] = total
                    out["granted"] = float(info.get("granted_balance") or 0)
                else:
                    out[cur] = total
            return out
        if kind == "openrouter":
            r = await http.get("https://openrouter.ai/api/v1/credits", headers=headers)
            r.raise_for_status()
            d = r.json().get("data") or {}
            total, used = float(d.get("total_credits") or 0), float(d.get("total_usage") or 0)
            return {"usd": total - used, "total_credits": total, "total_usage": used}
    return None


async def refresh_all(config, store) -> dict[str, dict]:
    results: dict[str, dict] = {}
    for name, cfg in (config.providers or {}).items():
        if not cfg or not cfg.get("balance"):
            continue
        try:
            data = await fetch_balance(name, cfg, config)
        except Exception as exc:
            results[name] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        if data is not None:
            store.save_balance(name, data)
            results[name] = {k: v for k, v in data.items() if k != "raw"}
    return results
