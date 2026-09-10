"""The whole path a paying customer takes, in order.

Checkout, key, renewal, cancellation inside the paid period, expiry. Then the
same path interrupted by a refund. This is the test that would have caught all
four defects between them, so it is written as one story rather than as units.
"""

import time

import pytest

from tmo.payments.event_ledger import ProcessedEventLedger
from tmo.payments.handler import PaddleWebhookHandler
from tmo.payments.store import SubscriberStore
from tests.payments_fixtures import SECRET, adjustment_event, sign, subscription_event

MAP = "pri_terminal:terminal"
NEXT_MONTH = "2026-10-10T00:00:00Z"


@pytest.fixture
def h(tmp_path):
    return PaddleWebhookHandler(
        secret=SECRET,
        store=SubscriberStore(str(tmp_path / "s.json"), cache_ttl_s=0),
        event_ledger=ProcessedEventLedger(str(tmp_path / "e.json")),
        tier_price_map=MAP,
    )


def send(h, body, *, ts=None):
    return h.handle(body=body, signature_header=sign(body, ts=ts))


def test_checkout_to_key(h):
    body = subscription_event("subscription.created", ends_at=NEXT_MONTH,
                              event_id="evt_1")
    result = send(h, body)
    key = result["api_key"]
    assert key.startswith("tmo_")
    assert result["tier"] == "terminal"
    assert h.store.is_active_key(key)


def test_a_renewal_does_not_mint_a_second_key(h):
    first = send(h, subscription_event("subscription.created", ends_at=NEXT_MONTH,
                                       event_id="evt_1"))["api_key"]
    second = send(h, subscription_event("subscription.updated", ends_at=NEXT_MONTH,
                                        event_id="evt_2"))["api_key"]
    assert first == second


def test_a_paddle_retry_of_the_same_event_is_a_no_op(h):
    body = subscription_event("subscription.created", ends_at=NEXT_MONTH, event_id="evt_1")
    first = send(h, body)
    again = send(h, body)
    assert again["handled"] is False
    assert again["reason"].startswith("duplicate")
    assert h.store.api_key_of("sub_123") == first["api_key"]


def test_cancelling_keeps_the_key_working_to_the_end_of_the_paid_period(h):
    key = send(h, subscription_event("subscription.created", ends_at=NEXT_MONTH,
                                     event_id="evt_1"))["api_key"]
    # a cancellation payload carries no billing period of its own
    send(h, subscription_event("subscription.canceled", event_id="evt_2"))
    entry = h.store.get("sub_123")
    assert entry["active"] is False
    assert h.store.is_active_key(key), "the customer paid through October"
    # and it does stop once that date passes
    assert not h.store.is_active("sub_123", now=entry["active_until"] + 1)


def test_a_failed_card_gets_a_grace_window_not_a_lockout(h, monkeypatch):
    monkeypatch.setenv("TMO_PAST_DUE_GRACE_S", "3600")
    key = send(h, subscription_event("subscription.created", event_id="evt_1"))["api_key"]
    send(h, subscription_event("subscription.past_due", event_id="evt_2"))
    assert h.store.is_active_key(key)
    assert not h.store.is_active("sub_123", now=time.time() + 7200)


def test_past_due_never_shortens_a_longer_paid_period(h, monkeypatch):
    monkeypatch.setenv("TMO_PAST_DUE_GRACE_S", "60")
    send(h, subscription_event("subscription.created", ends_at=NEXT_MONTH, event_id="evt_1"))
    paid_through = h.store.get("sub_123")["active_until"]
    send(h, subscription_event("subscription.past_due", event_id="evt_2"))
    assert h.store.get("sub_123")["active_until"] == paid_through


def test_expiry_revokes_at_once_overriding_any_grace(h):
    key = send(h, subscription_event("subscription.created", ends_at=NEXT_MONTH,
                                     event_id="evt_1"))["api_key"]
    send(h, subscription_event("subscription.expired", event_id="evt_2"))
    assert not h.store.is_active_key(key)


def test_a_refund_mid_period_ends_access_there_and_then(h):
    key = send(h, subscription_event("subscription.created", ends_at=NEXT_MONTH,
                                     event_id="evt_1"))["api_key"]
    assert h.store.is_active_key(key)
    send(h, adjustment_event(kind="full", event_id="evt_2"))
    assert not h.store.is_active_key(key)
    assert h.store.get("sub_123")["adjustments"][0]["revoked"] is True


def test_a_stale_signature_is_rejected(h):
    body = subscription_event("subscription.created", event_id="evt_1")
    with pytest.raises(Exception, match="stale"):
        send(h, body, ts=int(time.time()) - 3600)


def test_a_replayed_activation_cannot_resurrect_a_cancelled_subscriber(h):
    body = subscription_event("subscription.created", event_id="evt_1")
    send(h, body)
    send(h, subscription_event("subscription.expired", event_id="evt_2"))
    key = h.store.api_key_of("sub_123")
    assert not h.store.is_active_key(key)
    # the captured activation, replayed inside the signature window
    send(h, body)
    assert not h.store.is_active_key(key), "the event ledger must refuse the replay"
