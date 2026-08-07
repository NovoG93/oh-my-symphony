"""SPEC §6.3 — dispatch preflight: refuse to start workers on broken config.

Builder defaults make a ServiceConfig *constructible* even when many
fields are blank; `validate_for_dispatch` is the second gate that runs
just before workers spin up and surfaces "this tracker can't actually
talk to the upstream" errors as typed exceptions the orchestrator can
log and surface to the operator.
"""

from __future__ import annotations

from ..errors import (
    ConfigValidationError,
    MissingTrackerApiKey,
    MissingTrackerEmail,
    MissingTrackerEndpoint,
    MissingTrackerProjectSlug,
    UnsupportedTrackerKind,
)
from .config import ServiceConfig
from .constants import SUPPORTED_TRACKER_KINDS


def stage_turn_budget_error(config: ServiceConfig) -> str | None:
    active_states = [state for state in config.tracker.active_states if state]
    required_turns = len(active_states)
    if required_turns <= 1:
        return None
    if config.agent.max_turns >= required_turns:
        return None
    states = ", ".join(active_states)
    return (
        f"agent.max_turns={config.agent.max_turns} cannot cover "
        f"{required_turns} active states ({states}). Set agent.max_turns >= "
        f"{required_turns}, or reduce active_states for a single-stage harness."
    )


def validate_for_dispatch(config: ServiceConfig) -> None:
    if not config.tracker.kind:
        raise UnsupportedTrackerKind("tracker.kind is required")
    if config.tracker.kind not in SUPPORTED_TRACKER_KINDS:
        raise UnsupportedTrackerKind(
            "tracker kind not supported", kind=config.tracker.kind
        )
    if config.tracker.kind == "linear":
        if not config.tracker.api_key:
            raise MissingTrackerApiKey(
                "tracker.api_key missing or empty after $VAR resolution"
            )
        if not config.tracker.project_slug:
            raise MissingTrackerProjectSlug(
                "tracker.project_slug required for linear tracker"
            )
    if config.tracker.kind == "file":
        if config.tracker.board_root is None:
            raise ConfigValidationError(
                "tracker.board_root is required when tracker.kind=file"
            )
    if config.tracker.kind == "jira":
        if not config.tracker.endpoint:
            raise MissingTrackerEndpoint(
                "tracker.endpoint required for jira tracker "
                "(e.g., https://your-domain.atlassian.net)"
            )
        if not config.tracker.email:
            raise MissingTrackerEmail(
                "tracker.email missing or empty after $VAR resolution"
            )
        if not config.tracker.api_key:
            raise MissingTrackerApiKey(
                "tracker.api_key missing or empty after $VAR resolution"
            )
        if not config.tracker.project_slug:
            raise MissingTrackerProjectSlug(
                "tracker.project_slug required for jira tracker (the project key, e.g., PROJ)"
            )
    kind = config.agent.kind
    if kind == "codex":
        if not config.codex.command.strip():
            raise ConfigValidationError("codex.command must be non-empty")
    elif kind == "claude":
        if not config.claude.command.strip():
            raise ConfigValidationError("claude.command must be non-empty")
    elif kind == "gemini":
        if not config.gemini.command.strip():
            raise ConfigValidationError("gemini.command must be non-empty")
    elif kind == "opencode":
        if not config.opencode.command.strip():
            raise ConfigValidationError("opencode.command must be non-empty")
    elif kind == "pi":
        if not config.pi.command.strip():
            raise ConfigValidationError("pi.command must be non-empty")
    budget_error = stage_turn_budget_error(config)
    if budget_error is not None:
        raise ConfigValidationError(budget_error)
    validate_workflow_engine(config)


def validate_workflow_engine(config: ServiceConfig) -> None:
    """Structural checks for governed mode, cheap enough to run per tick.

    Deliberately does *not* compile the default workflow: compilation reads
    prompt files off disk, and this runs on every poll. Compilation happens
    at dispatch, where the orchestrator's loader caches by file mtime.
    """
    engine = config.workflow_engine
    if not engine.enabled:
        return
    if engine.directory is None or not engine.directory.is_dir():
        raise ConfigValidationError(
            "workflow_engine.directory does not exist; create it and commit at "
            "least one workflow file before enabling governed mode",
            directory=str(engine.directory),
        )
    if not any(
        (engine.directory / f"{engine.default}{suffix}").is_file()
        for suffix in (".yaml", ".yml")
    ):
        raise ConfigValidationError(
            f"workflow_engine.default names {engine.default!r}, but no such "
            f"file exists in {engine.directory}",
            workflow=engine.default,
        )
    known_states = {
        state.strip().lower()
        for state in (*config.tracker.active_states, *config.tracker.terminal_states)
        if state
    }
    for condition, target in engine.ticket_state_mapping.items():
        if target.strip().lower() not in known_states:
            raise ConfigValidationError(
                f"workflow_engine.ticket_state_mapping.{condition} targets "
                f"{target!r}, which is not one of this tracker's states",
                state=target,
            )
