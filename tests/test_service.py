"""The fit service: cached, honest about staleness, never fits twice at once."""

import threading
import time

import pytest
from fastapi.testclient import TestClient

from tmo.service.app import create_app
from tmo.service.cache import SurfaceCache


def fake_surface(currency: str) -> dict:
    return {
        "venue": "deribit", "currency": currency, "as_of": "2026-09-10T08:00:00+00:00",
        "forward_front": 78000.0,
        "quality": {"rmse_vol_pts": 0.68, "inside_bid_ask": 0.93,
                    "our_butterfly_violations": 0, "our_calendar_violations": 0},
        "expiries": [{
            "expiry": "2026-09-18", "dte": 8.0, "forward": 78000.0, "quotes": 40,
            "rmse_vol_pts": 0.5, "inside_bid_ask": 0.95, "status": "ok",
            "strike": [70000, 78000], "log_moneyness": [-0.108, 0.0],
            "bid_iv": [0.55, 0.50], "ask_iv": [0.57, 0.52],
            "venue_mark_iv": [0.56, 0.51], "our_iv": [0.561, 0.509],
        }],
    }


@pytest.fixture
def client():
    app = create_app(builder=fake_surface, ttl_s=30.0, warm=False)
    with TestClient(app) as c:
        yield c


def test_health_reports_a_cold_service_without_fitting(client):
    body = client.get("/health").json()
    assert body["ok"]
    assert body["surfaces"]["deribit/BTC"]["warm"] is False


def test_a_surface_comes_back_with_its_error_attached(client):
    body = client.get("/surface/deribit/BTC").json()
    assert body["currency"] == "BTC"
    assert body["quality"]["rmse_vol_pts"] == 0.68
    assert body["quality"]["our_butterfly_violations"] == 0
    assert body["stale"] is False


def test_the_index_view_leaves_out_the_per_strike_arrays(client):
    body = client.get("/surface/deribit/BTC").json()
    e = body["expiries"][0]
    assert "rmse_vol_pts" in e and "strike" not in e


def test_one_expiry_carries_our_ivs_against_the_venue_marks(client):
    body = client.get("/surface/deribit/BTC/2026-09-18").json()
    s = body["slice"]
    assert s["our_iv"] == [0.561, 0.509]
    assert s["venue_mark_iv"] == [0.56, 0.51]
    assert len(s["strike"]) == len(s["our_iv"])


def test_an_unknown_expiry_says_which_ones_exist(client):
    r = client.get("/surface/deribit/BTC/2099-01-01")
    assert r.status_code == 404
    assert "2026-09-18" in r.json()["detail"]


def test_an_unsupported_venue_is_a_404(client):
    assert client.get("/surface/nse/NIFTY").status_code == 404


def test_the_second_request_is_served_from_cache(client):
    calls = []
    app = create_app(builder=lambda c: (calls.append(c), fake_surface(c))[1],
                     ttl_s=30.0, warm=False)
    with TestClient(app) as c:
        c.get("/surface/deribit/BTC")
        c.get("/surface/deribit/BTC")
        c.get("/surface/deribit/BTC/2026-09-18")
    assert calls == ["BTC"], "one fit, three requests"


def test_a_response_says_how_old_its_surface_is(client):
    cache = client.app.state.cache
    client.get("/surface/deribit/BTC")
    entry = cache.peek(("deribit", "BTC"))
    entry.computed_at -= 45
    body = client.get("/surface/deribit/BTC").json()
    assert body["age_s"] >= 45 and body["stale"] is True


# ── the cache itself ───────────────────────────────────────────────────────
def test_a_burst_of_callers_computes_once():
    started, done = threading.Barrier(8), []
    cache = SurfaceCache(ttl_s=30.0)
    calls = []

    def slow():
        calls.append(1)
        time.sleep(0.2)
        return "surface"

    def worker():
        started.wait()
        done.append(cache.get("k", slow)[0])

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert done == ["surface"] * 8
    assert len(calls) == 1, "eight callers, one fit"


def test_a_failed_refresh_keeps_the_last_good_surface():
    cache = SurfaceCache(ttl_s=30.0)
    cache.get("k", lambda: "good")
    ok = cache.refresh("k", lambda: (_ for _ in ()).throw(RuntimeError("deribit down")))
    assert ok is False
    assert cache.peek("k").value == "good"
    assert "deribit down" in cache.peek("k").error


def test_staleness_is_measured_not_guessed():
    cache = SurfaceCache(ttl_s=30.0)
    cache.get("k", lambda: "v")
    assert not cache.is_stale("k")
    cache.peek("k").computed_at -= 31
    assert cache.is_stale("k")
    assert cache.is_stale("never-computed")


def test_the_background_refresher_warms_a_cold_cache():
    cache = SurfaceCache(ttl_s=0.05)
    cache.start_refreshing({"k": lambda: "warm"}, interval_s=0.01)
    try:
        deadline = time.time() + 3
        while time.time() < deadline and cache.peek("k") is None:
            time.sleep(0.01)
        assert cache.peek("k") is not None and cache.peek("k").value == "warm"
    finally:
        cache.stop()
