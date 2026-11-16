from datetime import datetime, timedelta, timezone

from switchboard.config import Config
from switchboard.credits import Ledger, Schedule
from switchboard.store import Store

from .conftest import make_home

NOW = datetime(2026, 11, 16, 21, 0, tzinfo=timezone.utc)  # 13:00 in Los Angeles


def ledger(tmp_path, sources):
    cfg = Config(make_home(tmp_path / "h", credits=sources))
    return Ledger(cfg, Store(cfg.db_path))


def test_daily_reset_in_timezone():
    start, end = Schedule.parse({"every": "day", "at": "00:00", "tz": "America/Los_Angeles"}).bounds(NOW)
    assert start.isoformat().startswith("2026-11-16T00:00") and end.isoformat().startswith("2026-11-17T00:00")


def test_weekly_monthly_and_rolling_windows():
    s, e = Schedule.parse({"every": "week", "weekday": "mon", "tz": "UTC"}).bounds(NOW)
    assert s.weekday() == 0 and (e - s) == timedelta(days=7) and s <= NOW < e
    s, e = Schedule.parse({"every": "month", "day": 31, "tz": "UTC"}).bounds(datetime(2026, 2, 10, tzinfo=timezone.utc))
    assert s.day == 31 and s.month == 1 and e.month == 2 and e.day == 28  # clamped to month length
    s, e = Schedule.parse({"every": "5h", "tz": "UTC"}).bounds(NOW)
    assert e - s == timedelta(hours=5) and s <= NOW < e


def test_expiring_credit_is_critical_and_preferred(tmp_path):
    led = ledger(tmp_path, [
        {"id": "promo", "provider": "openai", "type": "prepaid", "amount": 12.4, "expires": "2026-11-17T20:00:00+00:00"},
        {"id": "budget", "provider": "openai", "type": "payg", "monthly_budget": 50},
    ])
    alerts = led.alerts(NOW)
    assert alerts[0].severity == "critical" and "12.40" in alerts[0].title and "tomorrow" in alerts[0].title
    st = led.pick_source("openai", "gpt-x", NOW)
    assert st.source.id == "promo" and st.urgency == 1.0 and st.cost_factor < 0.1


def test_spend_reduces_prepaid_and_low_balance_alert(tmp_path):
    led = ledger(tmp_path, [{"id": "p", "provider": "x", "type": "prepaid", "amount": 3.0}])
    led.record("x", "m", "p", 1000, 1000, 1.5)
    st = led.status(led.sources[0], NOW)
    assert abs(st.remaining - 1.5) < 1e-9
    assert any("Low balance" in a.title for a in led.alerts(NOW))
    led.record("x", "m", "p", 1000, 1000, 2.0)
    assert not led.status(led.sources[0], NOW).available


def test_free_tier_reset_warning_only_near_reset(tmp_path):
    src = {"id": "gem", "provider": "gemini", "type": "free_tier", "amount": 100, "unit": "requests",
           "reset": {"every": "day", "at": "00:00", "tz": "America/Los_Angeles"}}
    led = ledger(tmp_path, [src])
    assert led.alerts(NOW) == []  # 11h before reset: quiet
    late = datetime(2026, 11, 17, 6, 30, tzinfo=timezone.utc)  # 22:30 LA, 1.5h before reset
    alerts = led.alerts(late)
    assert alerts and "100% of free tier unused" in alerts[0].title
    st = led.status(led.sources[0], late)
    assert st.cost_factor == 0.0 and st.urgency > 0


def test_free_tier_model_patterns(tmp_path):
    led = ledger(tmp_path, [{"id": "flash", "provider": "gemini", "type": "free_tier", "amount": 10,
                             "models": ["gemini-*flash*"], "reset": {"every": "day"}}])
    assert led.pick_source("gemini", "gemini-2.5-flash").source.id == "flash"
    assert led.pick_source("gemini", "gemini-3-pro").source.implicit  # not covered: plain pay-as-you-go


def test_payg_budget_blocks_when_exceeded(tmp_path):
    led = ledger(tmp_path, [{"id": "b", "provider": "anthropic", "type": "payg", "monthly_budget": 1}])
    led.record("anthropic", "m", "b", 0, 0, 1.2)
    assert led.pick_source("anthropic", "m") is None
    assert led.alerts()[0].severity == "critical"


def test_live_balance_snapshot(tmp_path):
    led = ledger(tmp_path, [{"id": "ds", "provider": "deepseek", "type": "prepaid", "auto_balance": True}])
    led.store.save_balance("deepseek", {"usd": 7.5})
    led.record("deepseek", "deepseek-chat", "ds", 10, 10, 0.5)
    assert abs(led.status(led.sources[0]).remaining - 7.0) < 1e-6
