"""Pre-registered strategy specifications, hash-committed.

A spec is a JSON file in ``ledger/specs/``. It states, before any position is
opened, exactly what the strategy will do: universe, entry, exit, sizing,
hedging, costs. It is hashed canonically and the hash is appended to the
``registry`` ledger the first time it is seen.

The point of the hash is that it cannot be edited afterwards without the edit
being visible. Editing a file in place and re-running does not overwrite the
registry row -- it produces a *second* row with a different hash, and
``verify_immutable`` refuses the run unless the new file declares a bumped
``version`` and names what it ``supersedes``. A track record whose rules can be
silently rewritten is worth nothing, so this check is a hard failure, not a
warning.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

from . import ledger

SPEC_DIR = os.path.join(os.getcwd(), "ledger", "specs")

# Keys carrying registration bookkeeping rather than trading rules. They are
# excluded from the hash so that re-registering the identical rules under a new
# comment does not look like a rule change.
_NON_RULE_KEYS = {"registered_at", "notes"}

REQUIRED = ("strategy_id", "version", "venue", "universe", "entry", "exit",
            "sizing", "hedging", "costs", "marking", "supersedes")


def canonical(spec: dict) -> str:
    # Keys beginning with an underscore are loader bookkeeping (the source
    # filename); they are not rules and must not move the hash.
    rules = {k: v for k, v in spec.items()
             if k not in _NON_RULE_KEYS and not k.startswith("_")}
    return json.dumps(rules, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def spec_hash(spec: dict) -> str:
    return hashlib.sha256(canonical(spec).encode("utf-8")).hexdigest()[:16]


def load_all(spec_dir: str = SPEC_DIR) -> list[dict]:
    out = []
    for fn in sorted(os.listdir(spec_dir)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(spec_dir, fn), encoding="utf-8") as fh:
            spec = json.load(fh)
        missing = [k for k in REQUIRED if k not in spec]
        if missing:
            raise ValueError(f"{fn}: spec is missing required keys {missing}")
        spec["_file"] = fn
        out.append(spec)
    return out


def verify_immutable(specs: list[dict], *, root: str = ledger.DEFAULT_ROOT) -> None:
    """Refuse a run whose spec files contradict what was already registered.

    Rules already published may be retired but never rewritten: a changed file
    must carry a new ``version`` and point ``supersedes`` at the hash it
    replaces.
    """
    registered = ledger.read("registry", root=root)
    by_id_version = {(r["strategy_id"], r["version"]): r for r in registered}
    known_hashes = {r["spec_hash"] for r in registered}
    problems = []
    for spec in specs:
        h = spec_hash(spec)
        prior = by_id_version.get((spec["strategy_id"], spec["version"]))
        if prior and prior["spec_hash"] != h:
            problems.append(
                f"{spec['_file']}: {spec['strategy_id']} v{spec['version']} was registered "
                f"as {prior['spec_hash']} and now hashes to {h}. Published rules are "
                f"immutable -- bump 'version' and set 'supersedes' to {prior['spec_hash']}.")
            continue
        sup = spec.get("supersedes")
        if sup and sup not in known_hashes:
            problems.append(f"{spec['_file']}: supersedes {sup}, which was never registered.")
    if problems:
        raise ValueError("spec immutability check failed:\n  " + "\n  ".join(problems))


def register(specs: list[dict], *, root: str = ledger.DEFAULT_ROOT,
             now: datetime | None = None) -> int:
    """Append a registry row for every spec not already registered.

    Idempotent: the second run of the day adds nothing.
    """
    verify_immutable(specs, root=root)
    ts = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    rows = [{
        "spec_hash": spec_hash(spec),
        "strategy_id": spec["strategy_id"],
        "version": spec["version"],
        "registered_at": ts,
        "supersedes": spec.get("supersedes"),
        "file": spec["_file"],
        "canonical": canonical(spec),
    } for spec in specs]
    return ledger.append("registry", rows, "spec_hash", root=root)


def active(specs: list[dict]) -> list[dict]:
    """Specs that no other spec supersedes."""
    retired = {s["supersedes"] for s in specs if s.get("supersedes")}
    return [s for s in specs if spec_hash(s) not in retired]
