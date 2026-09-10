"""BUG 4: the route read Content-Length bytes before checking who was asking."""

import http.client
import json
import threading

import pytest

from tmo.payments.event_ledger import ProcessedEventLedger
from tmo.payments.handler import PaddleWebhookHandler
from tmo.payments.server import MAX_BODY_BYTES, safe_content_length, serve
from tmo.payments.store import SubscriberStore
from tests.payments_fixtures import SECRET, sign, subscription_event


@pytest.fixture
def endpoint(tmp_path):
    def factory():
        return PaddleWebhookHandler(
            secret=SECRET,
            store=SubscriberStore(str(tmp_path / "s.json"), cache_ttl_s=0),
            event_ledger=ProcessedEventLedger(str(tmp_path / "e.json")),
            tier_price_map="pri_terminal:terminal",
        )

    httpd = serve("127.0.0.1", 0, handler_factory=factory, read_timeout_s=1.0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield httpd.server_address
    httpd.shutdown()
    httpd.server_close()


def _post(addr, body: bytes, *, headers: dict, raw_length: str | None = None):
    conn = http.client.HTTPConnection(*addr, timeout=5)
    h = dict(headers)
    h["Content-Length"] = raw_length if raw_length is not None else str(len(body))
    conn.putrequest("POST", "/webhook", skip_accept_encoding=True, skip_host=True)
    conn.putheader("Host", f"{addr[0]}:{addr[1]}")
    for k, v in h.items():
        conn.putheader(k, v)
    conn.endheaders()
    conn.send(body)
    resp = conn.getresponse()
    out = (resp.status, json.loads(resp.read() or b"{}"))
    conn.close()
    return out


def test_a_declared_body_over_the_cap_is_refused_without_being_read(endpoint):
    # Declare a gigabyte, send nothing. The old code allocated the gigabyte.
    status, payload = _post(endpoint, b"", headers={},
                            raw_length=str(MAX_BODY_BYTES + 1))
    assert status == 413
    assert payload["error"] == "request body too large"


def test_a_gigabyte_claim_is_refused_promptly(endpoint):
    status, _ = _post(endpoint, b"", headers={}, raw_length=str(1024 ** 3))
    assert status == 413


def test_a_malformed_content_length_does_not_crash_the_worker(endpoint):
    status, payload = _post(endpoint, b"", headers={}, raw_length="not-a-number")
    assert status == 411
    # and the server is still answering
    body = subscription_event("subscription.activated")
    assert _post(endpoint, body, headers={"Paddle-Signature": sign(body)})[0] == 200


def test_a_negative_content_length_is_refused(endpoint):
    assert _post(endpoint, b"", headers={}, raw_length="-1")[0] == 411


def test_a_stalled_body_gets_a_timeout_rather_than_a_parked_thread(endpoint):
    # Declares 500 bytes, sends two. The read must give up, not block forever.
    assert _post(endpoint, b"{}", headers={}, raw_length="500")[0] == 408


def test_a_valid_signed_webhook_is_applied(endpoint):
    body = subscription_event("subscription.activated")
    status, payload = _post(endpoint, body, headers={"Paddle-Signature": sign(body)})
    assert status == 200
    assert payload["ok"] and payload["active"] is True


def test_the_minted_key_is_never_echoed_back_to_paddle(endpoint):
    body = subscription_event("subscription.activated")
    _, payload = _post(endpoint, body, headers={"Paddle-Signature": sign(body)})
    assert "api_key" not in payload


def test_an_unsigned_webhook_is_rejected(endpoint):
    body = subscription_event("subscription.activated")
    status, payload = _post(endpoint, body, headers={})
    assert status == 400
    assert "signature" in payload["error"]


def test_a_forged_signature_is_rejected(endpoint):
    body = subscription_event("subscription.activated")
    bad = sign(body, secret="pdl_ntfset_wrong")
    status, payload = _post(endpoint, body, headers={"Paddle-Signature": bad})
    assert status == 400
    assert "invalid webhook signature" in payload["error"]


def test_a_body_tampered_with_after_signing_is_rejected(endpoint):
    body = subscription_event("subscription.activated")
    header = sign(body)
    tampered = body.replace(b"sub_123", b"sub_999")
    assert _post(endpoint, tampered, headers={"Paddle-Signature": header})[0] == 400


def test_another_path_is_not_the_webhook(endpoint):
    conn = http.client.HTTPConnection(*endpoint, timeout=5)
    conn.request("POST", "/anything", body=b"{}", headers={"Content-Length": "2"})
    assert conn.getresponse().status == 404
    conn.close()


def test_content_length_parsing_is_defensive():
    assert safe_content_length("10") == 10
    assert safe_content_length(None) is None
    assert safe_content_length("") is None
    assert safe_content_length("1e9") is None
    assert safe_content_length("-1") is None
