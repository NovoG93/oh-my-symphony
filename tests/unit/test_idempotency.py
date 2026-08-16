from symphony.mcp.idempotency import IdempotencyStore


def test_put_get_and_no_overwrite(tmp_path):
    s = IdempotencyStore(tmp_path / "idem.sqlite3")
    assert s.get("k1") is None
    s.put("k1", "TASK-1")
    assert s.get("k1") == "TASK-1"
    # INSERT OR IGNORE: a second put with a different id does not overwrite
    s.put("k1", "TASK-2")
    assert s.get("k1") == "TASK-1"
    s.close()


def test_distinct_keys(tmp_path):
    s = IdempotencyStore(tmp_path / "idem.sqlite3")
    s.put("a", "TASK-A")
    s.put("b", "TASK-B")
    assert s.get("a") == "TASK-A"
    assert s.get("b") == "TASK-B"
    s.close()
