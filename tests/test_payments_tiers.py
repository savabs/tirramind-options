"""BUG 2: an unrecognised price must not provision access."""

import json

import pytest

from tmo.payments import PaddleConfig, PaddleConfigError
from tmo.payments.handler import PaddleWebhookHandler
from tmo.payments.event_ledger import ProcessedEventLedger
from tmo.payments.store import SubscriberStore
from tmo.payments.tiers import UNKNOWN_PRICE, parse_map, resolve_tier
from tests.payments_fixtures import subscription_event

MAP = "pri_terminal:terminal,pri_terminal_year:terminal_annual"


@pytest.fixture
def handler(tmp_path):
    return PaddleWebhookHandler(
        secret="",                                   # verification off for these
        store=SubscriberStore(str(tmp_path / "s.json"), cache_ttl_s=0),
        event_ledger=ProcessedEventLedger(str(tmp_path / "e.json")),
        tier_price_map=MAP,
    )


def test_a_mapped_price_resolves_to_its_tier():
    assert resolve_tier("pri_terminal", mapping_raw=MAP) == "terminal"
    assert resolve_tier("pri_terminal_year", mapping_raw=MAP) == "terminal_annual"


def test_an_unmapped_price_resolves_to_nothing():
    assert resolve_tier("pri_someone_elses_product", mapping_raw=MAP) == UNKNOWN_PRICE
    assert resolve_tier(None, mapping_raw=MAP) == UNKNOWN_PRICE
    assert resolve_tier("pri_terminal", mapping_raw="") == UNKNOWN_PRICE


def test_a_malformed_map_entry_is_skipped_not_guessed_at():
    assert parse_map("pri_a:terminal,garbage,:,pri_b:") == {"pri_a": "terminal"}


def test_an_unknown_price_does_not_activate_a_subscriber(handler):
    body = subscription_event("subscription.activated", price_id="pri_not_ours")
    result = handler.handle(body=body, signature_header="")
    assert result["handled"] is False
    assert "not mapped" in result["reason"]
    assert handler.store.get("sub_123") is None
    assert handler.store.active_keys() == []


def test_a_known_price_does_activate(handler):
    result = handler.handle(body=subscription_event("subscription.activated"),
                            signature_header="")
    assert result["handled"] and result["active"]
    assert result["tier"] == "terminal"
    assert result["api_key"].startswith("tmo_")


def test_an_unknown_price_cannot_extend_an_existing_subscriber(handler):
    handler.handle(body=subscription_event("subscription.activated"), signature_header="")
    key = handler.store.api_key_of("sub_123")
    handler.handle(body=subscription_event("subscription.updated", price_id="pri_not_ours"),
                   signature_header="")
    # unchanged, not upgraded and not downgraded
    assert handler.store.tier_of("sub_123") == "terminal"
    assert handler.store.api_key_of("sub_123") == key


def test_revocation_ignores_the_price_map_entirely(handler):
    handler.handle(body=subscription_event("subscription.activated"), signature_header="")
    result = handler.handle(
        body=subscription_event("subscription.expired", price_id="pri_not_ours"),
        signature_header="")
    assert result["handled"] and result["active"] is False
    assert not handler.store.is_active("sub_123")


def test_live_mode_refuses_to_start_without_a_price_map():
    env = {"TMO_PADDLE_MODE": "live", "TMO_PADDLE_WEBHOOK_SECRET": "pdl_x"}
    with pytest.raises(PaddleConfigError, match="TMO_TIER_PRICE_MAP"):
        PaddleConfig.from_env(env)


def test_live_mode_refuses_to_start_without_a_webhook_secret():
    with pytest.raises(PaddleConfigError, match="WEBHOOK_SECRET"):
        PaddleConfig.from_env({"TMO_PADDLE_MODE": "live"})
