from __future__ import annotations

import json

import pytest

from symphony.orchestrator.diagnostics import (
    MAX_DIAGNOSTIC_STRING_BYTES,
    event_payload_json,
    redact_text,
)


@pytest.mark.parametrize(
    "raw, secret",
    [
        (
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
            "abcdefghijklmnopqrstuvwxyz",
        ),
        ("https://user:supersecret@example.com/path", "supersecret"),
        ('password="supersecret"', "supersecret"),
        ('password="two word secret"', "two word secret"),
        ('{"password":"json-secret"}', "json-secret"),
        ("{'token': 'python-secret'}", "python-secret"),
        ("COOKIE=sessionid=cookie-secret", "cookie-secret"),
        ('{"cookie":"sessionid=json-cookie-secret"}', "json-cookie-secret"),
        ("{'cookie': 'sessionid=python-cookie-secret'}", "python-cookie-secret"),
        ("MY_CREDENTIAL=hunter2", "hunter2"),
        ("API_KEY=top-secret", "top-secret"),
        ("eyJabcdefghijk.abcdefghijk.abcdefghijk", "eyJabcdefghijk"),
        (
            "-----BEGIN PRIVATE KEY-----\nsecret-material\n-----END PRIVATE KEY-----",
            "secret-material",
        ),
        (
            "-----BEGIN ENCRYPTED PRIVATE KEY-----\nencrypted-material\n"
            "-----END ENCRYPTED PRIVATE KEY-----",
            "encrypted-material",
        ),
    ],
)
def test_redact_text_removes_common_embedded_secret_shapes(
    raw: str, secret: str
) -> None:
    redacted = redact_text(raw)
    assert secret not in redacted
    assert "[REDACTED]" in redacted


def test_redact_text_enforces_utf8_byte_ceiling() -> None:
    redacted = redact_text("가" * 2_000)
    assert len(redacted.encode("utf-8")) <= MAX_DIAGNOSTIC_STRING_BYTES
    assert redacted.endswith("…[truncated]")


def test_event_payload_is_allowlisted_and_bounded_before_persistence() -> None:
    encoded = event_payload_json(
        "turn_failed",
        {
            "turn": 2,
            "reason": "token=secret-value failed",
            "stderr_lines": ["password=hidden", "safe context"],
            "raw_tool_transcript": "must-not-survive",
        },
    )
    payload = json.loads(encoded)
    assert payload["turn"] == 2
    assert "secret-value" not in encoded
    assert "hidden" not in encoded
    assert "raw_tool_transcript" not in payload


def test_unknown_event_type_fails_closed() -> None:
    with pytest.raises(ValueError, match="unsupported diagnostic event type"):
        event_payload_json("raw_backend_frame", {"payload": "secret"})


def test_phase_transition_event_is_structured_without_raw_backend_data() -> None:
    payload = json.loads(
        event_payload_json(
            "phase_transition",
            {
                "from_state": "Implementation",
                "to_state": "Review",
                "turn": 3,
                "attempt": 1,
                "is_rewind": False,
                "prompt": "must-not-survive",
            },
        )
    )
    assert payload == {
        "attempt": 1,
        "from_state": "Implementation",
        "is_rewind": False,
        "to_state": "Review",
        "turn": 3,
    }


def test_success_events_drop_transcript_and_session_identifiers() -> None:
    completed = json.loads(
        event_payload_json(
            "turn_completed",
            {
                "turn": 1,
                "message": "assistant repeated a private prompt",
                "total_tokens": 10,
            },
        )
    )
    session = json.loads(
        event_payload_json("session_started", {"session_id": "sensitive-session"})
    )
    assert completed == {"total_tokens": 10, "turn": 1}
    assert session == {}
