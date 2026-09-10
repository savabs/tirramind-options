"""Verify the session tokens the sign-in edge mints.

The edge (Cloudflare) establishes who someone is. This side decides what they
are entitled to, because this side owns the subscriber record and the rules
about grace windows and refunds that go with it. Two copies of those rules would
be two things to keep correct about money, and the copy nobody watches is the one
that drifts.

Token format, matching ``web/lib/token.ts`` exactly: ``payload.signature``, both
base64url without padding, signature is HMAC-SHA256 over the payload's ASCII
bytes. Not a JWT: there is no algorithm field, so there is no algorithm
confusion, and no library to keep current.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any

from .store import SubscriberStore, effective_active


class SessionError(ValueError):
    """The token is absent, malformed, unsigned, forged or expired."""


@dataclass(frozen=True)
class Identity:
    sub: str
    email: str
    name: str | None
    issued_at: float
    expires_at: float


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def sign(claims: dict[str, Any], secret: str) -> str:
    """Mint a token. Here for tests and local development, not for production:
    in production only the sign-in edge mints, and this side only verifies."""
    payload = _b64url_encode(json.dumps(claims, separators=(",", ":")).encode())
    mac = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return f"{payload}.{_b64url_encode(mac)}"


def verify(token: str | None, secret: str, *, now: float | None = None) -> Identity:
    """Return the identity, or raise. Never returns something half-checked."""
    if not secret:
        raise SessionError("no session secret configured")
    if not token or token.count(".") != 1:
        raise SessionError("not a session token")
    payload, sig = token.split(".")
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    try:
        given = _b64url_decode(sig)
    except Exception as exc:
        raise SessionError("signature is not base64url") from exc
    if not hmac.compare_digest(expected, given):
        raise SessionError("signature does not match")
    try:
        claims = json.loads(_b64url_decode(payload))
    except Exception as exc:
        raise SessionError("payload is not JSON") from exc
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or exp <= (time.time() if now is None else now):
        raise SessionError("session has expired")
    if not claims.get("sub") or not claims.get("email"):
        raise SessionError("session names nobody")
    # The edge refuses to mint for an unverified Google email, because
    # entitlement is matched on it. Refuse again here rather than assume.
    if claims.get("email_verified") is not True:
        raise SessionError("email on this session is not verified")
    return Identity(sub=str(claims["sub"]), email=str(claims["email"]).lower(),
                    name=claims.get("name"), issued_at=float(claims.get("iat", 0)),
                    expires_at=float(exp))


@dataclass(frozen=True)
class Entitlement:
    """What this identity may use, and why."""

    entitled: bool
    tier: str | None
    reason: str
    subscription_id: str | None = None
    active_until: float | None = None
    needs_link: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"entitled": self.entitled, "tier": self.tier, "reason": self.reason,
                "subscription_id": self.subscription_id,
                "active_until": self.active_until, "needs_link": self.needs_link}


def entitlement_for(identity: Identity, store: SubscriberStore, *,
                    now: float | None = None) -> Entitlement:
    """Resolve a signed-in person to a subscription.

    Two ways to be recognised, in order. A subscription explicitly bound to this
    Google account wins, because someone bound it deliberately. Otherwise the
    verified email is matched against the email Paddle holds, which covers the
    ordinary case where a person pays and signs in as themselves.

    Neither matching means the account is real but unrecognised, which is a
    different answer from "not entitled" and gets ``needs_link`` so the terminal
    can offer to bind it with the key we sent, rather than implying they never
    paid.
    """
    t = time.time() if now is None else now
    record = store.by_identity(identity.sub) or store.by_email(identity.email)
    if record is None:
        return Entitlement(False, None, "no subscription is linked to this account",
                           needs_link=True)
    if not effective_active(record, now=t):
        return Entitlement(False, record.get("tier"), "the subscription is not active",
                           subscription_id=record.get("subscription_id"),
                           active_until=record.get("active_until"))
    return Entitlement(True, record.get("tier"), "active subscription",
                       subscription_id=record.get("subscription_id"),
                       active_until=record.get("active_until"))


__all__ = ["Identity", "Entitlement", "SessionError", "verify", "sign", "entitlement_for"]
