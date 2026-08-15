import pytest

from symphony.mcp.errors import PermissionDenied, ProjectNotAllowed
from symphony.mcp.policy import Policy


def test_deny_by_default():
    p = Policy(frozenset())
    with pytest.raises(ProjectNotAllowed):
        p.assert_project_allowed("anything")


def test_allowlist():
    p = Policy(frozenset({"oh-my-symphony"}))
    p.assert_project_allowed("oh-my-symphony")
    with pytest.raises(ProjectNotAllowed):
        p.assert_project_allowed("other")


def test_permissions_v1():
    p = Policy(frozenset())
    p.assert_permission("read")
    p.assert_permission("create")
    for action in ("delete", "pause", "shell", "configure"):
        with pytest.raises(PermissionDenied):
            p.assert_permission(action)


def test_control_gate_default_denied():
    p = Policy(frozenset())
    with pytest.raises(PermissionDenied):
        p.assert_control_allowed()


def test_control_gate_enabled():
    p = Policy(frozenset(), allow_control=True)
    p.assert_control_allowed()
