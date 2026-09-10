"""Price to tier. An unrecognised price buys nothing.

BUG 2, as shipped in tirramind: ``_resolve_tier`` returned the base tier for
any price it could not map, and for every price when no map was configured.
The effect was that a webhook naming a price we do not sell -- a price from
another product in the same Paddle account, a sandbox price replayed at a
live endpoint, a typo in the dashboard -- provisioned a real, working API key.
A billing system that grants access on an unrecognised SKU is not a billing
system.

The rule now: a price grants a tier only if the operator has explicitly said
so. Anything else resolves to ``UNKNOWN_PRICE`` and the handler refuses to
activate. That is deliberately fail-closed, and it has a cost: adding a price
in the Paddle dashboard without adding it to ``TMO_TIER_PRICE_MAP`` means new
customers on that price are not provisioned. Refusing a customer is visible
and fixable in a minute. Granting access to whoever names any price is
neither, so the trade goes this way round.

Revocation never consults this map. A refund or a cancellation is honoured
whatever price it names.
"""

from __future__ import annotations

import os

# Sentinel, not None: a caller that forgets to check gets a tier string that
# is obviously not a product rather than a None that might read as "default".
UNKNOWN_PRICE = "__unknown_price__"

_ENV = "TMO_TIER_PRICE_MAP"


def parse_map(raw: str | None) -> dict[str, str]:
    """``"pri_a:terminal,pri_b:terminal_annual"`` to a dict. Junk is skipped."""
    out: dict[str, str] = {}
    for pair in (raw or "").split(","):
        pid, sep, tier = pair.strip().partition(":")
        pid, tier = pid.strip(), tier.strip()
        if sep and pid and tier:
            out[pid] = tier
    return out


def resolve_tier(price_id: str | None, *, mapping_raw: str | None = None) -> str:
    """The tier this price sells, or ``UNKNOWN_PRICE``.

    ``mapping_raw`` defaults to the environment so callers stay simple; tests
    and the handler pass it explicitly.
    """
    mapping = parse_map(os.getenv(_ENV, "") if mapping_raw is None else mapping_raw)
    if not price_id or not mapping:
        return UNKNOWN_PRICE
    return mapping.get(price_id, UNKNOWN_PRICE)


__all__ = ["resolve_tier", "parse_map", "UNKNOWN_PRICE"]
