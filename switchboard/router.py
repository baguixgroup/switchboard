"""Task classification and model/effort/funding selection."""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from typing import Iterable

from .catalog import EFFORT_OUTPUT_MULT, EFFORTS, Catalog, ModelInfo, effort_rank
from .credits import Ledger, SourceStatus

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "image_gen": ["generate an image", "generate image", "create an image", "make an image", "draw", "illustration",
                  "logo", "picture of", "image of", "wallpaper", "poster", "icon for", "concept art", "photo of"],
    "image_edit": ["photoshop", "retouch", "remove the background", "remove background", "psd", "edit this image",
                   "edit the photo", "layer mask", "color grade", "colour grade"],
    "design_3d": ["blender", "3d model", "mesh", ".fbx", ".obj", "low poly", "low-poly", "rigging", "uv map"],
    "game_dev": ["unity", "unreal", "godot", "game", "sprite", "shader", "prefab", "gameobject", "monobehaviour",
                 "player controller", "level design"],
    "mobile_test": ["android", "iphone", "ios", "apk", "my phone", "on my device", "ps4", "playstation", "emulator",
                    "simulator", "test on"],
    "devops": ["deploy", "server", "hostinger", "nginx", "apache", "docker", "ssh", "dns", "domain", "ci/cd",
               "kubernetes", "vps", "ssl", "cron"],
    "data": ["csv", "excel", "spreadsheet", "dataframe", "pandas", "sql", "chart", "dataset", "pivot", "statistics"],
    "code": ["code", "function", "script", "bug", "error", "stack trace", "refactor", "python", "javascript",
             "typescript", "c#", "java", "rust", "golang", "api", "class", "compile", "unit test", "regex", "html",
             "css", "react", "endpoint", "app", "backend", "frontend", "database", "schema", "microservice*",
             "system design", "architecture", "website", "web app", "mobile app", "repo", "git"],
    "reasoning": ["prove", "proof", "calculate", "equation", "math", "logic puzzle", "probability", "algorithm"],
    "research": ["research", "compare", "latest", "news", "sources", "market", "find out", "what are the best",
                 "review of"],
    "writing": ["write", "email", "essay", "blog", "article", "story", "rewrite", "summarize", "summary",
                "translate", "letter", "caption"],
}
PRIORITY = list(CATEGORY_KEYWORDS) + ["chat"]
UP_WORDS = ["architecture", "entire", "full app", "complete app", "production", "from scratch", "complex",
            "multi-step", "end-to-end", "scalable", "whole project", "multiplayer", "optimi*", "security audit",
            "detailed", "in depth", "in-depth", "thorough", "step by step"]
DOWN_WORDS = ["quick", "simple", "short", "one line", "one-liner", "tl;dr", "briefly", "just", "small"]
OUTPUT_TOKENS = {"chat": 500, "writing": 1200, "research": 2000, "code": 2500, "game_dev": 3000, "devops": 1500,
                 "data": 1500, "reasoning": 1500, "image_edit": 800, "design_3d": 1500, "mobile_test": 800,
                 "image_gen": 0}
COMPLEXITY_OUT = [0.5, 0.8, 1.0, 1.6, 2.5]
REQUIRED_QUALITY = {1: 4.0, 2: 5.5, 3: 7.0, 4: 8.5, 5: 9.5}
EFFORT_BY_COMPLEXITY = {1: "low", 2: "low", 3: "medium", 4: "high", 5: "xhigh"}
MODE_WEIGHTS = {"balanced": (2.0, 1.0), "best": (3.0, 0.25), "cheapest": (1.0, 3.0)}


@dataclass
class TaskProfile:
    category: str
    complexity: int
    needs: list[str] = field(default_factory=list)
    tool_servers: list[str] = field(default_factory=list)
    est_input_tokens: int = 0
    est_output_tokens: int = 0
    signals: list[str] = field(default_factory=list)


@dataclass
class Plan:
    provider: str
    model: str
    model_label: str
    effort: str | None
    native_effort: str | None
    max_tokens: int
    source_id: str | None
    source_label: str | None
    est_cost_usd: float
    effective_cost_usd: float
    score: float
    profile: TaskProfile
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    alternatives: list[dict] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    manual: bool = False
    needs_confirmation: bool = False
    suggestions: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Plan":
        d = dict(d)
        d["profile"] = TaskProfile(**d["profile"])
        return cls(**d)


_PATTERNS: dict[str, re.Pattern] = {}


def _pattern(word: str) -> re.Pattern:
    """Whole-word match; a trailing * means prefix match ('optimi*' matches optimise/optimize)."""
    if word not in _PATTERNS:
        # Letters/digits are word characters; '_' and punctuation separate words (so 'unity_project' matches 'unity').
        if word.endswith("*"):
            _PATTERNS[word] = re.compile(r"(?<![a-z0-9])" + re.escape(word[:-1]))
        else:
            _PATTERNS[word] = re.compile(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])")
    return _PATTERNS[word]


def _hits(text: str, words: Iterable[str]) -> list[str]:
    return [w.rstrip("*") for w in words if _pattern(w).search(text)]


