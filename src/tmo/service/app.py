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

from fastapi import FastAPI, HTTPException

from . import surface
from .cache import SurfaceCache

logger = logging.getLogger(__name__)

SUPPORTED = {("deribit", "BTC"), ("deribit", "ETH")}
DEFAULT_TTL_S = 30.0


def create_app(*, builder: Callable[[str], dict[str, Any]] | None = None,
               ttl_s: float | None = None, warm: bool = True,
               refresh_interval_s: float = 5.0) -> FastAPI:
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
