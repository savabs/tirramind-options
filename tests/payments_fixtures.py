"""Signed webhook bodies, built the way Paddle builds them."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

SECRET = "pdl_ntfset_test_secret"


def sign(body: bytes, *, secret: str = SECRET, ts: int | None = None) -> str:
    ts = int(time.time()) if ts is None else ts
    digest = hmac.new(secret.encode(), f"{ts}:{body.decode()}".encode(), hashlib.sha256)
    return f"ts={ts};h1={digest.hexdigest()}"


def subscription_event(event_type: str, *, subscription_id: str = "sub_123",
                       price_id: str = "pri_terminal", ends_at: str | None = None,
                       customer_id: str = "ctm_1", event_id: str | None = None) -> bytes:
    data = {
        "id": subscription_id,
        "customer_id": customer_id,
        "items": [{"price": {"id": price_id}}],
    }
    if ends_at:
        data["current_billing_period"] = {"starts_at": "2026-09-01T00:00:00Z", "ends_at": ends_at}
    payload = {"event_type": event_type, "data": data}
    if event_id:
        payload["event_id"] = event_id
    return json.dumps(payload).encode()


def adjustment_event(*, action: str = "refund", status: str = "approved",
                     kind: str = "full", subscription_id: str = "sub_123",
                     event_type: str = "adjustment.created",
                     event_id: str | None = None) -> bytes:
    payload = {
        "event_type": event_type,
        "data": {
            "id": "adj_999",                 # the adjustment's own id, not the subscription's
            "action": action,
            "status": status,
            "type": kind,
            "subscription_id": subscription_id,
            "transaction_id": "txn_1",
            "totals": {"total": "1900", "currency_code": "USD"},
        },
    }
    if event_id:
        payload["event_id"] = event_id
    return json.dumps(payload).encode()
