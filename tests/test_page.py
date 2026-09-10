"""The Deribit tab: self-contained, and honest on its face."""

import re

import pytest

from tmo import page


def payload(currency="BTC", *, status="ok"):
    return {
        "venue": "deribit", "currency": currency,
        "as_of": "2026-09-10T09:00:00+00:00", "forward_front": 78000.0,
        "quality": {"rmse_vol_pts": 0.68, "inside_bid_ask": 0.93, "quotes_fitted": 412,
                    "expiries": 2, "our_butterfly_violations": 0,
                    "our_calendar_violations": 0, "executable_venue_arbs": 3,
                    "guarantee": "butterfly and calendar checked on a dense grid"},
        "expiries": [
            {"expiry": "2026-09-18", "dte": 8.0, "forward": 78000.0, "quotes": 21,
             "rmse_vol_pts": 0.41, "inside_bid_ask": 1.0, "status": "ok",
             "strike": [70000, 78000], "log_moneyness": [-0.108, 0.0],
             "bid_iv": [0.55, 0.50], "ask_iv": [0.57, 0.52],
             "venue_mark_iv": [0.56, 0.51], "our_iv": [0.561, 0.509]},
            {"expiry": "2026-10-02", "dte": 22.0, "forward": 78100.0, "quotes": 12,
             "rmse_vol_pts": 1.36, "inside_bid_ask": 0.92, "status": status,
             "strike": [70000, 78000], "log_moneyness": [-0.109, 0.0],
             "bid_iv": [0.54, 0.49], "ask_iv": [0.56, 0.51],
             "venue_mark_iv": [0.55, 0.50], "our_iv": [0.552, 0.499]},
        ],
    }


@pytest.fixture
def html():
    return page.render({"BTC": payload("BTC"), "ETH": payload("ETH", status="calendar_fail")})


def test_the_error_is_on_the_page_not_in_a_footnote(html):
    head = html[:html.index("The smile")]
    assert "0.68" in head and "93%" in head
    assert "OUR ERROR" in head.upper()


def test_every_expiry_reports_its_own_error(html):
    assert "0.41" in html and "1.36" in html


def test_a_slice_that_fell_back_says_so(html):
    assert "calendar fail" in html


def test_both_currencies_are_present(html):
    assert 'data-cur="BTC"' in html and 'data-cur="ETH"' in html


def test_the_page_fetches_nothing_when_opened(html):
    assert not re.search(r"<script[^>]+\bsrc=", html)
    assert not re.search(r"<link[^>]+stylesheet", html)
    assert not re.search(r"<img[^>]+src=", html)
    assert "fetch(" not in html and "XMLHttpRequest" not in html


def test_the_data_travels_with_the_page(html):
    assert "window.__SURFACES__=" in html
    assert '"our_iv"' in html and '"venue_mark_iv"' in html


def test_it_says_when_it_was_made(html):
    assert "Generated 20" in html and "refit every 30 minutes" in html


def test_it_carries_the_legal_line(html):
    assert "not a recommendation" in html or "Nothing here is a recommendation" in html


def test_dark_mode_is_defined_for_both_the_os_setting_and_a_toggle(html):
    assert "prefers-color-scheme: dark" in html
    assert ':root[data-theme="dark"]' in html


def test_it_refuses_to_render_nothing():
    with pytest.raises(ValueError):
        page.render({})


def test_a_clean_surface_and_a_dirty_one_read_differently():
    dirty = payload()
    dirty["quality"]["our_butterfly_violations"] = 2
    html = page.render({"BTC": dirty})
    assert "violations" in html and "arbitrage-free</div>" not in html
