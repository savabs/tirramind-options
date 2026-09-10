"""Append-only, content-addressed snapshots.

Taken unchanged from tirramind-universe/src/tirramind/store.py, itself ported
from queue_attrition/src/snapshot.py. A snapshot is a canonical CSV
of a DataFrame, named by the day and a 16-hex content digest. Writing the
same content twice produces one file and two index rows (``new`` then
``unchanged``), so a source that goes quiet is visible as a gap in the
index rather than as silence. Nothing is ever edited in place.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

import pandas as pd

DEFAULT_ROOT = os.path.join(os.getcwd(), "data", "snapshots")


def digest(df: pd.DataFrame) -> str:
    """Hash the content, not the file: identical data must hash identically
    regardless of row order or how pandas felt about column dtypes today."""
    canonical = df.reindex(sorted(df.columns), axis=1).astype(str)
    canonical = canonical.sort_values(list(canonical.columns), kind="stable")
    return hashlib.sha256(canonical.to_csv(index=False).encode("utf-8")).hexdigest()[:16]


def _index_path(root: str) -> str:
    return os.path.join(root, "index.jsonl")


def read_index(root: str = DEFAULT_ROOT) -> list[dict]:
    path = _index_path(root)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _record(root: str, row: dict) -> None:
    os.makedirs(root, exist_ok=True)
    with open(_index_path(root), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def _already_have(root: str, name: str) -> set[str]:
    return {r["digest"] for r in read_index(root) if r["name"] == name and r.get("digest")}


def snapshot(
    name: str,
    df: pd.DataFrame,
    *,
    root: str = DEFAULT_ROOT,
    captured_at: datetime | None = None,
    source: str = "live",
) -> tuple[str | None, str, str]:
    """Store ``df`` under ``name``. Returns ``(path, digest, status)`` where
    status is ``new`` or ``unchanged``. ``path`` is None when unchanged.

    ``captured_at`` defaults to now (UTC); pass a historical timestamp when
    backfilling from an archive such as Wayback, together with ``source``.
    """
    now = captured_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("captured_at must be timezone-aware")
    day = now.strftime("%Y-%m-%d")
    dg = digest(df)
    base = {
        "captured_at": now.isoformat(timespec="seconds"),
        "name": name,
        "source": source,
        "rows": int(len(df)),
        "digest": dg,
        "error": None,
    }
    if dg in _already_have(root, name):
        _record(root, {**base, "status": "unchanged", "path": None})
        return None, dg, "unchanged"

    rel = os.path.join(name, f"{day}_{dg}.csv.gz")
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    canonical = df.reindex(sorted(df.columns), axis=1)
    canonical.to_csv(path, index=False, compression="gzip")
    _record(root, {**base, "status": "new", "path": rel})
    return path, dg, "new"


def record_failure(name: str, error: str, *, root: str = DEFAULT_ROOT) -> None:
    _record(root, {
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "name": name, "source": "live", "rows": 0, "digest": None,
        "status": "fail", "path": None, "error": error[:200],
    })


def load_snapshot(root: str, rel_path: str) -> pd.DataFrame:
    return pd.read_csv(os.path.join(root, rel_path), dtype=str, keep_default_na=False)


def snapshots_for(name: str, *, root: str = DEFAULT_ROOT) -> list[dict]:
    """All ``new`` index rows for ``name``, oldest first."""
    rows = [r for r in read_index(root) if r["name"] == name and r["status"] == "new"]
    return sorted(rows, key=lambda r: r["captured_at"])
