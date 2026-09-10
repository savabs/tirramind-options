"""Paddle billing for the Terminal subscription.

**The production webhook is not here.** It runs at the edge, in
``web/functions/webhook.ts``, with subscriber state in D1, because billing that
depends on one machine being up is billing that stops when that machine stops.

What remains here is worth keeping for two reasons. It is the reference
implementation of the same rules, and it is the oracle they are checked against:
``web/lib/rules.json`` is a table of cases that both this and the TypeScript
implementation must satisfy, so neither can drift without the other's tests
failing. It is also what a local run uses, where there is no D1.

Ported from tirramind/agent/payments, with the four defects that shipped
there fixed and pinned by tests:

  1. ``SubscriberStore`` saved by truncating and rewriting the file, so a
     crash mid-write lost every subscriber. It now writes a sibling temp file
     and ``os.replace``s it into position.
  2. ``_resolve_tier`` fell back to a real tier for any price it did not
     recognise, so a webhook for a price we do not sell provisioned access.
     An unrecognised price now resolves to nothing and refuses to activate.
  3. Refunds and chargebacks (``adjustment.*``) were not handled at all. A
     refunded customer kept a working key until their period ran out.
  4. The webhook route read ``Content-Length`` bytes before verifying the
     signature, so an unauthenticated caller could ask the process to
     allocate an arbitrary amount of memory. The body is now capped first.

A fifth thing was learned later and is worth recording here: Paddle Billing has
no ``subscription.expired`` event. Paddle refuses it as an invalid subscription,
so the hard-revoke branch below can never fire from a webhook. It is kept
because the rule it encodes is correct, and because refunds and chargebacks
reach the same code path. Dunning ends in a cancellation, not an expiry.
"""

from .client import PaddleAPIError, PaddleClient
from .config import PaddleConfig, PaddleConfigError
from .event_ledger import ProcessedEventLedger
from .handler import PaddleWebhookHandler
from .store import SubscriberStore
from .tiers import UNKNOWN_PRICE, resolve_tier
from .webhook import WebhookVerificationError, is_verified, verify_webhook_signature

__all__ = [
    "PaddleAPIError", "PaddleClient",
    "PaddleConfig", "PaddleConfigError", "PaddleWebhookHandler",
    "ProcessedEventLedger", "SubscriberStore", "UNKNOWN_PRICE", "resolve_tier",
    "WebhookVerificationError", "is_verified", "verify_webhook_signature",
]
