"""Central named agent profile resolution and overlay logic (PLAN §7).

Resolves an ``AgentSelection`` into a concrete ``ResolvedAgentConfig`` by
overlaying non-null profile fields onto the global backend configuration
using immutable dataclass replacement.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from ..errors import ConfigValidationError
from .config import (
    AgentConfig,
    AgentProfileConfig,
    AgentSelection,
    AgyConfig,
    ClaudeConfig,
    CodexConfig,
    CopilotConfig,
    GeminiConfig,
    KiroConfig,
    OpenCodeConfig,
    PiConfig,
    PrimeAgentConfig,
    ServiceConfig,
    _default_copilot_config,
)
from .constants import PROFILE_FIELDS_BY_KIND, SUPPORTED_AGENT_KINDS


@dataclass(frozen=True)
class ResolvedAgentConfig:
    """Fully resolved concrete backend configuration for a worker execution."""

    kind: str
    profile_name: str | None = None

    codex: CodexConfig | None = None
    claude: ClaudeConfig | None = None
    gemini: GeminiConfig | None = None
    agy: AgyConfig | None = None
    kiro: KiroConfig | None = None
    opencode: OpenCodeConfig | None = None
    pi: PiConfig | None = None
    prime_agent: PrimeAgentConfig | None = None
    copilot: CopilotConfig | None = None

    @property
    def active_config(self) -> Any:
        """Return the concrete configuration dataclass for the active backend kind."""
        return getattr(self, self.kind.replace("-", "_"), None)


def _get_backend_config(cfg: ServiceConfig, kind: str) -> Any:
    if kind == "codex":
        return cfg.codex
    if kind == "claude":
        return cfg.claude
    if kind == "gemini":
        return cfg.gemini
    if kind == "agy":
        return cfg.agy
    if kind == "kiro":
        return cfg.kiro
    if kind == "opencode":
        return cfg.opencode
    if kind == "pi":
        return cfg.pi
    if kind == "prime-agent":
        return cfg.prime_agent
    if kind == "copilot":
        return cfg.copilot or _default_copilot_config()
    raise ConfigValidationError(
        f"unsupported backend kind {kind!r}; supported: {sorted(SUPPORTED_AGENT_KINDS)}",
        kind=kind,
    )


def _build_resolved(kind: str, profile_name: str | None, concrete: Any) -> ResolvedAgentConfig:
    attr = kind.replace("-", "_")
    kwargs = {attr: concrete}
    return ResolvedAgentConfig(kind=kind, profile_name=profile_name, **kwargs)


def resolve_agent_config(
    cfg: ServiceConfig,
    selection: AgentSelection,
) -> ResolvedAgentConfig:
    """Overlay non-null profile fields on the global backend config.

    Uses immutable dataclass replacement (``replace``) so inherited values
    (e.g. global command or un-overridden timeouts) remain intact.
    """
    kind = selection.kind
    profile_name = selection.profile
    base = _get_backend_config(cfg, kind)

    if profile_name is None:
        return _build_resolved(kind, None, base)

    profile_cfg = cfg.agent_profiles.get(profile_name)
    if profile_cfg is None:
        raise ConfigValidationError(
            f"unknown agent profile {profile_name!r}", profile=profile_name
        )
    if profile_cfg.kind != kind:
        raise ConfigValidationError(
            f"profile {profile_name!r} kind {profile_cfg.kind!r} does not match selection kind {kind!r}",
            profile=profile_name,
            profile_kind=profile_cfg.kind,
            selection_kind=kind,
        )

    allowed = PROFILE_FIELDS_BY_KIND.get(kind, set())
    overrides: dict[str, Any] = {}
    for field_name in (
        "model",
        "reasoning_effort",
        "command",
        "turn_timeout_ms",
        "read_timeout_ms",
        "stall_timeout_ms",
        "resume_across_turns",
    ):
        val = getattr(profile_cfg, field_name, None)
        if val is not None and field_name in allowed:
            overrides[field_name] = val

    overlaid = replace(base, **overrides) if overrides else base
    return _build_resolved(kind, profile_name, overlaid)


def selection_for_state(
    cfg: ServiceConfig | AgentConfig,
    state: str | None,
    *,
    ticket_profile: str | None = None,
    ticket_kind: str | None = None,
    dispatch_profile: str | None = None,
    dispatch_kind: str | None = None,
    agent_profiles: dict[str, AgentProfileConfig] | None = None,
) -> AgentSelection:
    """Convenience helper to resolve selection on either ServiceConfig or AgentConfig."""
    if isinstance(cfg, ServiceConfig):
        return cfg.selection_for_state(
            state,
            ticket_profile=ticket_profile,
            ticket_kind=ticket_kind,
            dispatch_profile=dispatch_profile,
            dispatch_kind=dispatch_kind,
        )
    return cfg.selection_for_state(
        state,
        ticket_profile=ticket_profile,
        ticket_kind=ticket_kind,
        dispatch_profile=dispatch_profile,
        dispatch_kind=dispatch_kind,
        agent_profiles=agent_profiles,
    )
