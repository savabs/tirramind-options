"""The fit service. One job: hand back an arbitrage-checked surface, fast.

Fast means served from a warm cache. A cold fit is about eight seconds, so the
service keeps its currencies warm in the background and a request never waits
on one unless the cache has nothing at all.

The spec asked for a cache keyed per venue and expiry. It is keyed per venue and
currency instead, because the eSSVI backbone is fitted across the whole chain at
once: the expiries are not independent and there is no such thing as refitting
one of them. Per-expiry responses are views onto that one cached fit, which is
the same saving with none of the inconsistency between neighbouring slices.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request

from . import surface
from ..payments import session as sess
from ..payments.store import SubscriberStore
from .cache import SurfaceCache

logger = logging.getLogger(__name__)

SUPPORTED = {("deribit", "BTC"), ("deribit", "ETH")}
DEFAULT_TTL_S = 30.0


def create_app(*, builder: Callable[[str], dict[str, Any]] | None = None,
               ttl_s: float | None = None, warm: bool = True,
               refresh_interval_s: float = 5.0,
               store: SubscriberStore | None = None,
               session_secret: str | None = None) -> FastAPI:
    build = builder or (lambda currency: surface.build(currency))
    ttl = DEFAULT_TTL_S if ttl_s is None else ttl_s
    cache = SurfaceCache(ttl_s=ttl)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # Start warm so the first customer request is a cache read, not a fit.
        if warm:
            cache.start_refreshing({k: (lambda c=k[1]: build(c)) for k in SUPPORTED},
                                   interval_s=refresh_interval_s)
        yield
        cache.stop()

    app = FastAPI(title="TirraMind Options fit service",
                  description="Arbitrage-checked volatility surfaces, with their residuals.",
                  version="0.1.0", lifespan=lifespan)
    app.state.cache = cache
    app.state.store = store
    app.state.session_secret = (os.getenv("TMO_SESSION_SECRET", "")
                                if session_secret is None else session_secret)

    def key(venue: str, currency: str) -> tuple[str, str]:
        v, c = venue.lower(), currency.upper()
        if (v, c) not in SUPPORTED:
            raise HTTPException(404, f"no surface for {venue}/{currency}; "
                                     f"this service serves {sorted(SUPPORTED)}")
        return v, c

    def fetch(k: tuple[str, str]) -> tuple[dict[str, Any], float]:
        value, age = cache.get(k, lambda: build(k[1]))
        if value is None:
            raise HTTPException(503, "no surface available yet")
        return value, age

    @app.get("/health")
    def health() -> dict[str, Any]:
        """Whether each surface is warm, and how old it is. Never fits."""
        surfaces = {}
        for v, c in sorted(SUPPORTED):
            entry = cache.peek((v, c))
            surfaces[f"{v}/{c}"] = {
                "warm": entry is not None,
                "age_s": round(cache.age_s((v, c)) or 0.0, 2) if entry else None,
                "stale": cache.is_stale((v, c)),
                "last_error": entry.error if entry else None,
            }
        return {"ok": True, "ttl_s": cache.ttl_s, "surfaces": surfaces}

    def identity_of(request: Request) -> sess.Identity:
        """The signed-in person, or a 401 that says which part failed.

        The token may arrive as the cookie the sign-in edge sets, or as a bearer
        header for anything that is not a browser. Same token either way.
        """
        token = request.cookies.get("tmo_session")
        if not token:
            auth = request.headers.get("Authorization", "")
            if auth.lower().startswith("bearer "):
                token = auth[7:].strip()
        try:
            return sess.verify(token, app.state.session_secret)
        except sess.SessionError as exc:
            raise HTTPException(401, str(exc)) from exc

    def subscribers() -> SubscriberStore:
        if app.state.store is None:
            raise HTTPException(503, "subscriber records are not available here")
        return app.state.store

    @app.get("/me")
    def me(request: Request) -> dict[str, Any]:
        """Who is signed in, and what they may use.

        Entitlement is answered here rather than at the sign-in edge, because
        this is where the subscriber record lives and where the rules about
        grace windows and refunds are already written.
        """
        identity = identity_of(request)
        ent = sess.entitlement_for(identity, subscribers())
        return {"email": identity.email, "name": identity.name,
                "session_expires_at": identity.expires_at, **ent.as_dict()}

    @app.post("/link")
    def link(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        """Bind this sign-in account to a subscription, using the key we sent.

        For the person who paid with one address and signs in with another. The
        key is the proof: only the subscriber ever received it.
        """
        identity = identity_of(request)
        store_ = subscribers()
        key = str(body.get("api_key", "")).strip()
        record = store_._by_api_key(key) if key else None
        if record is None:
            # Deliberately the same answer for an unknown key and an inactive
            # one: distinguishing them tells someone guessing which keys exist.
            raise HTTPException(403, "that key does not match an active subscription")
        if not store_.is_active_key(key):
            raise HTTPException(403, "that key does not match an active subscription")
        store_.link_identity(record["subscription_id"], identity.sub)
        ent = sess.entitlement_for(identity, store_)
        return {"linked": True, "email": identity.email, **ent.as_dict()}

    @app.get("/surface/{venue}/{currency}")
    def get_surface(venue: str, currency: str) -> dict[str, Any]:
        payload, age = fetch(key(venue, currency))
        return {**surface.summarise(payload), "age_s": round(age, 2),
                "stale": age >= cache.ttl_s}

    @app.get("/surface/{venue}/{currency}/{expiry}")
    def get_slice(venue: str, currency: str, expiry: str) -> dict[str, Any]:
        payload, age = fetch(key(venue, currency))
        for e in payload["expiries"]:
            if e["expiry"] == expiry:
                return {"venue": payload["venue"], "currency": payload["currency"],
                        "as_of": payload["as_of"], "age_s": round(age, 2),
                        "stale": age >= cache.ttl_s,
                        "quality": payload["quality"], "slice": e}
        raise HTTPException(404, f"no expiry {expiry}; have "
                                 f"{[e['expiry'] for e in payload['expiries']]}")

    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run(app, host=os.getenv("TMO_SERVICE_HOST", "127.0.0.1"),
                port=int(os.getenv("TMO_SERVICE_PORT", "8080")), log_level="info")


if __name__ == "__main__":
    main()
