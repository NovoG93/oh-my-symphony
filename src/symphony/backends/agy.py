"""Antigravity CLI backend (`agy`)."""

from __future__ import annotations

from ..workflow import AgyConfig
from . import BackendInit
from .plain_cli import PlainCliBackend


class AgyBackend(PlainCliBackend):
    """Drive `agy --print "$(cat)"` once per Symphony worker turn."""

    def __init__(self, init: BackendInit) -> None:
        cfg = (
            init.resolved_backend_config
            if isinstance(init.resolved_backend_config, AgyConfig)
            else init.cfg.agy
        )
        super().__init__(
            init,
            agent_name="agy",
            command=cfg.command,
            turn_timeout_ms=cfg.turn_timeout_ms,
            resume_across_turns=cfg.resume_across_turns,
            unattended_flags=("--dangerously-skip-permissions",),
            continuation_flag="--continue",
        )
