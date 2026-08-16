import json

from symphony.mcp.audit import audit


def test_audit_redacts_secrets(tmp_path):
    p = tmp_path / "audit.jsonl"
    audit(p, {"tool": "symphony_create_request", "api_key": "sk-secret", "Authorization": "Bearer x"})
    rec = json.loads(p.read_text().strip())
    assert rec["api_key"] == "[REDACTED]"
    assert rec["Authorization"] == "[REDACTED]"
    assert rec["tool"] == "symphony_create_request"
    assert "timestamp" in rec


def test_audit_nested_redaction(tmp_path):
    p = tmp_path / "audit.jsonl"
    audit(p, {"nested": {"password": "pw", "ok": "v"}, "list": [{"token": "t"}]})
    rec = json.loads(p.read_text().strip())
    assert rec["nested"]["password"] == "[REDACTED]"
    assert rec["nested"]["ok"] == "v"
    assert rec["list"][0]["token"] == "[REDACTED]"


def test_audit_appends_multiple(tmp_path):
    p = tmp_path / "audit.jsonl"
    audit(p, {"n": 1})
    audit(p, {"n": 2})
    lines = p.read_text().strip().splitlines()
    assert len(lines) == 2
