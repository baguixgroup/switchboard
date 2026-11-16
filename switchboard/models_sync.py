"""Reconcile the model catalog with what your keys can actually see, plus current public prices."""
from __future__ import annotations

import asyncio
import re

import httpx

OPENROUTER_PREFIX = {"openai": "openai", "anthropic": "anthropic", "x-ai": "xai", "google": "gemini",
                     "deepseek": "deepseek", "mistralai": "mistral"}
SKIP = re.compile(r"embed|tts|whisper|dall-e|moderation|audio|realtime|transcri|search-preview|babbage|davinci|"
                  r"computer-use|aqa|imagen|veo|learnlm|gemma|vision-preview|-instruct$", re.I)


async def openrouter_prices() -> dict[tuple[str, str], tuple[float, float]]:
    """(provider, model id) -> (input, output) USD per 1M tokens, from OpenRouter's public list."""
    async with httpx.AsyncClient(timeout=30) as http:
        r = await http.get("https://openrouter.ai/api/v1/models")
        r.raise_for_status()
    out = {}
    for m in r.json().get("data", []):
        prefix, _, mid = m.get("id", "").partition("/")
        prov = OPENROUTER_PREFIX.get(prefix)
        pricing = m.get("pricing") or {}
        try:
            pin, pout = float(pricing.get("prompt", 0)) * 1e6, float(pricing.get("completion", 0)) * 1e6
        except (TypeError, ValueError):
            continue
        if prov and pin >= 0 and pout >= 0:
            out[(prov, mid)] = (round(pin, 4), round(pout, 4))
            out.setdefault((prov, mid.replace(".", "-")), (round(pin, 4), round(pout, 4)))
    return out


def guess(provider: str, mid: str) -> dict:
    low = mid.lower()
    if re.search(r"nano|lite|mini|flash|haiku|small|fast", low):
        tier, q = "fast", 6.5
    elif re.search(r"pro|opus|max|ultra|large|fable|-6|reasoner", low):
        tier, q = "frontier", 9.0
    else:
        tier, q = "strong", 8.0
    efforts: list[str] = []
    if provider == "anthropic":
        efforts = ["low", "medium", "high", "xhigh", "max"]
    elif provider == "openai" and re.match(r"(gpt-[5-9]|o\d)", low):
        efforts = ["minimal", "low", "medium", "high"]
    elif provider == "gemini" and re.search(r"gemini-(2\.5|[3-9])", low):
        efforts = ["low", "medium", "high"]
    feats = ["tools"] + (["vision"] if provider in ("openai", "anthropic", "gemini", "xai") else [])
    if re.search(r"image", low):
        feats = ["image_gen"]
    return {"tier": tier, "q": q, "efforts": efforts, "features": feats}


async def sync(engine, write: bool = False) -> dict:
    cfg = engine.config
    report: dict = {"providers": {}, "added": [], "missing": [], "repriced": []}
    try:
        prices = await openrouter_prices()
    except Exception as exc:
        prices = {}
        report["price_error"] = str(exc)
    avail = engine.available_providers(refresh=True)
    overrides = {(m["provider"], str(m["id"])): m for m in cfg.models_override()}

    async def listing(name: str):
        try:
            return name, await engine.providers[name].list_models(), None
        except Exception as exc:
            return name, [], str(exc)

    api_providers = [n for n in avail if engine.providers[n].kind in ("openai_compat", "anthropic")]
    for name, live, err in await asyncio.gather(*(listing(n) for n in api_providers)):
        report["providers"][name] = {"models": len(live), "error": err}
        if err or not live:
            continue
        live_set = set(live)
        for m in engine.catalog.for_provider(name):
            if m.id not in live_set:
                report["missing"].append(m.key)
                if write:  # not visible to this key: stop routing to it
                    overrides[(name, m.id)] = {"provider": name, "id": m.id, "disabled": True}
            else:
                p = prices.get((name, m.id))
                if p and (abs(p[0] - m.price_in) > 1e-6 or abs(p[1] - m.price_out) > 1e-6):
                    report["repriced"].append({"model": m.key, "old": [m.price_in, m.price_out], "new": list(p)})
                if write:
                    base = overrides.get((name, m.id)) or {"provider": name, "id": m.id}
                    base.update({"verified": True})
                    if p:
                        base["price"] = list(p)
                    overrides[(name, m.id)] = base
        for mid in live:
            if engine.catalog.find(f"{name}:{mid}") or SKIP.search(mid):
                continue
            p = prices.get((name, mid))
            entry = {"provider": name, "id": mid, "label": mid, "price": list(p) if p else [0, 0], "verified": bool(p),
                     **guess(name, mid)}
            if not p:
                entry["note"] = "price unknown: edit before relying on cost estimates"
            report["added"].append(f"{name}:{mid}")
            if write:
                overrides[(name, mid)] = entry
    if write:
        cleaned = []
        for e in overrides.values():
            e = {k: v for k, v in e.items() if k != "key"}
            cleaned.append(e)
        cfg.save_models_override(cleaned)
    return report
