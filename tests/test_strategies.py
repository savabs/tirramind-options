import json

import pytest

from tmo import portfolio as pf
from tmo import specs, strategies
from tests.fixtures import chain

VRP = json.load(open("ledger/specs/btc_weekly_vrp_v1.json"))
HOLD = json.load(open("ledger/specs/btc_hold_context_v1.json"))


def _book(trades):
    return pf.replay([{**t, "as_of": "2026-09-10", "seq": i} for i, t in enumerate(trades)])


def test_vrp_opens_the_expiry_nearest_seven_days_at_the_money():
    marks = chain(dtes=(3.0, 7.0, 30.0), forward=60000.0)
    trades, note = strategies.btc_weekly_vrp(VRP, pf.Book(), marks)
    opts = [t for t in trades if t["kind"] == "option"]
    assert len(opts) == 2
    assert {t["is_call"] for t in opts} == {True, False}
    assert all(t["strike"] == 60000.0 for t in opts)
    assert all(t["qty"] == -1 for t in opts)          # short straddle
    assert "20260917" in note                          # 7 days from 2026-09-10


def test_vrp_sells_at_the_bid_never_the_mid():
    marks = chain()
    trades, _ = strategies.btc_weekly_vrp(VRP, pf.Book(), marks)
    by_inst = marks.set_index("instrument")
    for t in [t for t in trades if t["kind"] == "option"]:
        assert t["price_usd"] == pytest.approx(float(by_inst.loc[t["instrument"], "bid_usd"]))


def test_vrp_hedges_the_straddle_delta_on_entry():
    marks = chain()
    trades, _ = strategies.btc_weekly_vrp(VRP, pf.Book(), marks)
    hedge = [t for t in trades if t["kind"] == "future"]
    # short a 0.5-delta call and a -0.5-delta put: net zero, inside the band
    assert hedge == []


def test_vrp_hedges_when_delta_leaves_the_band():
    marks = chain()
    marks.loc[marks["is_call"], "delta"] = 0.9        # spot ran away overnight
    trades, _ = strategies.btc_weekly_vrp(VRP, pf.Book(), marks)
    hedge = [t for t in trades if t["kind"] == "future"]
    assert len(hedge) == 1
    # short both legs: option delta is -(0.9) - (-0.5) = -0.4, so buy 0.4 forward
    assert hedge[0]["qty"] == pytest.approx(0.4)


def test_vrp_holds_and_does_not_reopen_while_a_straddle_is_on():
    marks = chain()
    book = _book(strategies.btc_weekly_vrp(VRP, pf.Book(), marks)[0])
    trades, note = strategies.btc_weekly_vrp(VRP, book, marks)
    assert trades == []
    assert "held" in note


def test_vrp_closes_inside_the_exit_threshold():
    marks = chain(dtes=(7.0,))
    book = _book(strategies.btc_weekly_vrp(VRP, pf.Book(), marks)[0])
    later = chain(dtes=(7.0,))
    later["dte"] = 1.5                                 # the next run, six days on
    trades, note = strategies.btc_weekly_vrp(VRP, book, later)
    assert len(trades) == 2 and all(t["qty"] == 1 for t in trades)
    assert "closed" in note


def test_vrp_settles_a_leg_that_vanished_and_flags_it():
    marks = chain(dtes=(7.0,))
    book = _book(strategies.btc_weekly_vrp(VRP, pf.Book(), marks)[0])
    gone = chain(dtes=(30.0,), forward=70000.0)        # the week is no longer listed
    trades, _ = strategies.btc_weekly_vrp(VRP, book, gone)
    assert all(t["imputed"] for t in trades)
    call = [t for t in trades if t["is_call"]][0]
    assert call["price_usd"] == pytest.approx(10000.0)


def test_vrp_declines_to_trade_when_no_expiry_is_in_the_window():
    marks = chain(dtes=(2.0, 45.0))
    trades, note = strategies.btc_weekly_vrp(VRP, pf.Book(), marks)
    assert trades == [] and "no expiry" in note


def test_the_context_line_buys_once_and_never_again():
    marks = chain()
    trades, _ = strategies.btc_hold_context(HOLD, pf.Book(), marks)
    assert len(trades) == 1 and trades[0]["qty"] == 1
    assert strategies.btc_hold_context(HOLD, _book(trades), marks)[0] == []


def test_every_active_spec_has_an_implementation():
    for spec in specs.active(specs.load_all()):
        assert spec["strategy_id"] in strategies.REGISTRY


def test_the_same_inputs_always_produce_the_same_fills():
    marks = chain()
    a, _ = strategies.btc_weekly_vrp(VRP, pf.Book(), marks)
    b, _ = strategies.btc_weekly_vrp(VRP, pf.Book(), marks)
    assert a == b
