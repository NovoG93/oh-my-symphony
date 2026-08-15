"""Policy / RBAC for the MCP gateway (v1: READ broad, CREATE restricted)."""

from __future__ import annotations

from .errors import PermissionDenied, ProjectNotAllowed


class Policy:
    def __init__(
        self,
        allowed_projects: frozenset[str] | None = None,
        allow_control: bool = False,
    ) -> None:
        self._allowed = allowed_projects or frozenset()
        self._allow_control = allow_control

    def assert_project_allowed(self, project_id: str) -> None:
        """Deny-by-default: an empty allowlist permits nothing."""
        if not self._allowed or project_id not in self._allowed:
            raise ProjectNotAllowed(f"project {project_id!r} is not in the allowlist")

    def assert_control_allowed(self) -> None:
        """Control tools (cancel/update/recover/skip) require an explicit opt-in."""
        if not self._allow_control:
            raise PermissionDenied(
                "control actions are disabled; set SYMPHONY_MCP_ALLOW_CONTROL=1 to enable"
            )

    def assert_permission(self, action: str) -> None:
        """v1 permits only read and create; control/delete/config/shell are denied."""
        if action not in ("read", "create"):
            raise PermissionDenied(f"action {action!r} is not permitted in v1")
