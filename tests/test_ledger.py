from tmo import ledger


def test_append_is_idempotent(tmp_path):
    root = str(tmp_path)
    rows = [{"k": "a", "v": 1}, {"k": "b", "v": 2}]
    assert ledger.append("t", rows, "k", root=root) == 2
    assert ledger.append("t", rows, "k", root=root) == 0
    assert len(ledger.read("t", root=root)) == 2


def test_a_second_row_for_a_known_key_is_dropped_not_merged(tmp_path):
    root = str(tmp_path)
    ledger.append("t", [{"k": "a", "v": 1}], "k", root=root)
    ledger.append("t", [{"k": "a", "v": 99}], "k", root=root)
    assert [r["v"] for r in ledger.read("t", root=root)] == [1]


def test_missing_ledger_reads_empty(tmp_path):
    assert ledger.read("nope", root=str(tmp_path)) == []
