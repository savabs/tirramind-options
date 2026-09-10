import pandas as pd
import pytest

from tmo import portfolio as pf
from tests.fixtures import chain


def test_fees_follow_deribits_published_taker_schedule():
    # 0.03% of a 60k forward is $18; the premium cap bites only on cheap options
    assert pf.option_fee(60000.0, 1000.0, 1) == pytest.approx(18.0)
    assert pf.option_fee(60000.0, 40.0, 1) == pytest.approx(5.0)   # 12.5% of premium
    assert pf.future_fee(60000.0, 1) == pytest.approx(30.0)


def test_selling_credits_cash_and_still_pays_the_fee():
    t = pf.make_trade(instrument="X", kind="option", qty=-1, price_usd=1000.0,
                      forward=60000.0, action="open", reason="")
    assert t["cash_delta_usd"] == pytest.approx(1000.0 - 18.0)


def test_replay_nets_positions_and_forgets_flat_ones():
    trades = [
        {"as_of": "2026-09-10", "seq": 0, "instrument": "X", "kind": "option",
         "qty": -1, "cash_delta_usd": 982.0, "strike": 60000.0, "is_call": True},
        {"as_of": "2026-09-11", "seq": 0, "instrument": "X", "kind": "option",
         "qty": 1, "cash_delta_usd": -1018.0, "strike": 60000.0, "is_call": True},
    ]
    book = pf.replay(trades)
    assert book.positions == {}
    assert book.cash_usd == pytest.approx(-36.0)


def test_equity_starts_at_minus_the_cost_of_getting_in():
    marks = chain()
    row = marks.iloc[0]
    t = pf.make_trade(instrument=row["instrument"], kind="option", qty=-1,
                      price_usd=float(row["bid_usd"]), forward=float(row["forward"]),
                      action="open", reason="", strike=float(row["strike"]),
                      expiry=str(row["expiry"]), is_call=bool(row["is_call"]))
    book = pf.replay([{**t, "as_of": "2026-09-10", "seq": 0}])
    v = pf.value(book, marks, price_col="mid_usd")
    # sold at the bid, marked at the mid, and paid a fee: strictly negative
    assert v["equity_usd"] < 0
    assert v["equity_usd"] == pytest.approx(
        float(row["bid_usd"]) - float(row["mid_usd"]) - t["fee_usd"])


def test_an_instrument_that_vanished_is_settled_at_intrinsic():
    marks = chain(forward=70000.0)
    book = pf.replay([{"as_of": "2026-09-10", "seq": 0, "instrument": "GONE",
                       "kind": "option", "qty": 1.0, "cash_delta_usd": -100.0,
                       "strike": 60000.0, "is_call": True}])
    v = pf.value(book, marks, price_col="mid_usd")
    assert v["missing_instruments"] == ["GONE"]
    assert v["position_value_usd"] == pytest.approx(10000.0)


def test_trade_ids_are_content_derived_so_an_identical_replay_collides():
    t = {"instrument": "X", "qty": -1, "price_usd": 1000.0, "action": "open"}
    assert pf.trade_id("s", "2026-09-10", 0, t) == pf.trade_id("s", "2026-09-10", 0, t)
    assert pf.trade_id("s", "2026-09-10", 0, t) != pf.trade_id("s", "2026-09-10", 0,
                                                              {**t, "qty": -2})