def money(x: float) -> str:
    return "<$0.0001" if 0 < x < 0.0001 else f"${x:.4f}"


def classify(prompt: str, attachments: int = 0, image_attachments: int = 0, history_tokens: int = 0,
             tool_hints: dict[str, list[str]] | None = None) -> TaskProfile:
    text = " " + prompt.lower() + " "
    scores: dict[str, int] = {}
    signals: list[str] = []
    for cat, words in CATEGORY_KEYWORDS.items():
        h = _hits(text, words)
        if h:
            scores[cat] = len(h)
            signals.append(f"{cat}: {', '.join(h[:3])}")
    if scores:
        best = max(scores.values())
        category = next(c for c in PRIORITY if scores.get(c) == best)
    else:
        category = "chat"

    words = len(prompt.split())
    c = 1 if words < 12 else 2 if words < 50 else 3 if words < 150 else 4
    up, down = _hits(text, UP_WORDS), _hits(text, DOWN_WORDS)
    c += min(2, len(up)) - min(1, len(down))
    if category in ("code", "game_dev", "reasoning", "devops", "design_3d"):
        c = max(c, 2)
    if category == "chat" and not up:
        c = min(c, 3)
    c = max(1, min(5, c))
    if up:
        signals.append("harder: " + ", ".join(up[:3]))
    if down:
        signals.append("easier: " + ", ".join(down[:3]))

    needs: list[str] = []
    if image_attachments:
        needs.append("vision")
    if category == "image_gen":
        needs.append("image_gen")

    servers = []
    for name, hints in (tool_hints or {}).items():
        if _hits(text, [name.lower()] + [h.lower() for h in hints]):
            servers.append(name)
    if servers:
        needs.append("tools")

    est_in = int(len(prompt) / 3.5) + 300 + attachments * 1600 + history_tokens
    if est_in > 150_000:
        needs.append("long_context")
    est_out = int(OUTPUT_TOKENS.get(category, 800) * COMPLEXITY_OUT[c - 1])
    return TaskProfile(category, c, needs, servers, est_in, est_out, signals)


def recommended_effort(complexity: int, mode: str) -> str:
    level = EFFORTS.index(EFFORT_BY_COMPLEXITY[complexity])
    if mode == "best" and complexity >= 4:
        level += 1
    if mode == "cheapest":
        level -= 1
    return EFFORTS[max(0, min(len(EFFORTS) - 1, level))]


