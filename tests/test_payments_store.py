"""BUG 1: a crash during save must not lose every subscriber."""

import json
import os
import time
from pathlib import Path

import pytest

from tmo.payments import atomic
from tmo.payments.store import SubscriberStore, effective_active


@pytest.fixture
def store(tmp_path):
    # cache_ttl_s=0 so each test sees the file, not a neighbour's cached parse
    return SubscriberStore(str(tmp_path / "subscribers.json"), cache_ttl_s=0)


def test_a_failed_write_leaves_the_previous_file_intact(store, tmp_path, monkeypatch):
    store.set_active("sub_1", active=True, tier="terminal")
    before = Path(store._path).read_text()

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(atomic.os, "replace", boom)
    with pytest.raises(OSError):
        store.set_active("sub_2", active=True, tier="terminal")

    assert Path(store._path).read_text() == before
    assert json.loads(before)["sub_1"]["api_key"].startswith("tmo_")


def test_a_failed_write_leaves_no_temp_file_behind(store, tmp_path, monkeypatch):
    store.set_active("sub_1", active=True, tier="terminal")
    monkeypatch.setattr(atomic.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    with pytest.raises(OSError):
        store.set_active("sub_2", active=True, tier="terminal")
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def test_a_value_that_cannot_be_serialised_never_touches_the_file(store):
    store.set_active("sub_1", active=True, tier="terminal")
    before = Path(store._path).read_text()
    store._data["sub_2"] = {"bad": object()}
    with pytest.raises(TypeError):
        store._save()
    assert Path(store._path).read_text() == before


def test_the_state_file_is_not_world_readable(store):
    store.set_active("sub_1", active=True, tier="terminal")
    assert oct(os.stat(store._path).st_mode)[-3:] == "600"


def test_activation_mints_exactly_one_key_and_keeps_it(store):
    first = store.set_active("sub_1", active=True, tier="terminal")["api_key"]
    again = store.set_active("sub_1", active=True, tier="terminal")["api_key"]
    assert first == again and first.startswith("tmo_")


def test_a_cancelled_subscriber_keeps_access_to_the_end_of_the_paid_period():
    entry = {"active": False, "active_until": time.time() + 60}
    assert effective_active(entry, now=time.time())
    assert not effective_active(entry, now=time.time() + 120)


def test_a_hard_expiry_beats_a_grace_window_still_on_file():
    now = time.time()
    entry = {"active": True, "active_until": now + 10_000, "expires_at": now}
    assert not effective_active(entry, now=now)


def test_a_record_with_neither_timestamp_falls_back_to_the_flag():
    assert effective_active({"active": True}, now=time.time())
    assert not effective_active({"active": False}, now=time.time())


def test_active_keys_includes_a_subscriber_inside_their_grace_window(store):
    store.set_active("sub_1", active=False, tier="terminal",
                     active_until=time.time() + 3600)
    key = store.api_key_of("sub_1")
    # no key is minted on an inactive write, so give them one as activation would
    store.set_active("sub_1", active=True, tier="terminal")
    store.set_active("sub_1", active=False, tier="terminal")
    key = store.api_key_of("sub_1")
    assert store.is_active_key(key)
    assert key in store.active_keys()


def test_rotation_needs_a_live_key(store):
    store.set_active("sub_1", active=True, tier="terminal")
    old = store.api_key_of("sub_1")
    new = store.rotate_key_for_api_key(old)
    assert new and new != old
    assert not store.is_active_key(old)
    assert store.rotate_key_for_api_key(old) is None


def test_an_expired_subscriber_cannot_mint_a_fresh_credential(store):
    store.set_active("sub_1", active=True, tier="terminal")
    key = store.api_key_of("sub_1")
    store.set_active("sub_1", active=False, tier="terminal", expires_at=time.time() - 1)
    assert store.rotate_key_for_api_key(key) is None
