from switchboard.catalog import Catalog
from switchboard.config import Config
from switchboard.credits import Ledger
from switchboard.router import Router, classify
from switchboard.store import Store

from .conftest import MOCK_MODELS, make_home

MODELS = [
    {"provider": "big", "id": "frontier", "q": 9.5, "price": [5, 25], "efforts": ["low", "medium", "high", "xhigh", "max"],
     "features": ["tools", "vision"]},
    {"provider": "cheap", "id": "fast", "q": 7, "price": [0.1, 0.4], "efforts": ["low", "medium", "high"], "features": ["tools"]},
    {"provider": "promo", "id": "strong", "q": 8.5, "price": [3, 12], "efforts": ["low", "medium", "high"], "features": ["tools"]},
    {"provider": "img", "id": "painter", "q": 9, "price": [0, 0], "per_call": 0.05, "features": ["image_gen"]},
]


def router(tmp_path, credits=None, models=MODELS, cfg_router=None):
    cfg = Config(make_home(tmp_path / "h", models=models, credits=credits))
    led = Ledger(cfg, Store(cfg.db_path))
    return Router(Catalog(models), led, cfg_router or {})


def test_classify_word_boundaries_and_categories():
    assert classify("what's the capital of France?").category == "chat"  # 'api' inside 'capital' is not code
    assert classify("write a python function that parses dates").category == "code"
    assert classify("make a unity prefab with a shader").category == "game_dev"
    assert classify("deploy my site to hostinger over ssh").category == "devops"
    assert classify("generate an image of a fox logo").category == "image_gen"
    hard = classify("Design the complete architecture for a production multiplayer backend from scratch, detailed")
    assert hard.complexity >= 4


def test_simple_task_gets_cheap_model_and_low_effort(tmp_path):
    r = router(tmp_path)
    p = r.plan("hi!", {"big", "cheap", "promo"}, classify("hi!"))
    assert p.model == "fast" and p.effort == "low"


def test_hard_task_gets_frontier_model(tmp_path):
    prompt = "Refactor the entire codebase architecture for production scale, detailed and thorough, from scratch"
    r = router(tmp_path)
    p = r.plan(prompt, {"big", "cheap", "promo"}, classify(prompt))
    assert p.model == "frontier" and p.effort in ("high", "xhigh")


def test_expiring_credit_wins_when_good_enough(tmp_path):
    prompt = "write a python script that renames photos by date taken"
    credits = [{"id": "promo-credit", "provider": "promo", "type": "prepaid", "amount": 20, "expires": "2000-01-01"}]
    # expired credit: no effect
    r = router(tmp_path / "a", credits=credits)
    assert r.plan(prompt, {"big", "cheap", "promo"}, classify(prompt)).provider != "promo"
    from datetime import datetime, timedelta
    soon = (datetime.now().astimezone() + timedelta(hours=10)).isoformat()
    r = router(tmp_path / "b", credits=[dict(credits[0], expires=soon)])
    p = r.plan(prompt, {"big", "cheap", "promo"}, classify(prompt))
    assert p.provider == "promo" and p.source_id == "promo-credit"
    assert any("before it is lost" in x for x in p.reasons)


def test_manual_max_effort_on_trivial_task_warns_or_downgrades(tmp_path):
    r = router(tmp_path / "a")
    p = r.plan("hello", {"big", "cheap"}, classify("hello"), model="frontier", effort="max")
    assert p.effort == "max" and any("overkill" in w for w in p.warnings)
    r = router(tmp_path / "b", cfg_router={"auto_downgrade_effort": True})
    p = r.plan("hello", {"big", "cheap"}, classify("hello"), model="frontier", effort="max")
    assert p.effort == "low" and any("Lowered automatically" in w for w in p.warnings)


def test_image_tasks_route_to_image_models_or_fall_back(tmp_path):
    r = router(tmp_path)
    p = r.plan("generate an image of a castle", {"big", "img"}, classify("generate an image of a castle"))
    assert p.model == "painter"
    p = r.plan("generate an image of a castle", {"big"}, classify("generate an image of a castle"))
    assert p.model == "frontier" and any("image" in w for w in p.warnings)


def test_unknown_or_unavailable_model_errors(tmp_path):
    import pytest
    r = router(tmp_path)
    with pytest.raises(ValueError):
        r.plan("x", {"big"}, classify("x"), model="nope")
    with pytest.raises(ValueError):
        r.plan("x", {"big"}, classify("x"), model="fast")


def test_native_effort_mapping():
    cat = Catalog([{"provider": "o", "id": "g", "efforts": ["minimal", "low", "medium", "high"]},
                   {"provider": "x", "id": "n", "efforts": []}] + MOCK_MODELS)
    g = cat.find("o:g")
    assert g.native_effort("max") == "high" and g.native_effort("low") == "low" and g.native_effort("xhigh") == "high"
    assert cat.find("n").native_effort("high") is None