class Router:
    def __init__(self, catalog: Catalog, ledger: Ledger, router_cfg: dict | None = None):
        self.catalog = catalog
        self.ledger = ledger
        self.cfg = router_cfg or {}

    def _eligible(self, m: ModelInfo, profile: TaskProfile, available: set[str]) -> bool:
        if m.provider not in available:
            return False
        if "image_gen" in profile.needs:
            return "image_gen" in m.features
        if "image_gen" in m.features:
            return False
        if "vision" in profile.needs and "vision" not in m.features:
            return False
        if "tools" in profile.needs and "tools" not in m.features:
            return False
        if "long_context" in profile.needs and m.context < profile.est_input_tokens * 1.2:
            return False
        return True

    def _evaluate(self, m: ModelInfo, profile: TaskProfile, effort: str, mode: str,
                  source: SourceStatus) -> tuple[float, float, float, list[str]]:
        reasons: list[str] = []
        required = REQUIRED_QUALITY[profile.complexity]
        quality = m.quality(profile.category)
        shortfall = max(0.0, required - quality)
        quality_score = min(quality, required + 0.5) - 2.0 * shortfall
        overkill = max(0.0, quality - required - 1.5)
        mult = EFFORT_OUTPUT_MULT.get(effort, 1.0) if m.efforts else 1.0
        est_cost = m.cost(profile.est_input_tokens + (1500 if profile.tool_servers else 0),
                          profile.est_output_tokens * mult)
        eff_cost = est_cost * source.cost_factor
        cost_term = math.log10(1 + eff_cost * 100)
        qw, cw = MODE_WEIGHTS.get(mode, MODE_WEIGHTS["balanced"])
        score = qw * quality_score - cw * cost_term + 2.5 * source.urgency
        score -= 0.4 * overkill * (1.0 if eff_cost > 0.0005 else 0.2)
        score += float((self.cfg.get("prefer") or {}).get(m.provider, 0))
        if shortfall >= 1.5:
            reasons.append(f"weaker than ideal for this task ({quality:g} vs {required:g})")
        if source.urgency >= 0.5:
            reasons.append(f"spends {source.source.label} before it is lost")
        if source.cost_factor == 0 and est_cost == 0 and m.price_in == 0:
            reasons.append("free")
        elif source.cost_factor <= 0.05:
            reasons.append(f"covered by {source.source.label}")
        return score, est_cost, eff_cost, reasons

    def plan(self, prompt: str, available: set[str], profile: TaskProfile, provider: str | None = None,
             model: str | None = None, effort: str | None = None, mode: str | None = None) -> Plan:
        mode = mode or self.cfg.get("mode", "balanced")
        rec_effort = recommended_effort(profile.complexity, mode)
        manual = bool(model or provider or effort)
        warnings: list[str] = []

        if model:
            m = self.catalog.find(model, provider)
            if not m:
                raise ValueError(f"Unknown model '{model}'. See `switchboard models list`.")
            if m.provider not in available:
                raise ValueError(f"{m.provider} is not available here (missing API key or CLI not installed).")
            candidates = [m]
        else:
            candidates = [m for m in self.catalog.all() if self._eligible(m, profile, available)
                          and (provider is None or m.provider == provider)]
            if not candidates and "tools" in profile.needs:
                warnings.append("No funded model supports tool calling; running without tools.")
                profile.needs.remove("tools")
                profile.tool_servers = []
                candidates = [m for m in self.catalog.all() if self._eligible(m, profile, available)
                              and (provider is None or m.provider == provider)]
            if not candidates and "image_gen" in profile.needs:
                warnings.append("No image model is configured; a text model will write a detailed image prompt "
                                "instead. Add an OpenAI or xAI key for real images.")
                profile.needs.remove("image_gen")
                profile.category = "writing"
                candidates = [m for m in self.catalog.all() if self._eligible(m, profile, available)]
        if not candidates:
            raise ValueError("No usable model: add at least one API key to secrets.env or install a subscription CLI.")

        scored = []
        for m in candidates:
            use_effort = effort or rec_effort
            src = self.ledger.pick_source(m.provider, m.id)
            if src is None:
                if model:
                    raise ValueError(f"{m.provider} is over its monthly budget.")
                continue
            score, est, eff, reasons = self._evaluate(m, profile, use_effort, mode, src)
            scored.append((score, m, src, est, eff, reasons))
        if not scored:
            raise ValueError("Every candidate provider is over budget or out of credit.")
        scored.sort(key=lambda x: x[0], reverse=True)
        score, m, src, est, eff, reasons = scored[0]

        chosen_effort = effort or rec_effort
        if effort and m.efforts and effort_rank(effort) - effort_rank(rec_effort) >= 2:
            _, rec_est, _, _ = self._evaluate(m, profile, rec_effort, mode, src)
            msg = (f"'{effort}' effort looks like overkill for a complexity-{profile.complexity} {profile.category} task; "
                   f"'{rec_effort}' should do (est. {money(rec_est)} instead of {money(est)}).")
            if self.cfg.get("auto_downgrade_effort"):
                chosen_effort = rec_effort
                est = rec_est
                eff = rec_est * src.cost_factor
                warnings.append(msg + " Lowered automatically.")
            else:
                warnings.append(msg)
        if effort and effort_rank(effort) < effort_rank(rec_effort) - 1:
            warnings.append(f"'{effort}' effort may be too low for this task; '{rec_effort}' is recommended.")
        if src.note:
            warnings.append(f"{src.source.label}: {src.note}")

        if manual and len(scored) == 1:
            better = self._best_alternative(prompt, available, profile, mode, exclude=m.key)
            if better and better["effective_cost_usd"] < eff * 0.5 and better["score"] >= score:
                warnings.append(f"Cheaper option: {better['label']} (est. {money(better['effective_cost_usd'])}) "
                                f"- {', '.join(better['reasons']) or 'similar quality'}.")

        max_tokens = max(2048, min(64000, int(profile.est_output_tokens * 2.5 * EFFORT_OUTPUT_MULT.get(chosen_effort, 1))))
        if profile.category in ("code", "game_dev"):
            max_tokens = max(max_tokens, 8192)

        why = [f"{profile.category}, complexity {profile.complexity}/5"]
        if m.efforts:
            why.append(f"effort {chosen_effort}")
        why += reasons
        alternatives = [
            {"key": mm.key, "label": mm.label or mm.id, "score": round(s, 2), "est_cost_usd": round(e, 5),
             "effective_cost_usd": round(ef, 5), "source": ss.source.label, "reasons": rr}
            for s, mm, ss, e, ef, rr in scored[1:5]
        ]
        confirm_above = float(self.cfg.get("confirm_above_usd", 0.5))
        return Plan(
            provider=m.provider, model=m.id, model_label=m.label or m.id, effort=chosen_effort if m.efforts else None,
            native_effort=m.native_effort(chosen_effort), max_tokens=max_tokens,
            source_id=src.source.id, source_label=src.source.label, est_cost_usd=round(est, 5),
            effective_cost_usd=round(eff, 5), score=round(score, 2), profile=profile, reasons=why,
            warnings=warnings, alternatives=alternatives, tools=list(profile.tool_servers), manual=manual,
            needs_confirmation=eff > confirm_above,
        )

    def _best_alternative(self, prompt: str, available: set[str], profile: TaskProfile, mode: str,
                          exclude: str) -> dict | None:
        try:
            auto = self.plan(prompt, available, TaskProfile(**asdict(profile)), mode=mode)
        except ValueError:
            return None
        if f"{auto.provider}:{auto.model}" == exclude:
            return None
        return {"label": auto.model_label, "effective_cost_usd": auto.effective_cost_usd, "score": auto.score,
                "reasons": [r for r in auto.reasons if "complexity" not in r and not r.startswith("effort")]}
