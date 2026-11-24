"""Local, experimental child-policy checks; never authority to enable child chat.

Patterns are deliberately auditable and limited. A no-match is not a safety
certification. Only parent review paths can exercise this policy in this phase.
"""
from __future__ import annotations

import asyncio
import re
import secrets
import time
import unicodedata
from dataclasses import dataclass

from .privacy import normalize
from .security import SecurityDenied, protected_mode

VERSION = "local-child-review-v1"
AGES = ("6-8", "9-12", "13-17")
LANGUAGES = ("en", "fr", "ar", "ary")
CATEGORIES = ("abuse_disclosure", "self_harm", "violence", "sexual", "dangerous_activity",
              "secrecy_dependency", "private_contact", "health", "age_sensitive")


@dataclass(frozen=True)
class ChildPolicy:
    enabled: bool = False
    age_band: str = "9-12"
    language: str = "en"
    review_queue: bool = False


def child_policy(config):
    protected = protected_mode(config)
    raw = config.raw.get("security", {}).get("child_safety", {})
    if not isinstance(raw, dict) or set(raw) - {"enabled", "age_band", "language", "review_queue"}:
        raise ValueError("Invalid child safety policy")
    enabled, queue = raw.get("enabled", False), raw.get("review_queue", False)
    age, language = raw.get("age_band", "9-12"), raw.get("language", "en")
    if type(enabled) is not bool or type(queue) is not bool or age not in AGES or language not in LANGUAGES:
        raise ValueError("Invalid child safety policy")
    if enabled and not protected:
        raise ValueError("Child safety review requires protected mode")
    if queue and not enabled:
        raise ValueError("Child safety queue requires enabled review")
    return ChildPolicy(enabled, age, language, queue)


