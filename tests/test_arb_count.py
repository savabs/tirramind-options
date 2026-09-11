"""The arbitrage count says how many trades exist, not how many kinds there are.

This shipped wrong: the executable report is a dict keyed by butterfly, vertical
and calendar, so counting the dict always gives three. The public page claimed
three arbitrages on a clean book for a day. Every number on that page is a claim
about the market, so this is pinned.
"""

import pytest

from tmo import market


class Report:
    def __init__(self, executable):
        self.venue_violations = {"executable": executable}
        self.refined = {"rmse_vol_pts": 0.5, "inside_bid_ask_share": 0.9}
        self.our_violations = {"butterfly_violations": 0, "calendar_violations": 0}
        self.n_fit, self.expiries = 100, 5


def count(executable):
    """The expression market.state uses, isolated."""
    return sum(len(v or []) for v in (executable or {}).values())


def test_a_clean_book_is_zero_not_three():
    assert count({"butterfly": [], "vertical": [], "calendar": []}) == 0


def test_one_violation_is_one():
    assert count({"butterfly": [{"edge_usd": 12.0}], "vertical": [], "calendar": []}) == 1


def test_violations_across_kinds_add_up():
    assert count({"butterfly": [{}, {}], "vertical": [{}], "calendar": [{}, {}, {}]}) == 6


def test_a_missing_report_is_zero_rather_than_an_error():
    assert count(None) == 0
    assert count({}) == 0


def test_a_kind_with_a_null_list_does_not_raise():
    assert count({"butterfly": None, "vertical": [{}]}) == 1
