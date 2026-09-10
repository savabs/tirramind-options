"""The webhook endpoint. One route, one job, one hard limit on request size.

BUG 4, as shipped in tirramind: the POST path read ``Content-Length`` bytes
into memory and only then verified the signature. ``Content-Length`` is
attacker-controlled and nothing had checked who the caller was yet, so any
unauthenticated request could ask the process to allocate as much memory as it
liked. On a single box running one process, that is the whole service.

The order here is the fix: parse the declared length defensively, refuse
anything over the cap before reading a byte, and only then read, verify and
apply. A real Paddle payload is a few kilobytes; the cap is 256 KiB, which is
two orders of magnitude of headroom and still nothing.

A refused request gets 413 and the connection closed, because the body we
declined to read would otherwise sit in the socket and desynchronise the next
request on a keep-alive connection.

The same class of problem, found while testing the fix: a caller that declares
a body and then sends less of it than promised parks a worker thread on a
blocking read forever. Enough of those and the endpoint is gone without a
single byte of payload. Every connection therefore carries a read timeout, and
a stalled body gets 408 rather than a thread.
"""

from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .config import PaddleConfig
from .handler import PaddleWebhookHandler

logger = logging.getLogger(__name__)

# Paddle payloads run to a few KB. This is headroom, not a guess at the size.
MAX_BODY_BYTES = 256 * 1024

# Long enough for a slow but real client, short enough that a stalled body
# cannot hold a worker thread. Paddle is neither slow nor far away.
DEFAULT_READ_TIMEOUT_S = 20.0


def safe_content_length(raw: str | None) -> int | None:
    """The declared body size, or None if the header is absent or nonsense.

    A malformed header must not raise out of a worker thread, and a negative
    or absurd value must not be trusted arithmetic.
    """
    if raw is None:
        return None
    try:
        n = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


class WebhookRequestHandler(BaseHTTPRequestHandler):
    """Serves POST /webhook and nothing else."""

    server_version = "tmo-webhook"
    handler_factory = None  # set by serve(); tests inject their own
    # socketserver applies this to the connection socket, so both the header
    # read and the body read below are bounded.
    timeout = DEFAULT_READ_TIMEOUT_S

    def _send(self, code: int, payload: dict[str, Any], *, close: bool = False) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's spelling
        path = self.path.split("?")[0]
        if path != "/webhook":
            self._send(404, {"ok": False, "error": "not found"})
            return

        declared = safe_content_length(self.headers.get("Content-Length"))
        if declared is None:
            self._send(411, {"ok": False, "error": "a valid Content-Length is required"},
                       close=True)
            return
        if declared > MAX_BODY_BYTES:
            # Refuse before reading. This is the whole point of the fix.
            logger.warning("[paddle] refused a webhook declaring %d bytes", declared)
            self._send(413, {"ok": False, "error": "request body too large"}, close=True)
            return

        try:
            body = self.rfile.read(declared) if declared else b""
        except OSError:
            # The body stalled. Do not wait on it; the thread is worth more.
            self._send(408, {"ok": False, "error": "timed out reading the request body"},
                       close=True)
            return
        # A short read means the caller lied about the length or went away.
        if len(body) != declared:
            self._send(400, {"ok": False, "error": "body shorter than Content-Length"},
                       close=True)
            return

        try:
            handler = self._build_handler()
            result = handler.handle(body=body,
                                    signature_header=self.headers.get("Paddle-Signature", ""))
        except Exception as exc:  # noqa: BLE001 - a bad signature or bad config
            logger.warning("[paddle] webhook rejected: %s: %s", type(exc).__name__, exc)
            self._send(400, {"ok": False, "error": str(exc)})
            return

        # The minted key never goes back to Paddle. Paddle has no use for it and
        # there is no reason to put a customer's credential on that wire.
        ack = {k: v for k, v in result.items() if k != "api_key"}
        self._send(200, {**ack, "ok": True})

    def _build_handler(self) -> PaddleWebhookHandler:
        if self.handler_factory is not None:
            return self.handler_factory()
        cfg = PaddleConfig.from_env()
        return PaddleWebhookHandler(secret=cfg.webhook_secret,
                                    tier_price_map=cfg.tier_price_map)

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info("[webhook] " + fmt, *args)


def serve(host: str = "127.0.0.1", port: int = 8787, *, handler_factory=None,
          read_timeout_s: float = DEFAULT_READ_TIMEOUT_S):
    """Start the endpoint. Returns the server so a caller can shut it down."""
    cls = type("BoundWebhookRequestHandler", (WebhookRequestHandler,),
               {"handler_factory": staticmethod(handler_factory) if handler_factory else None,
                "timeout": read_timeout_s})
    httpd = ThreadingHTTPServer((host, port), cls)
    logger.info("[webhook] listening on http://%s:%d/webhook", host, port)
    return httpd


__all__ = ["WebhookRequestHandler", "serve", "safe_content_length", "MAX_BODY_BYTES",
           "DEFAULT_READ_TIMEOUT_S"]
