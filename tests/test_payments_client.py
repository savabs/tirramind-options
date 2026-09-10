"""The Paddle client: correct calls, and errors that say what went wrong."""

import json

import pytest

from tmo.payments.client import PaddleAPIError, PaddleClient
from tmo.payments.config import PaddleConfig


class FakeResponse:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, *responses):
        self.headers = {}
        self._responses = list(responses)
        self.calls = []

    def request(self, method, url, params=None, data=None, timeout=None):
        self.calls.append({"method": method, "url": url, "params": params,
                           "body": json.loads(data) if data else None})
        return self._responses.pop(0) if self._responses else FakeResponse(200, {"data": []})


def cfg(mode="sandbox", api_key="pdl_sdbx_apikey_test"):
    return PaddleConfig(mode=mode, api_key=api_key, client_token="", webhook_secret="",
                        price_id="", tier_price_map="pri_x:terminal")


def test_it_refuses_to_exist_without_a_key():
    with pytest.raises(ValueError, match="no Paddle API key"):
        PaddleClient(cfg(api_key=""))


def test_sandbox_and_live_are_different_hosts():
    s = FakeSession()
    PaddleClient(cfg(), session=s).list_products()
    assert s.calls[0]["url"].startswith("https://sandbox-api.paddle.com")
    s2 = FakeSession()
    PaddleClient(cfg(mode="live"), session=s2).list_products()
    assert s2.calls[0]["url"].startswith("https://api.paddle.com")


def test_the_key_is_sent_as_a_bearer_token():
    s = FakeSession()
    PaddleClient(cfg(), session=s)
    assert s.headers["Authorization"] == "Bearer pdl_sdbx_apikey_test"


def test_a_price_is_created_in_minor_units_with_a_billing_cycle():
    s = FakeSession(FakeResponse(200, {"data": {"id": "pri_1"}}))
    PaddleClient(cfg(), session=s).create_price(
        product_id="pro_1", description="Terminal monthly (INR)",
        amount_minor=49900, currency_code="INR")
    body = s.calls[0]["body"]
    assert body["unit_price"] == {"amount": "49900", "currency_code": "INR"}
    assert body["billing_cycle"] == {"interval": "month", "frequency": 1}
    assert body["quantity"] == {"minimum": 1, "maximum": 1}


def test_an_error_carries_paddles_own_explanation():
    s = FakeSession(FakeResponse(403, {"error": {"code": "forbidden",
                                                 "detail": "key has been revoked"}}))
    with pytest.raises(PaddleAPIError, match="key has been revoked") as exc:
        PaddleClient(cfg(), session=s).list_products()
    assert exc.value.status == 403


def test_a_non_json_error_still_raises_something_readable():
    class Bad(FakeResponse):
        def json(self):
            raise ValueError("not json")

    s = FakeSession(Bad(502, {}))
    with pytest.raises(PaddleAPIError, match="502"):
        PaddleClient(cfg(), session=s).list_products()


def test_events_page_until_paddle_says_there_are_no_more():
    page1 = FakeResponse(200, {"data": [{"event_id": "evt_1"}, {"event_id": "evt_2"}],
                               "meta": {"pagination": {"has_more": True}}})
    page2 = FakeResponse(200, {"data": [{"event_id": "evt_3"}],
                               "meta": {"pagination": {"has_more": False}}})
    s = FakeSession(page1, page2)
    got = list(PaddleClient(cfg(), session=s).iter_events())
    assert [e["event_id"] for e in got] == ["evt_1", "evt_2", "evt_3"]
    assert s.calls[1]["params"]["after"] == "evt_2"


def test_paging_stops_on_an_empty_page():
    s = FakeSession(FakeResponse(200, {"data": [], "meta": {"pagination": {"has_more": True}}}))
    assert list(PaddleClient(cfg(), session=s).iter_events()) == []


def test_whoami_reports_the_mode_without_leaking_the_key():
    s = FakeSession(FakeResponse(200, {"data": [{"event_id": "evt_1"}],
                                       "meta": {"request_id": "req_1"}}))
    out = PaddleClient(cfg(), session=s).whoami()
    assert out == {"mode": "sandbox", "reachable": True,
                   "request_id": "req_1", "events_visible": 1}
    assert "pdl_sdbx" not in json.dumps(out)
