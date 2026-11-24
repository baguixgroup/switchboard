"""Bounded, offline text minimization for parent-reviewed requests.

Conservative patterns, local identifiers and cross-context disclosure cues are
layers, not a general named-entity model or a guarantee of anonymization.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass


class PrivacyFailure(ValueError):
    """Only fixed reason codes should leave this boundary."""

    def __init__(self, reason="privacy_check_failed"):
        allowed = {"text_limit_exceeded", "invalid_text", "unsupported_script", "privacy_check_failed",
                   "provider_request_failed", "provider_response_rejected", "invalid_text_request",
                   "privacy_gateway_busy", "preview_capacity_reached", "provider_not_configured",
                   "sandbox_unavailable", "local_tool_failed"}
        super().__init__(reason if reason in allowed else "privacy_check_failed")


def normalize(text: str) -> str:
    value = unicodedata.normalize("NFKC", text)
    value = "".join(c for c in value if unicodedata.category(c) not in ("Cf", "Mn") and (unicodedata.category(c) != "Cc" or c in "\n\t"))
    value = "".join(str(unicodedata.decimal(c)) if c.isdecimal() else c for c in value)
    return value.replace("’", "'").replace("`", "'")


@dataclass(frozen=True)
class MinimizedText:
    texts: tuple[str, ...]
    categories: tuple[tuple[str, int], ...]


# Long or ambiguous matches deliberately lose useful precision in this review path.
PATTERNS = (
    ("email", re.compile(r"[\w.+-]{1,64}\s{0,4}(?:@|\[at\]|\(at\)|\bat\b|\barobase\b)\s{0,4}[\w-]{1,63}(?:\s{0,4}(?:\.|\[dot\]|\(dot\)|\bdot\b|\bpoint\b)\s{0,4}[\w-]{1,63}){1,5}", re.I)),
    ("secret", re.compile(r"\b(?:sk-[A-Za-z0-9_-]{8,}|[A-Za-z0-9_-]{32,})\b")),
    ("credential", re.compile(r"\b(?:password|passwd|api[_ -]?key|access[_ -]?token|mot de passe)\s*[:=]\s*\S+", re.I)),
    ("url", re.compile(r"(?:https?://|www\.)[^\s<>]+", re.I)),
    ("address", re.compile(r"\b\d{1,5}\s+(?:[\w'-]+\s+){0,5}(?:street|st|road|rd|avenue|ave|lane|ln|boulevard|drive)\b", re.I)),
    ("address", re.compile(r"\b\d{1,5}\s+(?:rue|avenue|boulevard)\s+[^\n.!?]{1,80}", re.I)),
    ("date", re.compile(r"\b(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\b")),
    ("coordinates", re.compile(r"[-+]?\d{1,3}\.\d{3,}\s*[,;]\s*[-+]?\d{1,3}\.\d{3,}")),
    ("phone", re.compile(r"(?<!\w)\+?\d[\d\s().-]{5,80}\d(?!\w)")),
    ("handle", re.compile(r"(?<!\w)@[\w.-]{2,}")),
    ("postal", re.compile(r"\b[A-Z]\d[A-Z]\s?\d[A-Z]\d\b", re.I)),
)

# Whole-clause removal covers identity in prose without guessing entity boundaries.
IDENTITY_CUES = (
    "my name is", "call me", "i live", "my address", "my school", "i go to school", "my birthday",
    "my mother", "my father", "my sister", "my brother", "my friend", "my teacher", "my username", "my location",
    "je m'appelle", "mon nom", "j'habite", "mon adresse", "mon école", "mon ecole", "ma naissance",
    "ma mère", "ma mere", "mon père", "mon pere", "ma soeur", "mon frère", "mon frere",
    "اسمي", "إسمي", "أسكن", "اسكن", "عنواني", "مدرستي", "ولدت", "امي", "أمي", "أبي", "صديقي", "أنا", "انا",
    "smiti", "smyti", "smiya dyali", "sakn", "sakna", "kan9ra", "kanqra", "كنسكن", "سميتي", "كنقرا",
)
FEATURES = {
    "location": ("near", "street", "avenue", "school", "village", "neighborhood", "quartier", "près", "pres de", "rue", "école", "ecole", "مدرسة", "حي", "قرب", "7da", "mdrasa"),
    "schedule": ("tomorrow", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "every day", "after class", "demain", "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche", "غدا", "الساعة", "الجمعة", "ghdda", "ghda"),
    "activity": ("only", "violin", "club", "team", "practice", "seul", "violon", "équipe", "equipe", "entraînement", "entrainement", "الوحيد", "فريق", "تدريب", "kaman"),
}
STRUCTURED_IDENTITY = re.compile(
    r"(?:^|[\s{,])[\"']?(?:name|full[_ -]?name|nom|pr[eé]nom|school|[eé]cole|age|[aâ]ge|dob|birth[_ -]?date|birthday|address|adresse|phone|telephone|t[eé]l[eé]phone|email|parent|family|device[_ -]?id|user[_ -]?id|location|gps|password|api[_ -]?key|token|اسم|الاسم|العنوان|المدرسة|الهاتف|العمر)[\"']?\s*[:=]", re.I)


def _has_cue(text: str, cues: tuple[str, ...]) -> bool:
    # Word boundaries avoid treating e.g. "schooling" as the token "school".
    return any(re.search(r"(?<!\w)" + re.escape(cue) + r"(?!\w)", text, re.I) for cue in cues)


class LocalTextMinimizer:
    def __init__(self, known_identifiers: tuple[str, ...] = ()):
        self.known = tuple(normalize(value).strip().casefold() for value in known_identifiers)

    def minimize(self, texts: tuple[str, ...]) -> MinimizedText:
        if not texts or len(texts) > 17 or sum(len(t) for t in texts) > 24000:
            raise PrivacyFailure("text_limit_exceeded")
        if any(not isinstance(t, str) or not t.strip() for t in texts):
            raise PrivacyFailure("invalid_text")
        normalized = tuple(normalize(t) for t in texts)
        # Unknown scripts need a reviewed local detector; never forward speculatively.
        for text in normalized:
            for c in text:
                if c.isalpha() and not any(part in unicodedata.name(c, "") for part in ("LATIN", "ARABIC")):
                    raise PrivacyFailure("unsupported_script")
        features = [{name for name, cues in FEATURES.items() if _has_cue(t, cues)} for t in normalized]
        all_features = set().union(*features)
        linkage_risk = len(all_features) >= 2
        counts: Counter = Counter()
        result = []
        for text, observed in zip(normalized, features):
            if linkage_risk and observed:
                result.append("[removed linked identifying context]")
                counts["indirect_context"] += 1
                continue
            lines = []
            continuation = False
            for line in text.splitlines():
                if not line.strip():
                    continuation = False
                    lines.append(line)
                    continue
                sensitive = STRUCTURED_IDENTITY.search(line) or _has_cue(line, IDENTITY_CUES) or any(value in line.casefold() for value in self.known)
                if continuation or sensitive:
                    lines.append("[removed identifying context]"); counts["identity_context"] += 1
                    continuation = ((continuation and not line.rstrip().endswith(('.', '!', '?', '؟'))) or
                                    any(line.casefold().rstrip().endswith(cue.casefold()) for cue in IDENTITY_CUES) or
                                    bool(STRUCTURED_IDENTITY.search(line) and line.rstrip().endswith((':', '='))))
                else:
                    lines.append(line)
            text = "\n".join(lines)
            # No names are inferred by an external classifier. Known identifiers
            # and explicit identity clauses are removed entirely, locally.
            clauses = re.split(r"(?<=[.!?؟])\s+|\n+", text)
            cleaned = []
            for clause in clauses:
                if _has_cue(clause, IDENTITY_CUES) or any(value in clause.casefold() for value in self.known):
                    counts["identity_context"] += 1
                    cleaned.append("[removed identifying context]")
                    continue
                for category, pattern in PATTERNS:
                    clause, n = pattern.subn("[removed " + category + "]", clause)
                    counts[category] += n
                cleaned.append(clause)
            result.append(" ".join(cleaned))
        return MinimizedText(tuple(result), tuple(sorted((k, v) for k, v in counts.items() if v)))
