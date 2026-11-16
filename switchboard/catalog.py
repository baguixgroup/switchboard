"""Model catalog: prices, quality, native effort levels and features."""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import Config, load_default

# Universal effort levels shown to the user, lowest to highest.
EFFORTS = ["low", "medium", "high", "xhigh", "max"]
_RANK = {"none": -1, "minimal": 0, "low": 1, "medium": 2, "high": 3, "xhigh": 4, "max": 5}
# Reasoning tokens are billed as output: rough output multiplier per effort level.
EFFORT_OUTPUT_MULT = {"low": 0.6, "medium": 1.0, "high": 1.6, "xhigh": 2.2, "max": 3.0}


def effort_rank(level: str | None) -> int:
    return _RANK.get((level or "").lower(), 2)


@dataclass
class ModelInfo:
    provider: str
    id: str
    label: str = ""
    tier: str = "strong"
    price_in: float = 0.0
    price_out: float = 0.0
    per_call: float = 0.0
    context: int = 128000
    q: float = 7.0
    skills: dict = field(default_factory=dict)
    efforts: list = field(default_factory=list)
    features: set = field(default_factory=set)
    verified: bool = False

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.id}"

    def quality(self, category: str) -> float:
        return float(self.skills.get(category, self.q))

    def cost(self, input_tokens: float, output_tokens: float) -> float:
        return (input_tokens * self.price_in + output_tokens * self.price_out) / 1_000_000 + self.per_call

    def native_effort(self, level: str | None) -> str | None:
        """Map a universal effort level to the closest value this model supports (never above the request)."""
        if not level or not self.efforts:
            return None
        want = effort_rank(level)
        ranked = sorted(self.efforts, key=effort_rank)
        below = [e for e in ranked if effort_rank(e) <= want]
        return below[-1] if below else ranked[0]

    def to_dict(self) -> dict:
        return {
            "key": self.key, "provider": self.provider, "id": self.id, "label": self.label or self.id,
            "tier": self.tier, "price": [self.price_in, self.price_out], "per_call": self.per_call,
            "context": self.context, "q": self.q, "skills": self.skills, "efforts": self.efforts,
            "features": sorted(self.features), "verified": self.verified,
        }


def _parse(entry: dict) -> ModelInfo:
    price = entry.get("price") or [0, 0]
    return ModelInfo(
        provider=entry["provider"], id=str(entry["id"]), label=entry.get("label", ""),
        tier=entry.get("tier", "strong"), price_in=float(price[0]), price_out=float(price[1]),
        per_call=float(entry.get("per_call", 0)), context=int(entry.get("context", 128000)),
        q=float(entry.get("q", 7)), skills=dict(entry.get("skills") or {}),
        efforts=list(entry.get("efforts") or []), features=set(entry.get("features") or []),
        verified=bool(entry.get("verified", False)),
    )


class Catalog:
    def __init__(self, entries: list[dict]):
        self.models: dict[str, ModelInfo] = {}
        for e in entries:
            if e.get("disabled"):
                self.models.pop(f"{e['provider']}:{e['id']}", None)
                continue
            m = _parse(e)
            self.models[m.key] = m

    @classmethod
    def load(cls, cfg: Config) -> "Catalog":
        entries = list(load_default("models.yaml").get("models") or [])
        entries += cfg.models_override()  # later entries override earlier ones with the same key
        return cls(entries)

    def all(self) -> list[ModelInfo]:
        return list(self.models.values())

    def find(self, ref: str, provider: str | None = None) -> ModelInfo | None:
        """Find by 'provider:id', or by id (optionally restricted to a provider)."""
        if ref in self.models:
            return self.models[ref]
        if ":" in ref and not provider:
            p, _, mid = ref.partition(":")
            if f"{p}:{mid}" in self.models:
                return self.models[f"{p}:{mid}"]
        for m in self.models.values():
            if m.id == ref and (provider is None or m.provider == provider):
                return m
        return None

    def for_provider(self, provider: str) -> list[ModelInfo]:
        return [m for m in self.models.values() if m.provider == provider]
