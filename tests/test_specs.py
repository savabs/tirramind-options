"""The property the whole track record rests on: published rules cannot change."""

import json
import os

import pytest

from tmo import ledger, specs

SPEC = {
    "strategy_id": "toy", "version": 1, "supersedes": None, "venue": "deribit",
    "universe": {"currency": "BTC"}, "entry": {"when": "always"},
    "exit": {"close_when_dte_below": 2}, "sizing": {"contracts_per_leg": 1},
    "hedging": {"instrument": "none"}, "costs": {"fee": "0.03%"},
    "marking": {"price": "venue mid"},
}


def _write(d, spec, name="toy.json"):
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name), "w") as fh:
        json.dump(spec, fh)
    return d


def test_hash_ignores_commentary_but_not_rules():
    a = specs.spec_hash(SPEC)
    assert specs.spec_hash({**SPEC, "notes": "a comment"}) == a
    assert specs.spec_hash({**SPEC, "sizing": {"contracts_per_leg": 2}}) != a


def test_hash_is_stable_under_key_order():
    assert specs.spec_hash(SPEC) == specs.spec_hash(dict(reversed(list(SPEC.items()))))


def test_editing_a_registered_spec_in_place_is_refused(tmp_path):
    d, root = str(tmp_path / "specs"), str(tmp_path / "ledgers")
    _write(d, SPEC)
    specs.register(specs.load_all(d), root=root)
    _write(d, {**SPEC, "sizing": {"contracts_per_leg": 5}})
    with pytest.raises(ValueError, match="immutab"):
        specs.register(specs.load_all(d), root=root)


def test_a_bumped_version_that_names_what_it_replaces_is_accepted(tmp_path):
    d, root = str(tmp_path / "specs"), str(tmp_path / "ledgers")
    _write(d, SPEC)
    specs.register(specs.load_all(d), root=root)
    old = specs.spec_hash(SPEC)
    _write(d, {**SPEC, "version": 2, "supersedes": old, "sizing": {"contracts_per_leg": 5}})
    assert specs.register(specs.load_all(d), root=root) == 1
    assert len(ledger.read("registry", root=root)) == 2


def test_superseding_a_hash_that_was_never_registered_is_refused(tmp_path):
    d, root = str(tmp_path / "specs"), str(tmp_path / "ledgers")
    _write(d, {**SPEC, "version": 2, "supersedes": "deadbeefdeadbeef"})
    with pytest.raises(ValueError, match="never registered"):
        specs.register(specs.load_all(d), root=root)


def test_registration_is_idempotent(tmp_path):
    d, root = str(tmp_path / "specs"), str(tmp_path / "ledgers")
    _write(d, SPEC)
    assert specs.register(specs.load_all(d), root=root) == 1
    assert specs.register(specs.load_all(d), root=root) == 0


def test_a_spec_missing_a_required_section_is_refused(tmp_path):
    d = str(tmp_path / "specs")
    _write(d, {k: v for k, v in SPEC.items() if k != "exit"})
    with pytest.raises(ValueError, match="missing required keys"):
        specs.load_all(d)


def test_superseded_specs_drop_out_of_active(tmp_path):
    d = str(tmp_path / "specs")
    _write(d, SPEC)
    _write(d, {**SPEC, "version": 2, "supersedes": specs.spec_hash(SPEC)}, "toy_v2.json")
    live = specs.active(specs.load_all(d))
    assert [s["version"] for s in live] == [2]


def test_the_shipped_specs_are_well_formed():
    live = specs.load_all()
    assert {s["strategy_id"] for s in live} == {"btc-weekly-vrp", "btc-hold-context"}
    assert len({specs.spec_hash(s) for s in live}) == len(live)
