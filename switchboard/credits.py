"""Credit ledger: prepaid grants with expiry, free tiers that reset, subscriptions, pay-as-you-go budgets.

The ledger answers two questions:
  * which funding source should pay for this call?  (pick_source: expiring money first, free quota next)
  * what should the user be told?                  (alerts: expiring credit, unused quota about to reset, budgets)
"""
from __future__ import annotations

import calendar
import fnmatch
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


def local_tz():
    return datetime.now().astimezone().tzinfo


def get_tz(name: str | None):
    if not name or name.lower() == "local":
        return local_tz()
    if ZoneInfo is None:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def parse_dt(value, tz=None) -> datetime | None:
    """Parse a date/datetime. A bare date means 00:00 that day (conservative for expiries)."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    else:
        s = str(value).strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz or local_tz())
    return dt


def human_delta(td: timedelta) -> str:
    secs = int(td.total_seconds())
    if secs <= 0:
        return "now"
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    if days:
        return f"{days}d {hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h {mins}m" if mins else f"{hours}h"
    return f"{max(mins, 1)}m"


def fmt_when(dt: datetime) -> str:
    return dt.astimezone(local_tz()).strftime("%a %d %b %H:%M")


# --------------------------------------------------------------------------------------------
@dataclass
class Schedule:
    every: str = "day"
    at: str = "00:00"
    weekday: str = "mon"
    day: int = 1
    tz: str = "UTC"
    anchor: str | None = None

    @classmethod
    def parse(cls, d: dict | str | None) -> "Schedule | None":
        if not d:
            return None
        if isinstance(d, str):
            return cls(every=d)
        return cls(every=str(d.get("every", "day")), at=str(d.get("at", "00:00")),
                   weekday=str(d.get("weekday", "mon")), day=int(d.get("day", 1)),
                   tz=str(d.get("tz", "UTC")), anchor=d.get("anchor"))

    def bounds(self, now: datetime) -> tuple[datetime, datetime]:
        """Return (start of current period, next reset)."""
        tz = get_tz(self.tz)
        local = now.astimezone(tz)
        hh, mm = (int(x) for x in (self.at or "00:00").split(":")[:2])
        ev = self.every.strip().lower()
        if ev in ("hour", "hourly"):
            ev = "1h"
        m = re.fullmatch(r"(\d+)\s*([hd])", ev)
        if m:
            n = int(m.group(1))
            period = timedelta(hours=n) if m.group(2) == "h" else timedelta(days=n)
            anchor = parse_dt(self.anchor, tz) or datetime(2024, 1, 1, hh, mm, tzinfo=tz)
            k = math.floor((local - anchor) / period)
            start = anchor + k * period
            return start, start + period
        if ev in ("day", "daily"):
            cand = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
            start = cand if cand <= local else cand - timedelta(days=1)
            return start, start + timedelta(days=1)
        if ev in ("week", "weekly"):
            wd = WEEKDAYS.index(self.weekday[:3].lower())
            cand = local.replace(hour=hh, minute=mm, second=0, microsecond=0) - timedelta(days=(local.weekday() - wd) % 7)
            if cand > local:
                cand -= timedelta(days=7)
            return cand, cand + timedelta(days=7)
        if ev in ("month", "monthly"):
            def at_month(y: int, mo: int) -> datetime:
                return datetime(y, mo, min(self.day, calendar.monthrange(y, mo)[1]), hh, mm, tzinfo=tz)

            cand = at_month(local.year, local.month)
            if cand > local:
                y, mo = (local.year - 1, 12) if local.month == 1 else (local.year, local.month - 1)
                cand = at_month(y, mo)
            ny, nm = (cand.year + 1, 1) if cand.month == 12 else (cand.year, cand.month + 1)
            return cand, at_month(ny, nm)
        raise ValueError(f"unknown reset schedule: every={self.every!r}")


@dataclass
class CreditSource:
    id: str
    provider: str
    type: str  # prepaid | free_tier | subscription | payg
    label: str = ""
    amount: float | None = None
    unit: str = "usd"
    expires: datetime | None = None
    added: datetime | None = None
    renews: datetime | None = None
    reset: Schedule | None = None
    models: list = field(default_factory=list)
    auto_balance: bool = False
    monthly_budget: float | None = None
    warn_days: float = 7
    alert_before_hours: float | None = None
    implicit: bool = False

    @classmethod
    def parse(cls, d: dict) -> "CreditSource":
        typ = d.get("type", "prepaid")
        unit = d.get("unit") or ("usd" if typ in ("prepaid", "payg") else "requests")
        amt = d.get("amount")
        return cls(
            id=str(d["id"]), provider=str(d["provider"]), type=typ, label=d.get("label") or d["id"],
            amount=float(amt) if amt is not None else None, unit=unit,
            expires=parse_dt(d.get("expires")), added=parse_dt(d.get("added")), renews=parse_dt(d.get("renews")),
            reset=Schedule.parse(d.get("reset")), models=list(d.get("models") or []),
            auto_balance=bool(d.get("auto_balance", False)),
            monthly_budget=float(d["monthly_budget"]) if d.get("monthly_budget") is not None else None,
            warn_days=float(d.get("warn_days", 7)), alert_before_hours=d.get("alert_before_hours"),
        )

    def matches(self, model_id: str) -> bool:
        return not self.models or any(fnmatch.fnmatch(model_id, pat) for pat in self.models)


@dataclass
class SourceStatus:
    source: CreditSource
    available: bool
    remaining: float | None
    used: float
    period_start: datetime | None = None
    period_end: datetime | None = None
    urgency: float = 0.0
    cost_factor: float = 1.0
    note: str = ""

    def to_dict(self) -> dict:
        s = self.source
        return {
            "id": s.id, "provider": s.provider, "type": s.type, "label": s.label, "unit": s.unit,
            "amount": s.amount, "remaining": None if self.remaining is None else round(self.remaining, 4),
            "used": round(self.used, 4), "available": self.available, "urgency": round(self.urgency, 2),
            "cost_factor": round(self.cost_factor, 2), "note": self.note, "implicit": s.implicit,
            "expires": s.expires.isoformat() if s.expires else None,
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "resets": self.period_end.isoformat() if self.period_end else None,
            "monthly_budget": s.monthly_budget,
        }


@dataclass
class Alert:
    key: str
    severity: str
    title: str
    message: str
    provider: str
    source_id: str
    due: str | None = None

    def to_dict(self) -> dict:
        return self.__dict__.copy()


# --------------------------------------------------------------------------------------------
class Ledger:
    def __init__(self, cfg, store, provider_available: Callable[[str], bool] | None = None):
        self.cfg = cfg
        self.store = store
        self.provider_available = provider_available or (lambda p: True)
        self.reload()

    def reload(self) -> None:
        raw = self.cfg.credits_raw()
        self.low_balance = float(raw.get("low_balance_usd", 2.0))
        self.sources = [CreditSource.parse(s) for s in (raw.get("sources") or [])]

    # ---- usage helpers ------------------------------------------------------------------
    def _used(self, src: CreditSource, since: datetime | None) -> float:
        u = self.store.usage_since(src.id, since.timestamp() if since else 0)
        if src.unit == "usd":
            return u["usd"]
        if src.unit == "tokens":
            return u["tokens"]
        return u["requests"]

    def status(self, src: CreditSource, now: datetime | None = None) -> SourceStatus:
        now = now or datetime.now(timezone.utc)
        if src.type == "prepaid":
            return self._prepaid(src, now)
        if src.type in ("free_tier", "subscription"):
            return self._quota(src, now)
        return self._payg(src, now)

    def _prepaid(self, src: CreditSource, now: datetime) -> SourceStatus:
        remaining: float | None = None
        note = ""
        if src.auto_balance:
            snap = self.store.latest_balance(src.provider)
            if snap:
                ts, data = snap
                if data.get("usd") is not None:
                    remaining = float(data["usd"]) - self.store.provider_spend_since(src.provider, ts)
                    note = f"live balance {datetime.fromtimestamp(ts).strftime('%d %b %H:%M')}"
                else:
                    note = "balance not in USD: " + ", ".join(f"{k} {v}" for k, v in data.items() if k != "raw")
            else:
                note = "balance not fetched yet: run `switchboard credits refresh`"
        if remaining is None and src.amount is not None:
            remaining = src.amount - self._used(src, src.added)
        used = self._used(src, src.added)
        if src.expires and src.expires <= now:
            return SourceStatus(src, False, remaining, used, note="expired")
        urgency = 0.0
        if src.expires and remaining and remaining > 0:
            days = (src.expires - now).total_seconds() / 86400
            urgency = 1.0 if days <= 1 else 0.8 if days <= 3 else 0.5 if days <= 7 else 0.15 if days <= 30 else 0.0
        available = remaining is None or remaining > 0
        cost_factor = 0.9 - 0.85 * urgency  # money already paid; expiring money is nearly free
        return SourceStatus(src, available, remaining, used, urgency=urgency, cost_factor=cost_factor, note=note)

    def _quota(self, src: CreditSource, now: datetime) -> SourceStatus:
        start = end = None
        if src.reset:
            start, end = src.reset.bounds(now)
        used = self._used(src, start or src.added)
        remaining = None if src.amount is None else src.amount - used
        available = remaining is None or remaining > 0
        if src.expires and src.expires <= now:
            available, note = False, "expired"
        else:
            note = "" if src.amount is not None else "quota unknown: tracking usage only"
        urgency = 0.0
        if src.amount and remaining is not None and start and end:
            unused = max(0.0, remaining / src.amount)
            time_left = (end - now) / (end - start)
            if unused > 0.3 and time_left < 0.25:
                urgency = round(0.5 * unused * (1 - time_left / 0.25) + 0.1, 3)
        cost_factor = 0.0 if src.type == "free_tier" else 0.05
        return SourceStatus(src, available, remaining, used, start, end, urgency, cost_factor, note)

    def _payg(self, src: CreditSource, now: datetime) -> SourceStatus:
        local = now.astimezone(local_tz())
        month_start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if src.implicit:
            spend = self.store.provider_spend_since(src.provider, month_start.timestamp())
        else:
            spend = self._used(src, month_start)
        remaining = None if src.monthly_budget is None else src.monthly_budget - spend
        available = remaining is None or remaining > 0
        cost_factor = 1.25 if src.implicit and src.type == "payg_fallback" else 1.0
        note = "over monthly budget" if not available else ""
        return SourceStatus(src, available, remaining, spend, month_start, None, 0.0, cost_factor, note)

    # ---- queries ---------------------------------------------------------------------------
    def statuses(self, now: datetime | None = None) -> list[SourceStatus]:
        return [self.status(s, now) for s in self.sources]

    def pick_source(self, provider: str, model_id: str, now: datetime | None = None) -> SourceStatus | None:
        """Best funding source for a call: lowest effective cost first, then most urgent."""
        configured = [s for s in self.sources if s.provider == provider and s.matches(model_id)]
        statuses = [self.status(s, now) for s in configured]
        usable = [st for st in statuses if st.available]
        if usable:
            usable.sort(key=lambda st: (st.cost_factor, -st.urgency))
            return usable[0]
        if any(s.provider == provider and s.type == "payg" for s in self.sources):
            return None  # payg configured but over budget
        # Nothing tracked for this model: assume normal pay-as-you-go billing on the account.
        typ = "payg" if not configured else "payg_fallback"
        implicit = CreditSource(id=f"{provider}:payg", provider=provider, type=typ, label=f"{provider} (untracked)",
                                implicit=True)
        st = self._payg(implicit, now or datetime.now(timezone.utc))
        if configured:
            st.note = "tracked credit used up: this may bill your card or fail"
        return st

    def record(self, provider: str, model: str, source_id: str | None, input_tokens: int, output_tokens: int,
               cost_usd: float, task_id: str | None = None, requests: int = 1) -> None:
        self.store.record_usage(provider, model, source_id, input_tokens, output_tokens, cost_usd, task_id, requests)

    # ---- alerts ----------------------------------------------------------------------------
    def alerts(self, now: datetime | None = None) -> list[Alert]:
        now = now or datetime.now(timezone.utc)
        out: list[Alert] = []
        for src in self.sources:
            st = self.status(src, now)
            if src.type == "prepaid":
                out += self._prepaid_alerts(src, st, now)
            elif src.type in ("free_tier", "subscription"):
                out += self._quota_alerts(src, st, now)
            elif src.type == "payg" and src.monthly_budget:
                pct = st.used / src.monthly_budget if src.monthly_budget else 0
                month = now.astimezone(local_tz()).strftime("%Y-%m")
                if pct >= 1:
                    out.append(Alert(f"{src.id}:budget:{month}:100", "critical", f"{src.label}: over monthly budget",
                                     f"${st.used:.2f} spent of a ${src.monthly_budget:.2f} budget. Switchboard will "
                                     f"route around {src.provider} until next month.", src.provider, src.id))
                elif pct >= 0.8:
                    out.append(Alert(f"{src.id}:budget:{month}:80", "warning", f"{src.label}: {pct:.0%} of budget",
                                     f"${st.used:.2f} of ${src.monthly_budget:.2f} spent this month.", src.provider, src.id))
        out.sort(key=lambda a: (SEVERITY_ORDER.get(a.severity, 3), a.due or ""))
        return out

    def _prepaid_alerts(self, src: CreditSource, st: SourceStatus, now: datetime) -> list[Alert]:
        out = []
        rem = st.remaining
        if src.expires and rem is not None and rem > 0.01:
            left = src.expires - now
            if left.total_seconds() <= 0:
                out.append(Alert(f"{src.id}:expired", "info", f"{src.label} expired",
                                 f"About ${rem:.2f} was still unused when it expired on {fmt_when(src.expires)}.",
                                 src.provider, src.id, src.expires.isoformat()))
            elif left <= timedelta(days=src.warn_days):
                urgent = left <= timedelta(days=1)
                when = "tomorrow" if timedelta(hours=12) < left <= timedelta(days=1) else f"in {human_delta(left)}"
                out.append(Alert(
                    f"{src.id}:expiring:{src.expires.date()}:{'1d' if urgent else 'week'}",
                    "critical" if urgent else "warning",
                    f"Use ${rem:.2f} of {src.label} - expires {when}",
                    f"${rem:.2f} of {src.provider} credit expires {fmt_when(src.expires)}. Switchboard now routes "
                    f"suitable tasks to {src.provider} first; queue big jobs (refactors, batch docs, images) to use it up.",
                    src.provider, src.id, src.expires.isoformat()))
        if rem is not None and rem < self.low_balance and not (src.expires and src.expires <= now):
            day = now.astimezone(local_tz()).strftime("%Y-%m-%d")
            out.append(Alert(f"{src.id}:low:{day}", "warning", f"Low balance on {src.label}: ${max(rem, 0):.2f}",
                             f"Top up {src.provider} or Switchboard will route around it.", src.provider, src.id))
        return out

    def _quota_alerts(self, src: CreditSource, st: SourceStatus, now: datetime) -> list[Alert]:
        out = []
        if src.amount and st.remaining is not None and st.period_start and st.period_end:
            left = st.period_end - now
            period = st.period_end - st.period_start
            if src.alert_before_hours is not None:
                window = timedelta(hours=float(src.alert_before_hours))
            elif period <= timedelta(days=1):
                window = timedelta(hours=3)
            elif period <= timedelta(days=7):
                window = timedelta(hours=24)
            else:
                window = timedelta(hours=72)
            unused = max(0.0, st.remaining) / src.amount
            if left <= window and unused >= 0.5:
                kind = "free tier" if src.type == "free_tier" else "plan quota"
                out.append(Alert(
                    f"{src.id}:reset:{st.period_end.isoformat()}", "warning" if unused >= 0.8 else "info",
                    f"{src.label}: {unused:.0%} of {kind} unused - resets in {human_delta(left)}",
                    f"{max(st.remaining, 0):g} of {src.amount:g} {src.unit} left, resetting {fmt_when(st.period_end)}. "
                    f"Use it before then: Switchboard prefers it for matching tasks until the reset.",
                    src.provider, src.id, st.period_end.isoformat()))
        if src.renews:
            left = src.renews - now
            if timedelta(0) < left <= timedelta(days=3):
                out.append(Alert(f"{src.id}:renews:{src.renews.date()}", "info", f"{src.label} renews in {human_delta(left)}",
                                 f"Your {src.label} plan renews {fmt_when(src.renews)}.", src.provider, src.id,
                                 src.renews.isoformat()))
        return out
