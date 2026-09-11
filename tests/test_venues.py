"""The two price conventions, and the registry of markets we fit.

Getting inverse and linear the wrong way round does not raise. It produces a
surface that looks plausible and is wrong by the price of the underlying, which
is why the convention is checked on every fetch rather than trusted once.
"""

import numpy as np
import pandas as pd
import pytest

from tmo import venues


def chain(*, linear: bool, forward=100.0, mark_iv=0.5):
    """A tiny two-sided chain whose true prices we know."""
    rows = []
    for strike in (90.0, 100.0, 110.0):
        # A crude but positive price; the convention is what is under test, not
        # the pricing.
        usd = 8.0 if strike == 100.0 else 5.0
        quote = usd if linear else usd / forward
        rows.append({"instrument": f"X-{strike:.0f}-C", "expiry": pd.Timestamp("2026-10-10", tz="UTC"),
                     "T": 0.1, "strike": strike, "is_call": True, "forward": forward,
                     "mark_iv": mark_iv, "bid": quote * 0.98, "ask": quote * 1.02,
                     "open_interest": 1.0, "volume": 1.0})
    df = pd.DataFrame(rows)
    df["bid_usd"] = df["bid"] if linear else df["bid"] * df["forward"]
    df["ask_usd"] = df["ask"] if linear else df["ask"] * df["forward"]
    df["two_sided"] = True
    df["bid_iv"], df["ask_iv"] = mark_iv - 0.01, mark_iv + 0.01
    return df


def test_every_market_has_a_key_a_venue_and_a_convention():
    assert len(venues.MARKETS) >= 7
    for m in venues.MARKETS:
        assert m.key and m.venue and m.base
        assert m.convention in ("inverse", "linear")


def test_the_keys_are_unique_so_one_market_cannot_shadow_another():
    keys = [m.key for m in venues.MARKETS]
    assert len(keys) == len(set(keys))
    assert set(venues.BY_KEY) == set(keys)


def test_btc_and_eth_are_the_coin_settled_book():
    assert venues.BY_KEY["BTC"].convention == "inverse"
    assert venues.BY_KEY["ETH"].convention == "inverse"


def test_the_usdc_markets_are_linear_and_settle_in_usdc():
    for key in ("SOL", "XRP", "HYPE", "TRX", "AVAX"):
        m = venues.BY_KEY[key]
        assert m.convention == "linear"
        assert m.settled_in == "USDC"


def test_a_convention_that_agrees_with_the_venue_reads_near_zero():
    for linear in (True, False):
        out = venues.verify_convention(chain(linear=linear))
        assert out["n"] == 3
        assert out["median_vol_pts"] == pytest.approx(0.0, abs=1e-9)


def test_a_confused_convention_is_caught_rather_than_published():
    # The failure this exists for: a linear book read as if it were inverse.
    # Every quote is then overstated by the price of the underlying, which drags
    # the implied volatility far from the venue's own mark.
    df = chain(linear=True)
    df["bid_iv"] = df["bid_iv"] + 3.0        # what a hundredfold price error does
    df["ask_iv"] = df["ask_iv"] + 3.0
    out = venues.verify_convention(df)
    assert out["median_vol_pts"] > 100


def test_it_says_nothing_rather_than_guessing_on_an_empty_book():
    out = venues.verify_convention(pd.DataFrame(
        columns=["two_sided", "mark_iv", "bid_iv", "ask_iv", "strike", "forward"]))
    assert out["n"] == 0
    assert np.isnan(out["median_vol_pts"])


def test_it_samples_near_the_money_not_the_deepest_strike():
    # The first version took the most negative log-moneyness, which sampled the
    # deepest puts, where the spread is widest and the mid says least.
    df = chain(linear=True)
    far = df.iloc[[0]].copy()
    far["strike"] = 5.0                       # a long way from the forward
    far["bid_iv"], far["ask_iv"] = 9.0, 9.0   # and nonsense
    out = venues.verify_convention(pd.concat([df, far], ignore_index=True), sample=3)
    assert out["median_vol_pts"] == pytest.approx(0.0, abs=1e-9)
