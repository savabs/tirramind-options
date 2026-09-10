"""Paddle billing for the Terminal subscription.

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
"""

from .config import PaddleConfig, PaddleConfigError
from .event_ledger import ProcessedEventLedger
from .handler import PaddleWebhookHandler
from .store import SubscriberStore
from .tiers import UNKNOWN_PRICE, resolve_tier
from .webhook import WebhookVerificationError, is_verified, verify_webhook_signature

__all__ = [
    "PaddleConfig", "PaddleConfigError", "PaddleWebhookHandler",
    "ProcessedEventLedger", "SubscriberStore", "UNKNOWN_PRICE", "resolve_tier",
    "WebhookVerificationError", "is_verified", "verify_webhook_signature",
]