def folded(text):
    value = unicodedata.normalize("NFKD", normalize(text).casefold())
    value = "".join(c for c in value if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", value.replace("ـ", "").translate(str.maketrans("أإآى", "اااي")))


# No external moderation/classifier calls. Quotes and negations are intentionally
# not exemptions; a human reviews ambiguous matches instead of an automatic send.
PATTERNS = {
    "abuse_disclosure": r"(?:\b(?:dad|father|mom|mother|parent|teacher|someone|he|she) (?:hits?|hurts?|touches?|hit|hurt|touched) me\b|\bi (?:was|am|have been) abused\b|\b(?:papa|maman|pere|mere|professeur) me (?:frappe|touche|fait mal)\b|(?:ابي|امي|بابا|ماما|استاذي) (?:يضربني|يلمسني|كيضربني)|\b(?:baba|mama) (?:kayderbni|kaydrebni|kaymessni)\b)",
    "self_harm": r"(?:\b(?:kill|hurt|cut) myself\b|\b(?:want|wish|going) to die\b|\bsuicid\w*\b|\bme (?:tuer|faire du mal|couper)\b|\b(?:veux|vais) mourir\b|(?:اقتل نفسي|اؤذي نفسي|اوذي نفسي|اريد ان اموت|بغيت نموت|نقتل راسي)|\b(?:bghit nmout|n9tel rasi)\b)",
    "violence": r"(?:\b(?:kill|stab|shoot|poison) (?:someone|him|her|them|my|a person)\b|\b(?:tuer|poignarder|empoisonner) (?:quelqu'un|mon|ma|une personne)\b|(?:اقتل شخص|قتل شخص|نقتل شي واحد|اطعن شخص)|\bn9tel chi wa7ed\b)",
    "sexual": r"(?:\b(?:porn\w*|nudes?|sexting|sexual roleplay|sex roleplay)\b|\b(?:photos? nue?s?|pornograph\w*)\b|(?:اباحي|اباحية|صور عارية|صور عريانة)|\btsawer 3ryana\b)",
    "dangerous_activity": r"(?:\b(?:make|build) (?:a |an )?(?:bomb|weapon|explosive)\b|\b(?:take|swallow|mix) .{0,30}\b(?:pills|bleach|poison)\b|\b(?:fabriquer|construire) (?:une? )?(?:bombe|arme|explosif)\b|(?:صنع قنبلة|صنع سلاح|نشرب جافيل|شرب السم)|\b(?:nsayb bomba|nchreb javel)\b)",
    "secrecy_dependency": r"(?:\b(?:keep (?:this|it|our chat) secret|don't tell (?:anyone|your parents)|do not tell (?:anyone|your parents)|only (?:i|me) (?:can |will )?understand|you (?:only )?need (?:only )?me)\b|\b(?:garde (?:ca|ceci) secret|ne dis rien|tu n'as besoin que de moi)\b|(?:لا تخبر احدا|لا تخبري احدا|خليها سر|ما تقول لحتى واحد|غير انا اللي كنفهمك)|\b(?:matgoul l7ta wa7ed|khalliha sir)\b)",
    "private_contact": r"(?:\b(?:send|share|give) (?:me )?(?:your|a) (?:address|phone|password|location|photo)\b|\bmeet me (?:alone|in secret)\b|\b(?:donne|envoie) (?:moi )?(?:ton|ta) (?:adresse|numero|photo|position)\b|(?:ارسل عنوانك|اعطني عنوانك|ارسل صورتك|عطيني عنوانك|صيفط ليا تصويرة)|\b3tini 3onwank\b)",
    "health": r"(?:\b(?:diagnos\w*|dosage|dose of|stop taking|which medicine|medical advice)\b|\b(?:medicament|posologie|diagnostic)\b|(?:جرعة دواء|تشخيص|اي دواء|نحبس الدوا))",
    "age_sensitive": r"(?:\b(?:puberty|reproduction|contraception)\b|\b(?:puberte|contraception)\b|(?:البلوغ|التكاثر))",
}
RULES = {category: re.compile(pattern, re.I) for category, pattern in PATTERNS.items()}
URGENT = {"abuse_disclosure", "self_harm", "violence"}


@dataclass(frozen=True)
class Finding:
    categories: tuple[str, ...] = ()
    unavailable: bool = False


def detect(texts, age_band):
    if not isinstance(texts, tuple) or not 1 <= len(texts) <= 17 or any(not isinstance(t, str) or not t.strip() for t in texts) or sum(map(len, texts)) > 24000:
        raise ValueError("Invalid safety input")
    for text in texts:
        if any(c.isalpha() and not any(s in unicodedata.name(c, "") for s in ("LATIN", "ARABIC")) for c in text):
            return Finding(unavailable=True)
    value = folded("\n".join(texts))
    categories = tuple(c for c in CATEGORIES if RULES[c].search(value) and (c != "age_sensitive" or age_band == "6-8"))
    return Finding(categories)


FALLBACKS = {
    "en": (
        "Let's pause and ask a trusted adult to help with this.",
        "Thank you for telling me. You deserve safety and support. You are not in trouble for asking for help. If someone is hurting you, it is not your fault. Talk to a trusted adult, such as a teacher or another safe adult. If someone is hurting you, choose someone else. If you are in immediate danger, ask a safe adult to contact local emergency services. This app has not contacted anyone.",
        "Let's pause this request here. A trusted adult can help you find a safe and age-appropriate way to talk about it. This app has not contacted anyone.",
    ),
    "fr": (
        "Faisons une pause et demandons de l'aide a un adulte de confiance.",
        "Merci de me l'avoir dit. Tu merites de l'aide et de la securite. Tu n'as rien fait de mal en demandant de l'aide. Si quelqu'un te fait du mal, ce n'est pas ta faute. Parle a un adulte de confiance, par exemple un professeur. Si cette personne te fait du mal, choisis quelqu'un d'autre. En cas de danger immediat, demande a un adulte de confiance de contacter les secours locaux. Cette application n'a contacte personne.",
        "Faisons une pause sur cette demande. Un adulte de confiance peut t'aider a en parler de facon sure et adaptee a ton age. Cette application n'a contacte personne.",
    ),
    "ar": (
        "لنتوقف قليلا ونطلب المساعدة من شخص بالغ موثوق.",
        "شكرا لانك اخبرتني. تستحق الامان والمساعدة. طلب المساعدة ليس خطا. اذا كان شخص يؤذيك فهذا ليس ذنبك. تحدث مع شخص بالغ موثوق مثل معلم او شخص اخر يحميك. اذا كان شخص يؤذيك فاختر شخصا اخر. اذا كنت في خطر فوري فاطلب من شخص بالغ امن الاتصال بخدمات الطوارئ المحلية. هذا التطبيق لم يتصل باحد.",
        "لنتوقف قليلا عند هذا الطلب. يمكن لشخص بالغ موثوق مساعدتك على الحديث عنه بطريقة امنة ومناسبة لعمرك. هذا التطبيق لم يتصل باحد.",
    ),
    "ary": (
        "نوقفو شوية ونطلبو المساعدة من شي واحد كبير وموثوق.",
        "شكرا حيت قلتي ليا. كتستاهل تكون بامان والاذى ماشي ذنبك. هضر مع شي واحد كبير وموثوق بحال استاذ ولا شي واحد كيحميك. الا كان شي واحد كيضرك اختار شي واحد اخر. الا كنت فخطر دابا طلب من شي كبير امن يتاصل بالطوارئ المحلية. هاد التطبيق ما تاصل بحتى واحد.",
        "نوقفو هاد الطلب شوية. شي واحد كبير وموثوق يقدر يعاونك تهضر عليه بطريقة امنة ومناسبة لعمرك. هاد التطبيق ما تاصل بحتى واحد.",
    ),
}


class ChildSafety:
    def __init__(self, config, security, *, now=time.time):
        self.config, self.security, self.now = config, security, now
        self.policy = child_policy(config)
        self.closed = False
        self.events, self.timers = {}, {}
        self.timeout = 1.0
        self.slot = asyncio.Lock()

    def guard(self, principal, *, enabled=False):
        self.security._require_parent(principal)
        try:
            current = child_policy(self.config)
        except (ValueError, TypeError):
            raise SecurityDenied("child_safety_not_authorized") from None
        if self.closed or current != self.policy or (enabled and (not self.security.enabled or not self.policy.enabled)):
            raise SecurityDenied("child_safety_not_authorized")

    def system(self):
        if not self.policy.enabled:
            return ""
        return (f" Child-policy review: age band {self.policy.age_band}; response language {self.policy.language}."
                " Use age-appropriate plain language. No harmful instructions, sexual content, requests for identity/contact,"
                " secret relationships or exclusive emotional dependency. Support disclosures without blame or investigation."
                " Do not promise secrecy or claim anyone was notified. Suggest a safe trusted adult, who need not be a parent.")

    async def assess(self, texts):
        try:
            result = await asyncio.wait_for(asyncio.to_thread(detect, texts, self.policy.age_band), self.timeout)
            if type(result) is not Finding or type(result.unavailable) is not bool or not isinstance(result.categories, tuple) or any(c not in CATEGORIES for c in result.categories) or len(set(result.categories)) != len(result.categories):
                raise ValueError()
            return result
        except Exception:
            return Finding(unavailable=True)

    def forget(self, key):
        self.events.pop(key, None)
        timer = self.timers.pop(key, None)
        if timer: timer.cancel()

    def prune(self):
        for key, (_, event) in list(self.events.items()):
            if event["expires"] <= self.now(): self.forget(key)

    def fallback(self, principal, finding, *, stage):
        self.guard(principal, enabled=True)
        urgent = bool(set(finding.categories) & URGENT)
        answer = FALLBACKS[self.policy.language][1 if urgent else 0 if finding.unavailable else 2]
        self.prune()
        event_id = None
        if self.policy.review_queue and len(self.events) < 32:
            event_id = secrets.token_hex(16)
            event = {"id": event_id, "categories": finding.categories, "check_unavailable": finding.unavailable,
                     "stage": stage, "created": self.now(), "expires": self.now() + 900}
            self.events[event_id] = (principal, event)
            self.timers[event_id] = asyncio.get_running_loop().call_later(900, self.forget, event_id)
        return {"answer": answer, "delivery": "local_fallback", "categories": finding.categories,
                "check_unavailable": finding.unavailable, "review_id": event_id,
                "review_queue_state": "queued" if event_id else "disabled" if not self.policy.review_queue else "full",
                "external_transfer": stage == "response", "persisted": False, "notification_sent": False,
                "child_delivery_enabled": False, "policy_version": VERSION}

    async def gate(self, principal, texts, *, stage):
        self.guard(principal, enabled=True)
        if stage not in ("input", "response"):
            raise ValueError("Invalid safety stage")
        if self.slot.locked():
            return self.fallback(principal, Finding(unavailable=True), stage=stage)
        async with self.slot:
            finding = await self.assess(texts)
        self.guard(principal, enabled=True)
        if stage == "response" and not finding.unavailable and sum(map(len, texts)) > {"6-8": 1200, "9-12": 2400, "13-17": 4000}[self.policy.age_band]:
            finding = Finding(finding.categories, unavailable=True)
        if finding.unavailable or finding.categories:
            return self.fallback(principal, finding, stage=stage)
        return None

    def incidents(self, principal):
        self.guard(principal, enabled=True)
        self.prune()
        return [dict(event) for owner, event in self.events.values() if owner == principal]

    def review(self, principal, key, action):
        self.guard(principal, enabled=True)
        self.prune()
        value = self.events.get(key)
        if not value or value[0] != principal or action not in ("acknowledge", "dismiss"):
            raise SecurityDenied("safety_review_not_authorized")
        self.forget(key)
        return {"reviewed": True, "action": action, "notification_sent": False, "persisted": False}

    def close(self):
        self.closed = True
        for key in list(self.events): self.forget(key)
