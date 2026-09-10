"""BUG 3: refunds and chargebacks were not handled at all."""

import time

import pytest

from tmo.payments.event_ledger import ProcessedEventLedger
from tmo.payments.handler import PaddleWebhookHandler
from tmo.payments.store import SubscriberStore
from tests.payments_fixtures import adjustment_event, subscription_event

MAP = "pri_terminal:terminal"


@pytest.fixture
def handler(tmp_path):
    h = PaddleWebhookHandler(
        secret="",
        store=SubscriberStore(str(tmp_path / "s.json"), cache_ttl_s=0),
        event_ledger=ProcessedEventLedger(str(tmp_path / "e.json")),
        tier_price_map=MAP,
    )
    # a live subscriber, paid through a month from now
    far = "2026-10-10T00:00:00Z"
    h.handle(body=subscription_event("subscription.activated", ends_at=far),
             signature_header="")
    return h


def test_a_full_refund_revokes_access_immediately(handler):
    key = handler.store.api_key_of("sub_123")
    assert handler.store.is_active_key(key)
    result = handler.handle(body=adjustment_event(kind="full"), signature_header="")
    assert result["handled"] and result["revoked"] is True
    assert not handler.store.is_active_key(key)


def test_a_full_refund_beats_a_paid_through_date_still_in_the_future(handler):
    handler.handle(body=adjustment_event(kind="full"), signature_header="")
    entry = handler.store.get("sub_123")
    # the grace window is still on file, and the hard ceiling overrides it
    assert entry["expires_at"] <= time.time()
    assert not handler.store.is_active("sub_123")


def test_a_partial_refund_leaves_access_alone(handler):
    key = handler.store.api_key_of("sub_123")
    result = handler.handle(body=adjustment_event(kind="partial"), signature_header="")
    assert result["handled"] and result["revoked"] is False
    assert handler.store.is_active_key(key)


def test_a_chargeback_revokes_whatever_its_size(handler):
    result = handler.handle(body=adjustment_event(action="chargeback", kind="partial"),
                            signature_header="")
    assert result["revoked"] is True
    assert not handler.store.is_active("sub_123")


def test_a_refund_awaiting_approval_changes_nothing(handler):
    result = handler.handle(body=adjustment_event(status="pending_approval"),
                            signature_header="")
    assert result["handled"] is False
    assert handler.store.is_active("sub_123")


def test_a_rejected_refund_changes_nothing(handler):
    result = handler.handle(body=adjustment_event(status="rejected"), signature_header="")
    assert result["handled"] is False
    assert handler.store.is_active("sub_123")


def test_a_chargeback_warning_is_not_a_chargeback(handler):
    result = handler.handle(body=adjustment_event(action="chargeback_warning"),
                            signature_header="")
    assert result["handled"] is False
    assert handler.store.is_active("sub_123")


def test_a_credit_does_not_touch_access(handler):
    result = handler.handle(body=adjustment_event(action="credit"), signature_header="")
    assert result["handled"] is False
    assert handler.store.is_active("sub_123")


def test_the_refund_is_kept_on_the_subscriber_record(handler):
    handler.handle(body=adjustment_event(kind="partial"), signature_header="")
    adjustments = handler.store.get("sub_123")["adjustments"]
    assert len(adjustments) == 1
    assert adjustments[0]["action"] == "refund"
    assert adjustments[0]["revoked"] is False
    assert adjustments[0]["adjustment_id"] == "adj_999"


def test_an_adjustment_is_keyed_by_its_subscription_not_its_own_id(handler):
    """The adjustment payload's data.id is the adjustment. Reading it as the
    subscription id would create a phantom subscriber on every refund."""
    handler.handle(body=adjustment_event(kind="full"), signature_header="")
    assert handler.store.get("adj_999") is None
    assert handler.store.get("sub_123") is not None


def test_a_refund_for_a_subscription_we_never_had_invents_no_subscriber(handler):
    result = handler.handle(body=adjustment_event(subscription_id="sub_nobody"),
                            signature_header="")
    assert result["handled"] is False and result["reason"] == "unknown subscription"
    assert handler.store.get("sub_nobody") is None


def test_an_adjustment_with_no_subscription_is_not_applied(handler):
    body = adjustment_event(subscription_id=None)
    result = handler.handle(body=body, signature_header="")
    assert result["handled"] is False and result["reason"] == "no subscription_id"
