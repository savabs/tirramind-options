"""Append-only JSONL ledgers keyed by a unique field.

Ported verbatim in spirit from ``tirramind-universe/src/tirramind/ledger.py``.
A row is never edited. Re-appending a key that already exists is a no-op, so a
daily job that runs twice, or overlaps yesterday's window, costs nothing. A
correction is a new row carrying ``supersedes=<old key>``.

That no-op property is the whole safety story for this repo: the ledger is a
public track record, so the only acceptable failure mode for a re-run is
"wrote nothing", never "wrote it differently".
"""

from __future__ import annotations

import json
import os

DEFAULT_ROOT = os.path.join(os.getcwd(), "data", "ledgers")


def _path(name: str, root: str) -> str:
    return os.path.join(root, f"{name}.jsonl")


def read(name: str, *, root: str = DEFAULT_ROOT) -> list[dict]:
    p = _path(name, root)
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def keys(name: str, key: str, *, root: str = DEFAULT_ROOT) -> set:
    return {r[key] for r in read(name, root=root) if key in r}


def append(name: str, rows: list[dict], key: str, *, root: str = DEFAULT_ROOT) -> int:
    """Append rows whose ``key`` is not already present. Returns count added."""
    os.makedirs(root, exist_ok=True)
    seen = keys(name, key, root=root)
    added, out = 0, []
    for r in rows:
        k = r[key]
        if k in seen:
            continue
        out.append(json.dumps(r, sort_keys=True, default=str))
        seen.add(k)
        added += 1
    if not out:
        return 0
    # One open, one write, one fsync: a job killed mid-append must not leave a
    # half-written JSON line in a file that is only ever appended to.
    with open(_path(name, root), "a", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return added
