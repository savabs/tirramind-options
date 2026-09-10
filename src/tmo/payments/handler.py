"""Verify one Paddle webhook and apply it to subscriber state.

Lifecycle, and what each event does to access:

    created / updated / activated / revived / trialing
        grant access, mint a key on first activation, and record the
        paid-through date from ``current_billing_period.ends_at``
    canceled / paused
        ``active`` goes False, but the paid-through date already on file keeps
        the key working to the end of the period the customer paid for
    past_due
        a grace window, not revocation. A failed card is not a cancellation and
        Paddle is still retrying
    expired
        dunning is over. Revoke now, overriding any grace window
    adjustment.created / adjustment.updated
        a refund or a chargeback. A full refund or any chargeback revokes
        immediately. A partial refund is recorded and changes nothing

Verification is mandatory whenever a webhook secret is configured, and skipped
only when none is, which is how local development works without one.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from typing import Any, Callable

from .event_ledger import ProcessedEventLedger
from .store import SubscriberStore
from .tiers import UNKNOWN_PRICE, resolve_tier
from .webhook import WebhookVerificationError, is_verified, verify_webhook_signature

logger = logging.getLogger(__name__)

ACTIVE_EVENTS = {
    "subscription.created", "subscription.updated", "subscription.activated",
    "subscription.revived", "subscription.trialing",
}
GRACE_UNTIL_PERIOD_END_EVENTS = {"subscription.canceled", "subscription.paused"}
PAST_DUE_EVENTS = {"subscription.past_due"}
HARD_REVOKE_EVENTS = {"subscription.expired"}
ADJUSTMENT_EVENTS = {"adjustment.created", "adjustment.updated"}

# A refund the customer's money actually went back for. "pending_approval" has
# not happened yet and "rejected" never will, so neither touches access.
_REFUND_SETTLED_STATUSES = {"approved"}
# A chargeback is the bank reversing the payment over our head. It revokes
# whatever its size. "chargeback_warning" is a notification, not a reversal,
# and "chargeback_reverse" means we won it back, so neither revokes.
_REVOKING_ACTIONS = {"refund", "chargeback"}

_PAST_DUE_GRACE_ENV = "TMO_PAST_DUE_GRACE_S"
_DEFAULT_PAST_DUE_GRACE_S = 3 * 24 * 3600.0


def past_due_grace_s() -> float:
    raw = os.getenv(_PAST_DUE_GRACE_ENV, "").strip()
    try:
        return float(raw) if raw else _DEFAULT_PAST_DUE_GRACE_S
    except ValueError:
        return _DEFAULT_PAST_DUE_GRACE_S


def parse_paddle_timestamp(value: str | None) -> float | None:
    """RFC 3339 to epoch seconds. None for anything unparseable.

    Callers must treat None as "unknown" and never as epoch zero, which would
    read as a date far in the past and revoke a paying customer.
    """
    if not value or not isinstance(value, str):
        return None
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(v).timestamp()
    except ValueError:
        return None


def extract_active_until(data: dict[str, Any]) -> float | None:
    """The paid-through date on a subscription payload, or None.

    Every active or trialing subscription carries ``current_billing_period``.
    It is null once the subscription has actually reached cancelled or paused,
    by which point there is no current period, so only an access-granting event
    ever populates this. The scheduled-change fallback covers a cancel-at-period
    -end, which Paddle sends as an update while the status is still active.
    """
    period = data.get("current_billing_period") or {}
    ends_at = parse_paddle_timestamp(period.get("ends_at"))
    if ends_at is not None:
        return ends_at
    scheduled = data.get("scheduled_change") or {}
    return parse_paddle_timestamp(scheduled.get("effective_at"))


def _log_only_delivery(email: str | None, *, api_key: str, tier: str | None) -> None:
    """Default key delivery: say that a key was minted, never the key itself.

    No mail is configured for this product yet, and putting a live credential
    into a log line would be worse than not delivering it.
    """
    logger.info("[paddle] minted a %s key for %s; delivery is not configured",
                tier or "unknown-tier", email or "an unknown address")


class PaddleWebhookHandler:
    """Verify and apply one Paddle webhook payload."""

    def __init__(
        self,
        secret: str,
        store: SubscriberStore | None = None,
        event_ledger: ProcessedEventLedger | None = None,
        paddle_client: Any = None,
        tier_price_map: str | None = None,
        deliver_key: Callable[..., None] | None = None,
    ) -> None:
        self.secret = secret
        self.store = store or SubscriberStore()
        self.event_ledger = event_ledger if event_ledger is not None else ProcessedEventLedger()
        # Used only to resolve a customer's email, since no subscription or
        # transaction payload carries one. Optional: with no client, a
        # subscriber simply has no email on file.
        self._paddle_client = paddle_client
        self._tier_price_map = tier_price_map
        self._deliver_key = deliver_key or _log_only_delivery

    # ── helpers ────────────────────────────────────────────────────────────
    def _fetch_customer_email(self, customer_id: str | None) -> str | None:
        """Best effort. A Paddle hiccup must never block an activation."""
        if not customer_id or self._paddle_client is None:
            return None
        try:
            customer = self._paddle_client.get_customer(customer_id)
        except Exception as exc:  # noqa: BLE001 - see docstring
            logger.warning("[paddle] could not fetch customer %s: %s: %s",
                           customer_id, type(exc).__name__, exc)
            return None
        email = customer.get("email") if isinstance(customer, dict) else None
        return email.strip() if isinstance(email, str) and email.strip() else None

    def _tier(self, data: dict[str, Any]) -> str:
        items = data.get("items") or []
        price_id = (items[0].get("price") or {}).get("id") if items else None
        return resolve_tier(price_id, mapping_raw=self._tier_price_map)

    @staticmethod
    def _subscription_id(event_type: str, data: dict[str, Any]) -> str | None:
        """Where the subscription id lives depends on the object.

        On an adjustment, ``data.id`` is the adjustment's own id and the
        subscription is a separate field. Reading ``data.id`` there, as the
        subscription path does, would key subscriber state by adjustment id and
        create a phantom subscriber on every refund.
        """
        if event_type in ADJUSTMENT_EVENTS:
            return data.get("subscription_id")
        return (data.get("id") or data.get("subscription_id")
                or (data.get("subscription") or {}).get("id"))

    # ── the adjustment path ────────────────────────────────────────────────
    def _handle_adjustment(self, event_type: str, subscription_id: str,
                           data: dict[str, Any]) -> dict[str, Any]:
        action = (data.get("action") or "").strip().lower()
        status = (data.get("status") or "").strip().lower()
        kind = (data.get("type") or "").strip().lower()
        summary = {"event_type": event_type, "subscription_id": subscription_id,
                   "adjustment_id": data.get("id"), "action": action,
                   "status": status, "type": kind}

        if self.store.get(subscription_id) is None:
            # A refund against a subscription we never provisioned. Writing a
            # revoked record here would invent a subscriber that never existed;
            # say so instead.
            logger.warning("[paddle] %s on unknown subscription %s, ignored",
                           action or "adjustment", subscription_id)
            return {**summary, "handled": False, "reason": "unknown subscription"}
        if action not in _REVOKING_ACTIONS:
            # A credit, a chargeback warning, a chargeback we won back. Recorded
            # by Paddle, no effect on access here.
            return {**summary, "handled": False, "reason": f"adjustment action {action!r} does not affect access"}
        if action == "refund" and status not in _REFUND_SETTLED_STATUSES:
            return {**summary, "handled": False, "reason": f"refund is {status!r}, not settled"}

        # A chargeback revokes whatever its size: the bank has taken the money
        # back and the customer is no longer paying for anything. A partial
        # refund is a goodwill gesture on an otherwise live subscription, so it
        # is recorded and access continues.
        revoke = action == "chargeback" or kind == "full"
        if revoke:
            now = time.time()
            self.store.set_active(subscription_id, active=False,
                                  active_until=now, expires_at=now)
        self.store.record_refund(subscription_id, adjustment=summary, revoked=revoke)
        logger.info("[paddle] %s %s on subscription %s: access %s",
                    kind or "unsized", action, subscription_id,
                    "revoked" if revoke else "unchanged")
        return {**summary, "handled": True, "active": False if revoke else None,
                "revoked": revoke,
                "reason": "full refund or chargeback" if revoke else "partial refund, access unchanged"}

    # ── entry point ────────────────────────────────────────────────────────
    def handle(self, *, body: bytes, signature_header: str) -> dict[str, Any]:
        """Verify and process one webhook. Raises on a bad signature."""
        if is_verified(self.secret):
            verify_webhook_signature(body=body, signature_header=signature_header,
                                     secret=self.secret)
        try:
            payload = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise WebhookVerificationError("webhook body is not valid JSON") from exc

        event_type = payload.get("event_type", "")

        # Paddle sends a stable event id and reuses it on retries. This is both
        # the replay defence inside the signature's time window and what makes
        # an ordinary retry a no-op instead of a double write. A payload with no
        # event id cannot be deduplicated, so it is processed rather than
        # silently dropped.
        event_id = payload.get("event_id")
        if event_id and not self.event_ledger.check_and_mark(event_id):
            logger.info("[paddle] duplicate event %s (%s) ignored", event_id, event_type)
            return {"event_type": event_type, "handled": False,
                    "reason": "duplicate event (already processed)", "event_id": event_id}

        data = payload.get("data", {}) or {}
        subscription_id = self._subscription_id(event_type, data)
        if not subscription_id:
            return {"event_type": event_type, "handled": False, "reason": "no subscription_id"}

        if event_type in ADJUSTMENT_EVENTS:
            return self._handle_adjustment(event_type, subscription_id, data)

        customer_id = data.get("customer_id") or (data.get("customer") or {}).get("id")
        email = (data.get("customer") or {}).get("email")
        tier = self._tier(data)

        if event_type in ACTIVE_EVENTS:
            if tier == UNKNOWN_PRICE:
                # Fail closed. See tiers.py: we provision only prices we sell.
                logger.error("[paddle] refusing to activate subscription %s: its price is not "
                             "in TMO_TIER_PRICE_MAP", subscription_id)
                return {"event_type": event_type, "handled": False,
                        "subscription_id": subscription_id,
                        "reason": "price is not mapped to a product tier"}
            prior = self.store.get(subscription_id)
            had_key = bool(prior and prior.get("api_key"))
            had_email = bool(prior and prior.get("email"))
            resolved_email = email
            if not resolved_email and not had_email and customer_id:
                resolved_email = self._fetch_customer_email(customer_id)
            entry = self.store.set_active(
                subscription_id, active=True, customer_id=customer_id,
                email=resolved_email, tier=tier,
                active_until=extract_active_until(data))
            if not had_key and entry.get("api_key"):
                self._deliver_key(entry.get("email"), api_key=entry["api_key"],
                                  tier=entry.get("tier"))
            active = True
        elif event_type in GRACE_UNTIL_PERIOD_END_EVENTS:
            # Passing the extracted value, usually None, leaves the paid-through
            # date captured earlier untouched, which is what keeps the key alive
            # to the end of the period the customer paid for.
            entry = self.store.set_active(
                subscription_id, active=False, customer_id=customer_id, email=email,
                tier=tier, active_until=extract_active_until(data))
            active = False
        elif event_type in PAST_DUE_EVENTS:
            prior = self.store.get(subscription_id)
            prior_until = prior.get("active_until") if prior else None
            grace_until = time.time() + past_due_grace_s()
            # Never shrink a longer window that is already on file: this can
            # fire mid-cycle with weeks still paid for.
            new_until = max(prior_until, grace_until) if prior_until is not None else grace_until
            entry = self.store.set_active(
                subscription_id, active=False, customer_id=customer_id, email=email,
                tier=tier, active_until=new_until)
            active = False
        elif event_type in HARD_REVOKE_EVENTS:
            now = time.time()
            entry = self.store.set_active(
                subscription_id, active=False, customer_id=customer_id, email=email,
                tier=tier, active_until=now, expires_at=now)
            active = False
        else:
            return {"event_type": event_type, "handled": False, "reason": "unhandled event"}

        logger.info("[paddle] %s applied to subscription %s, active=%s",
                    event_type, subscription_id, active)
        return {"event_type": event_type, "handled": True,
                "subscription_id": subscription_id, "active": active,
                "tier": entry.get("tier"), "api_key": entry.get("api_key")}


__all__ = ["PaddleWebhookHandler", "ACTIVE_EVENTS", "ADJUSTMENT_EVENTS",
           "extract_active_until", "parse_paddle_timestamp", "past_due_grace_s"]
