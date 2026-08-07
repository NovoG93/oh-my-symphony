"""SPEC §5.5, §10.6, §11.4 — typed error classes."""

from __future__ import annotations


class SymphonyError(Exception):
    code: str = "symphony_error"

    def __init__(self, message: str = "", **context: object) -> None:
        super().__init__(message or self.code)
        self.message = message or self.code
        self.context = context

    def __str__(self) -> str:
        if not self.context:
            return f"{self.code}: {self.message}"
        ctx = " ".join(f"{k}={v!r}" for k, v in self.context.items())
        return f"{self.code}: {self.message} ({ctx})"


# §5.5
class MissingWorkflowFile(SymphonyError):
    code = "missing_workflow_file"


class WorkflowParseError(SymphonyError):
    code = "workflow_parse_error"


class WorkflowFrontMatterNotAMap(SymphonyError):
    code = "workflow_front_matter_not_a_map"


class TemplateParseError(SymphonyError):
    code = "template_parse_error"


class TemplateRenderError(SymphonyError):
    code = "template_render_error"


# §11.4
class UnsupportedTrackerKind(SymphonyError):
    code = "unsupported_tracker_kind"


class MissingTrackerApiKey(SymphonyError):
    code = "missing_tracker_api_key"


class MissingTrackerProjectSlug(SymphonyError):
    code = "missing_tracker_project_slug"


class LinearApiRequestError(SymphonyError):
    code = "linear_api_request"


class LinearApiStatusError(SymphonyError):
    code = "linear_api_status"


class LinearGraphQLErrors(SymphonyError):
    code = "linear_graphql_errors"


class LinearUnknownPayload(SymphonyError):
    code = "linear_unknown_payload"


class LinearMissingEndCursor(SymphonyError):
    code = "linear_missing_end_cursor"


class JiraApiRequestError(SymphonyError):
    code = "jira_api_request"


class JiraApiStatusError(SymphonyError):
    code = "jira_api_status"


class JiraUnknownPayload(SymphonyError):
    code = "jira_unknown_payload"


class JiraTransitionNotFound(SymphonyError):
    code = "jira_transition_not_found"


class MissingTrackerEmail(SymphonyError):
    code = "missing_tracker_email"


class MissingTrackerEndpoint(SymphonyError):
    code = "missing_tracker_endpoint"


# §10.6
class CodexNotFound(SymphonyError):
    code = "codex_not_found"


class InvalidWorkspaceCwd(SymphonyError):
    code = "invalid_workspace_cwd"


class ResponseTimeout(SymphonyError):
    code = "response_timeout"


class TurnTimeout(SymphonyError):
    code = "turn_timeout"


class PortExit(SymphonyError):
    code = "port_exit"


class ResponseError(SymphonyError):
    code = "response_error"


class TurnFailed(SymphonyError):
    code = "turn_failed"


class TurnCancelled(SymphonyError):
    code = "turn_cancelled"


class TurnInputRequired(SymphonyError):
    code = "turn_input_required"


# §6.3
class ConfigValidationError(SymphonyError):
    code = "config_validation_error"


# governed workflow engine (symphony.flow)
class WorkflowDefinitionInvalid(SymphonyError):
    """A workflow YAML file failed schema decoding or DAG compilation."""

    code = "workflow_invalid"


class WorkflowDefinitionNotFound(SymphonyError):
    code = "workflow_not_found"


class RunNotFound(SymphonyError):
    code = "run_not_found"


class IllegalRunTransition(SymphonyError):
    """A run status move the state machine does not allow (PRD §9.1)."""

    code = "illegal_run_transition"


class RunNotResumable(SymphonyError):
    """Resume was requested for a run that is terminal, active, or unverified."""

    code = "run_not_resumable"


class RunFenced(SymphonyError):
    """The issue already has a nonterminal governed run holding its fence."""

    code = "run_fenced"


class ApprovalNotFound(SymphonyError):
    code = "approval_not_found"


class ApprovalAlreadyResolved(SymphonyError):
    """A second, conflicting decision arrived for a gate already resolved."""

    code = "approval_already_resolved"


class ApprovalVersionConflict(SymphonyError):
    """The caller's `expected_version` no longer matches the stored record."""

    code = "approval_version_conflict"


class BackendCapabilityMissing(SymphonyError):
    code = "backend_capability_missing"


class ArtifactNotFound(SymphonyError):
    code = "artifact_not_found"


class WorkspaceIntegrityFailed(SymphonyError):
    """A required artifact is missing or its content no longer hashes equal."""

    code = "workspace_integrity_failed"


class UnsafePath(SymphonyError):
    """A path escaped its approved root via `..`, an absolute path, or a symlink."""

    code = "unsafe_path"


# operator chat (symphony.chat)
class ChatBusyError(SymphonyError):
    code = "chat_busy"


class ChatSessionExistsError(SymphonyError):
    code = "chat_session_exists"


class ChatNoSessionError(SymphonyError):
    code = "chat_no_session"
