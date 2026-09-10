"""The shared rule table, from the Python side.

Every case in `web/lib/rules.json` must hold here and in the TypeScript
implementation at `web/lib/rules.ts`. The rules decide who may use a paid
product, so a disagreement between the two is a disagreement about money. This
file and its TypeScript twin are what stop one drifting from the other: neither
can change a rule without the other's tests failing.
"""

import json
import pathlib

import pytest

from tmo.payments.handler import _REFUND_SETTLED_STATUSES, _REVOKING_ACTIONS
from tmo.payments.store import effective_active
from tmo.payments.tiers import UNKNOWN_PRICE, parse_map, resolve_tier

VECTORS = json.loads((pathlib.Path(__file__).parent.parent /
                      "web" / "lib" / "rules.json").read_text())


def ids(cases):
    return [c["case"] for c in cases]


@pytest.mark.parametrize("v", VECTORS["effective_active"], ids=ids(VECTORS["effective_active"]))
def test_effective_access(v):
    assert effective_active(v["entry"], now=v["now"]) is v["expect"]


@pytest.mark.parametrize("v", VECTORS["tier"], ids=ids(VECTORS["tier"]))
def test_price_to_tier(v):
    got = resolve_tier(v["price_id"], mapping_raw=v["map"])
    expected = v["expect"]
    assert (None if got == UNKNOWN_PRICE else got) == expected


def test_a_malformed_map_entry_is_skipped():
    assert parse_map("pri_a:terminal,garbage,:,pri_b:") == {"pri_a": "terminal"}


def _decide(action: str, status: str, kind: str) -> tuple[bool, bool]:
    """The same decision the handler makes, isolated from the store writes."""
    a, s, k = action.lower(), status.lower(), kind.lower()
    if a not in _REVOKING_ACTIONS:
        return False, False
    if a == "refund" and s not in _REFUND_SETTLED_STATUSES:
        return False, False
    return True, (a == "chargeback" or k == "full")


@pytest.mark.parametrize("v", VECTORS["adjustment"], ids=ids(VECTORS["adjustment"]))
def test_refunds_and_chargebacks(v):
    applied, revoke = _decide(v["action"], v["status"], v["kind"])
    assert applied is v["expect_applied"]
    assert revoke is v["expect_revoke"]


def test_the_table_is_not_empty_which_would_pass_vacuously():
    assert len(VECTORS["effective_active"]) >= 8
    assert len(VECTORS["tier"]) >= 6
    assert len(VECTORS["adjustment"]) >= 6
