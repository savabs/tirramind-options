"""Environment-driven Paddle settings. No secret is ever hard-coded.

    TMO_PADDLE_MODE             sandbox | live   (default sandbox)
    TMO_PADDLE_API_KEY          server-side API key, per mode
    TMO_PADDLE_CLIENT_TOKEN     client-side token, per mode
    TMO_PADDLE_WEBHOOK_SECRET   endpoint_secret_key of the notification destination
    TMO_PADDLE_PRICE_ID         the checkout price (pri_...)
    TMO_TIER_PRICE_MAP          pri_xxx:terminal,pri_yyy:terminal_annual

Switching sandbox to live is a config change, never a code change.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


class PaddleConfigError(ValueError):
    """Raised when Paddle config is missing or invalid."""


@dataclass(frozen=True)
class PaddleConfig:
    mode: str
    api_key: str
    client_token: str
    webhook_secret: str
    price_id: str
    tier_price_map: str

    @property
    def api_base(self) -> str:
        return "https://api.paddle.com" if self.mode == "live" else "https://sandbox-api.paddle.com"

    @property
    def checkout_base(self) -> str:
        return "https://checkout.paddle.com"

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    @classmethod
    def from_env(cls, env: dict | None = None) -> PaddleConfig:
        e = os.environ if env is None else env
        mode = e.get("TMO_PADDLE_MODE", "sandbox").strip().lower()
        if mode not in ("sandbox", "live"):
            raise PaddleConfigError(f"TMO_PADDLE_MODE must be 'sandbox' or 'live', got {mode!r}")
        webhook_secret = e.get("TMO_PADDLE_WEBHOOK_SECRET", "").strip()
        tier_price_map = e.get("TMO_TIER_PRICE_MAP", "").strip()
        # Live without a secret would silently skip signature verification,
        # which is the difference between a paywall and a suggestion.
        if mode == "live" and not webhook_secret:
            raise PaddleConfigError("TMO_PADDLE_WEBHOOK_SECRET is required in live mode")
        # Live without a price map would, given the tier rules, refuse every
        # activation. Fail at startup rather than at the first customer.
        if mode == "live" and not tier_price_map:
            raise PaddleConfigError("TMO_TIER_PRICE_MAP is required in live mode: "
                                    "an unmapped price cannot provision access")
        return cls(
            mode=mode,
            api_key=e.get("TMO_PADDLE_API_KEY", "").strip(),
            client_token=e.get("TMO_PADDLE_CLIENT_TOKEN", "").strip(),
            webhook_secret=webhook_secret,
            price_id=e.get("TMO_PADDLE_PRICE_ID", "").strip(),
            tier_price_map=tier_price_map,
        )

    def to_public_dict(self) -> dict:
        """The non-secret subset the checkout page may see."""
        return {"mode": self.mode, "client_token": self.client_token,
                "price_id": self.price_id, "checkout_base": self.checkout_base}


__all__ = ["PaddleConfig", "PaddleConfigError"]
