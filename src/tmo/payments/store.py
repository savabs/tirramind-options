"""Subscriber access state: who has a working key, and until when.

Ported from tirramind's ``handler.py``, where this class lived alongside the
webhook handler. The save path is the one thing that changed: see
``atomic.write_json`` for why, and BUG 1 in the package docstring.

The access question resolves through exactly one function, ``effective_active``.
Every route-facing check goes through it, so the policy for "cancelled but
paid through Friday" lives in one place instead of being re-derived at each
call site.
"""

from __future__ import annotations

import copy
import json
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from .atomic import write_json
from .tiers import UNKNOWN_PRICE

DEFAULT_STATE_PATH = ".tmo/subscribers.json"

# A read cache, because a gated request asks "is this key live" more than once
# and each ask would otherwise be a disk read, a JSON parse and a linear scan.
# The write path updates this cache immediately, so a subscriber activated or
# revoked by a webhook is visible to the next lookup in this process at once,
# whatever the TTL says. The TTL only bounds staleness for changes that did
# not come through this class: a hand edit, or a second process writing the
# same file. That second case is a real bug waiting for the day this becomes
# a fleet, and it is flagged here rather than assumed away.
_CACHE_TTL_S = 2.0
_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, dict[str, dict[str, Any]]]] = {}


def generate_api_key() -> str:
    """A customer-facing key, distinct from the internal Paddle subscription id."""
    return "tmo_" + secrets.token_urlsafe(24)


def effective_active(entry: dict[str, Any] | None, *, now: float) -> bool:
    """Is this subscriber's access valid right now.

    Precedence, most restrictive first:

    1. ``expires_at`` is a hard ceiling. Past it, access is gone whatever else
       says. This is how a refund or an expiry overrides a grace window that is
       still on file.
    2. ``active_until`` in the future grants access even when ``active`` is
       False. This is what makes a cancellation honour the period the customer
       already paid for, and what gives a failed card a grace window instead of
       instant revocation.
    3. Otherwise the plain ``active`` flag, so a record written before either
       timestamp existed behaves exactly as it did before.
    """
    if not entry:
        return False
    expires_at = entry.get("expires_at")
    if expires_at is not None and now >= expires_at:
        return False
    active_until = entry.get("active_until")
    if active_until is not None and now < active_until:
        return True
    return bool(entry.get("active"))


