"""The account endpoints: who is signed in, and what they may use."""

import pytest
from fastapi.testclient import TestClient

from tmo.payments import session as S
from tmo.payments.store import SubscriberStore
from tmo.service.app import create_app

SECRET = "a-secret-that-is-long-enough-to-be-a-secret"


def token(email="someone@example.com", sub="google-1", ttl=3600):
    import time
    now = int(time.time())
    return S.sign({"sub": sub, "email": email, "email_verified": True, "name": "Someone",
                   "iat": now, "exp": now + ttl}, SECRET)


@pytest.fixture
def store(tmp_path):
    return SubscriberStore(str(tmp_path / "s.json"), cache_ttl_s=0)


@pytest.fixture
def client(store):
    app = create_app(builder=lambda c: {}, warm=False, store=store, session_secret=SECRET)
    with TestClient(app) as c:
        yield c


def test_without_a_session_there_is_no_account(client):
    assert client.get("/me").status_code == 401


def test_a_forged_session_is_refused(client):
    bad = S.sign({"sub": "x", "email": "a@b.com", "email_verified": True, "exp": 9e9},
                 "some-other-secret")
    r = client.get("/me", headers={"Authorization": f"Bearer {bad}"})
    assert r.status_code == 401 and "signature" in r.json()["detail"]


def test_an_expired_session_says_so(client):
    r = client.get("/me", headers={"Authorization": f"Bearer {token(ttl=-1)}"})
    assert r.status_code == 401 and "expired" in r.json()["detail"]


def test_the_token_is_accepted_as_a_cookie_too(client, store):
    store.set_active("sub_1", active=True, tier="terminal", email="someone@example.com")
    client.cookies.set("tmo_session", token())
    body = client.get("/me").json()
    assert body["entitled"] and body["tier"] == "terminal"


def test_a_paying_subscriber_sees_their_tier(client, store):
    store.set_active("sub_1", active=True, tier="terminal", email="someone@example.com")
    body = client.get("/me", headers={"Authorization": f"Bearer {token()}"}).json()
    assert body["entitled"] and body["subscription_id"] == "sub_1"
    assert body["email"] == "someone@example.com"


def test_an_unrecognised_account_is_offered_the_link(client):
    body = client.get("/me", headers={"Authorization": f"Bearer {token()}"}).json()
    assert body["entitled"] is False and body["needs_link"] is True


def test_linking_with_the_key_binds_the_account(client, store):
    store.set_active("sub_1", active=True, tier="terminal", email="paid@example.com")
    key = store.api_key_of("sub_1")
    auth = {"Authorization": f"Bearer {token(email='signin@example.com')}"}
    assert client.get("/me", headers=auth).json()["needs_link"] is True
    r = client.post("/link", headers=auth, json={"api_key": key})
    assert r.status_code == 200 and r.json()["linked"] and r.json()["entitled"]
    assert client.get("/me", headers=auth).json()["entitled"] is True


def test_a_wrong_key_is_refused(client, store):
    store.set_active("sub_1", active=True, tier="terminal", email="paid@example.com")
    r = client.post("/link", headers={"Authorization": f"Bearer {token()}"},
                    json={"api_key": "tmo_not_a_real_key"})
    assert r.status_code == 403


def test_an_unknown_key_and_an_inactive_one_give_the_same_answer(client, store):
    """Otherwise the difference tells someone guessing which keys exist."""
    store.set_active("sub_1", active=True, tier="terminal", email="paid@example.com")
    key = store.api_key_of("sub_1")
    store.set_active("sub_1", active=False, tier="terminal", expires_at=0)
    auth = {"Authorization": f"Bearer {token()}"}
    inactive = client.post("/link", headers=auth, json={"api_key": key})
    unknown = client.post("/link", headers=auth, json={"api_key": "tmo_nonsense"})
    assert inactive.status_code == unknown.status_code == 403
    assert inactive.json()["detail"] == unknown.json()["detail"]


def test_linking_needs_a_session_of_its_own(client, store):
    store.set_active("sub_1", active=True, tier="terminal", email="paid@example.com")
    r = client.post("/link", json={"api_key": store.api_key_of("sub_1")})
    assert r.status_code == 401


def test_a_service_with_no_subscriber_records_says_so_rather_than_guessing():
    app = create_app(builder=lambda c: {}, warm=False, store=None, session_secret=SECRET)
    with TestClient(app) as c:
        r = c.get("/me", headers={"Authorization": f"Bearer {token()}"})
        assert r.status_code == 503


def test_the_surface_stays_free(client):
    """Sign-in gates the paid parts. The public surface is not one of them."""
    assert client.get("/health").status_code == 200
