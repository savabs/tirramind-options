"""A time-boxed cache that never lets two threads compute the same thing.

Fitting one chain costs about eight seconds of CPU: a second of eSSVI, the
rest in per-slice SVI refinement and the arbitrage checks. That number is the
whole design. A service that fits on demand answers its first request in eight
seconds and, under any concurrency at all, fits the same chain once per
request until the box falls over.

So: entries have a lifetime, a refresh runs in the background ahead of
expiry, and if a value is genuinely missing exactly one caller computes it
while the rest wait on the same result. A stale value is served in preference
to making a customer wait, and how stale it is goes in the response.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Hashable

logger = logging.getLogger(__name__)


@dataclass
class Entry:
    value: Any
    computed_at: float
    error: str | None = None


@dataclass
class SurfaceCache:
    """Single-flight, TTL, optional background refresh."""

    ttl_s: float = 30.0
    _entries: dict[Hashable, Entry] = field(default_factory=dict)
    _locks: dict[Hashable, threading.Lock] = field(default_factory=dict)
    _guard: threading.Lock = field(default_factory=threading.Lock)
    _stop: threading.Event = field(default_factory=threading.Event)
    _refresher: threading.Thread | None = None

    def _lock_for(self, key: Hashable) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    def peek(self, key: Hashable) -> Entry | None:
        return self._entries.get(key)

    def age_s(self, key: Hashable, *, now: float | None = None) -> float | None:
        e = self._entries.get(key)
        return None if e is None else (time.time() if now is None else now) - e.computed_at

    def get(self, key: Hashable, compute: Callable[[], Any], *,
            now: float | None = None) -> tuple[Any, float]:
        """Return ``(value, age_seconds)``, computing only if there is nothing.

        A present but stale value is returned as it is. Refreshing it is the
        background loop's job, so a request never pays for a fit that some
        other request could have been paying for already.
        """
        t = time.time() if now is None else now
        entry = self._entries.get(key)
        if entry is not None:
            return entry.value, t - entry.computed_at
        lock = self._lock_for(key)
        with lock:
            # Another thread may have filled it while we waited for the lock.
            entry = self._entries.get(key)
            if entry is not None:
                return entry.value, time.time() - entry.computed_at
            value = compute()
            self._entries[key] = Entry(value=value, computed_at=time.time())
            return value, 0.0

    def refresh(self, key: Hashable, compute: Callable[[], Any]) -> bool:
        """Recompute now. Returns False and keeps the old value on failure."""
        lock = self._lock_for(key)
        if not lock.acquire(blocking=False):
            return False           # someone is already on it
        try:
            value = compute()
            self._entries[key] = Entry(value=value, computed_at=time.time())
            return True
        except Exception as exc:  # noqa: BLE001 - a failed refresh must not kill the loop
            logger.warning("[cache] refresh of %s failed: %s: %s", key, type(exc).__name__, exc)
            existing = self._entries.get(key)
            if existing is not None:
                existing.error = f"{type(exc).__name__}: {exc}"
            return False
        finally:
            lock.release()

    def is_stale(self, key: Hashable, *, now: float | None = None) -> bool:
        age = self.age_s(key, now=now)
        return age is None or age >= self.ttl_s

    # ── background refresh ─────────────────────────────────────────────────
    def start_refreshing(self, targets: dict[Hashable, Callable[[], Any]], *,
                         interval_s: float = 5.0) -> None:
        """Keep ``targets`` warm. Idempotent; safe to call once at startup."""
        if self._refresher is not None:
            return
        self._stop.clear()

        def loop() -> None:
            while not self._stop.wait(0):
                for key, compute in targets.items():
                    if self._stop.is_set():
                        return
                    if self.is_stale(key):
                        self.refresh(key, compute)
                if self._stop.wait(interval_s):
                    return

        self._refresher = threading.Thread(target=loop, name="surface-refresh", daemon=True)
        self._refresher.start()

    def stop(self) -> None:
        self._stop.set()
        if self._refresher is not None:
            self._refresher.join(timeout=5)
            self._refresher = None


__all__ = ["SurfaceCache", "Entry"]