class SubscriberStore:
    """Persistent record of subscriber access state."""

    def __init__(self, path: str = DEFAULT_STATE_PATH, *, cache_ttl_s: float | None = None) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_key = str(self._path)
        self._cache_ttl_s = _CACHE_TTL_S if cache_ttl_s is None else cache_ttl_s
        self._data: dict[str, dict[str, Any]] = {}
        self._load()

    # ── persistence ────────────────────────────────────────────────────────
    def _load(self) -> None:
        if self._cache_ttl_s > 0:
            with _cache_lock:
                cached = _cache.get(self._cache_key)
                if cached is not None and (time.time() - cached[0]) < self._cache_ttl_s:
                    # Deep copy: entries are mutated in place below, and one
                    # instance's half-finished mutation must not leak into
                    # another's view before it has been saved.
                    self._data = copy.deepcopy(cached[1])
                    return
        if not self._path.exists():
            self._data = {}
        else:
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                self._data = {}
        self._update_cache()

    def _update_cache(self) -> None:
        if self._cache_ttl_s <= 0:
            return
        with _cache_lock:
            _cache[self._cache_key] = (time.time(), copy.deepcopy(self._data))

    def _save(self) -> None:
        write_json(self._path, self._data)
        self._update_cache()

    # ── writes ─────────────────────────────────────────────────────────────
    def set_active(
        self,
        subscription_id: str,
        *,
        active: bool,
        customer_id: str | None = None,
        email: str | None = None,
        tier: str | None = None,
        active_until: float | None = None,
        expires_at: float | None = None,
    ) -> dict[str, Any]:
        """Write or update one subscriber record.

        Every optional field follows "only overwrite when the caller supplies
        something". Most webhooks have nothing new to say about the paid-through
        date, and a bare cancellation carries no billing period at all, so
        passing None must leave what was captured earlier alone rather than
        clearing it.
        """
        entry = self._data.get(subscription_id, {})
        entry["subscription_id"] = subscription_id
        entry["active"] = bool(active)
        entry["customer_id"] = customer_id or entry.get("customer_id")
        entry["email"] = email or entry.get("email")
        if tier and tier != UNKNOWN_PRICE:
            entry["tier"] = tier
        else:
            entry.setdefault("tier", None)
        if active_until is not None:
            entry["active_until"] = active_until
        else:
            entry.setdefault("active_until", None)
        if expires_at is not None:
            entry["expires_at"] = expires_at
        else:
            entry.setdefault("expires_at", None)
        if active and not entry.get("api_key"):
            entry["api_key"] = generate_api_key()
        entry["updated_at"] = time.time()
        self._data[subscription_id] = entry
        self._save()
        return dict(entry)

    def record_refund(self, subscription_id: str, *, adjustment: dict[str, Any],
                      revoked: bool) -> dict[str, Any] | None:
        """Note a refund or chargeback against a subscriber, revoking or not.

        Kept on the record rather than only in the log, because "did this
        customer get their money back" is a question support asks later and a
        log line is not a place to answer it from.
        """
        entry = self._data.get(subscription_id)
        if entry is None:
            return None
        history = entry.setdefault("adjustments", [])
        history.append({**adjustment, "revoked": revoked, "recorded_at": time.time()})
        # Keep the record bounded; the useful signal is recent.
        del history[:-20]
        entry["updated_at"] = time.time()
        self._save()
        return dict(entry)

    def rotate_key(self, subscription_id: str) -> str | None:
        """Mint a new key and invalidate the old one. None if unknown."""
        entry = self._data.get(subscription_id)
        if entry is None:
            return None
        entry["api_key"] = generate_api_key()
        entry["updated_at"] = time.time()
        self._save()
        return entry["api_key"]

    def rotate_key_for_api_key(self, old_api_key: str) -> str | None:
        """Self-service rotation: prove ownership with the current key.

        None when the key is unknown or its subscriber is not currently active.
        An inactive subscriber has no standing to mint a fresh credential.
        """
        entry = self._by_api_key(old_api_key)
        if entry is None or not effective_active(entry, now=time.time()):
            return None
        sid = entry.get("subscription_id")
        return self.rotate_key(sid) if sid else None

    def revoke_key(self, subscription_id: str) -> bool:
        """Invalidate the key without minting a replacement."""
        entry = self._data.get(subscription_id)
        if entry is None:
            return False
        entry["api_key"] = None
        entry["updated_at"] = time.time()
        self._save()
        return True

    # ── reads ──────────────────────────────────────────────────────────────
    def _by_api_key(self, api_key: str) -> dict[str, Any] | None:
        if not api_key:
            return None
        for entry in self._data.values():
            if entry.get("api_key") == api_key:
                return entry
        return None

    def is_active(self, subscription_id: str, *, now: float | None = None) -> bool:
        return effective_active(self._data.get(subscription_id),
                                now=time.time() if now is None else now)

    def is_active_key(self, api_key: str, *, now: float | None = None) -> bool:
        """The customer-facing lookup. Subscribers present the opaque key."""
        return effective_active(self._by_api_key(api_key),
                                now=time.time() if now is None else now)

    def get(self, subscription_id: str) -> dict[str, Any] | None:
        entry = self._data.get(subscription_id)
        return dict(entry) if entry else None

    def tier_of(self, subscription_id: str) -> str | None:
        return self._data.get(subscription_id, {}).get("tier")

    def tier_of_key(self, api_key: str) -> str | None:
        entry = self._by_api_key(api_key)
        return entry.get("tier") if entry else None

    def api_key_of(self, subscription_id: str) -> str | None:
        return self._data.get(subscription_id, {}).get("api_key")

    def all(self) -> dict[str, dict[str, Any]]:
        return copy.deepcopy(self._data)

    def active_keys(self, *, now: float | None = None) -> list[str]:
        """Keys that work right now.

        Resolved through ``effective_active`` rather than the bare ``active``
        flag, so a customer inside their paid-through grace window appears here
        exactly as they appear to ``is_active_key``. The two disagreeing was
        how "cancelled but still entitled" users went missing from ops views.
        """
        t = time.time() if now is None else now
        return [e["api_key"] for e in self._data.values()
                if e.get("api_key") and effective_active(e, now=t)]


__all__ = ["SubscriberStore", "effective_active", "generate_api_key", "DEFAULT_STATE_PATH"]
