"""Shared managed-service identity protocol values."""

from __future__ import annotations

SERVICE_INSTANCE_ENV = "SYMPHONY_SERVICE_INSTANCE_ID"
MAX_SERVICE_INSTANCE_ID_LENGTH = 128


def normalize_service_instance_id(value: object) -> str | None:
    """Return a bounded non-blank instance ID or ``None``."""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_SERVICE_INSTANCE_ID_LENGTH
        or value.strip() != value
    ):
        return None
    return value
