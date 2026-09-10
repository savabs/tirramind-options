"""A small Paddle API client: only the calls this product actually makes.

No secret is stored here or logged anywhere. The key is read from the
environment by ``PaddleConfig`` and lives in the process, never on disk and
never in a log line. Errors carry the status and Paddle's own message, with the
Authorization header stripped from anything printed.

Why a client at all, when webhooks push everything we need: three reasons that
are each load-bearing. No subscription or transaction webhook carries an email
address, so ``get_customer`` is the only source. Products and prices have to
exist before a checkout can happen. And ``list_events`` is what lets a real
delivered payload be replayed through the handler, which is the difference
between testing our own fixtures and testing the wire.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterator

import requests

from .config import PaddleConfig

logger = logging.getLogger(__name__)


class PaddleAPIError(RuntimeError):
    """A non-2xx from Paddle, carrying its own explanation."""

    def __init__(self, status: int, body: Any, method: str, path: str) -> None:
        detail = body
        if isinstance(body, dict):
            err = body.get("error") or {}
            detail = f"{err.get('code', '?')}: {err.get('detail', body)}"
        super().__init__(f"{method} {path} -> {status}: {detail}")
        self.status = status
        self.body = body


class PaddleClient:
    """Thin, synchronous, and deliberately incomplete."""

    def __init__(self, config: PaddleConfig, *, session: requests.Session | None = None,
                 timeout: float = 20.0) -> None:
        if not config.api_key:
            raise ValueError("no Paddle API key configured")
        self._cfg = config
        self._timeout = timeout
        self._s = session or requests.Session()
        self._s.headers.update({
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "tirramind-options/0.1",
        })

    @property
    def mode(self) -> str:
        return self._cfg.mode

    def _call(self, method: str, path: str, *, params: dict | None = None,
              body: dict | None = None) -> dict:
        url = f"{self._cfg.api_base}{path}"
        r = self._s.request(method, url, params=params,
                            data=json.dumps(body) if body is not None else None,
                            timeout=self._timeout)
        try:
            payload = r.json()
        except ValueError:
            payload = {"raw": r.text[:500]}
        if r.status_code >= 400:
            raise PaddleAPIError(r.status_code, payload, method, path)
        return payload

    # ── reads ──────────────────────────────────────────────────────────────
    def whoami(self) -> dict:
        """Cheapest call that proves the key works and says which account it is.

        Paddle has no identity endpoint, so this asks for one event and reports
        what came back. A 403 here means the key is wrong or withdrawn.
        """
        data = self._call("GET", "/events", params={"per_page": 1})
        meta = data.get("meta", {})
        return {"mode": self.mode, "reachable": True,
                "request_id": meta.get("request_id"),
                "events_visible": len(data.get("data", []))}

    def get_customer(self, customer_id: str) -> dict:
        return self._call("GET", f"/customers/{customer_id}").get("data", {})

    def list_products(self, *, include_prices: bool = True) -> list[dict]:
        params = {"per_page": 200}
        if include_prices:
            params["include"] = "prices"
        return self._call("GET", "/products", params=params).get("data", [])

    def list_prices(self) -> list[dict]:
        return self._call("GET", "/prices", params={"per_page": 200}).get("data", [])

    def list_notification_settings(self) -> list[dict]:
        return self._call("GET", "/notification-settings").get("data", [])

    def iter_events(self, *, per_page: int = 50, max_pages: int = 20) -> Iterator[dict]:
        """Delivered events, newest page first. These are the real payloads."""
        after, pages = None, 0
        while pages < max_pages:
            params: dict[str, Any] = {"per_page": per_page}
            if after:
                params["after"] = after
            data = self._call("GET", "/events", params=params)
            rows = data.get("data", [])
            if not rows:
                return
            yield from rows
            after = rows[-1].get("event_id") or rows[-1].get("id")
            pages += 1
            if not data.get("meta", {}).get("pagination", {}).get("has_more"):
                return

    # ── writes ─────────────────────────────────────────────────────────────
    def create_product(self, *, name: str, description: str,
                       tax_category: str = "saas") -> dict:
        return self._call("POST", "/products", body={
            "name": name, "description": description, "tax_category": tax_category,
        }).get("data", {})

    def create_price(self, *, product_id: str, description: str, amount_minor: int,
                     currency_code: str, interval: str = "month",
                     frequency: int = 1) -> dict:
        return self._call("POST", "/prices", body={
            "product_id": product_id,
            "description": description,
            "unit_price": {"amount": str(amount_minor), "currency_code": currency_code},
            "billing_cycle": {"interval": interval, "frequency": frequency},
            "quantity": {"minimum": 1, "maximum": 1},
        }).get("data", {})


__all__ = ["PaddleClient", "PaddleAPIError"]
