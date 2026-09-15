"""Phase-oriented agent-attempt runner."""
from __future__ import annotations
import asyncio
import traceback
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, cast
from . import core as _core_module
if TYPE_CHECKING:
    from ..issue import Issue
    from ..workflow import ServiceConfig
def _core(): return _core_module
@dataclass
class _AttemptState:
    issue: Issue
    running_issue_id: str
    base_cfg: ServiceConfig
    cfg: ServiceConfig
    running: Any = None
    workspace: Any = None
    client: Any = None
    prev_phase_state: str = ""
    prev_phase_state_raw: str = ""
    known_app_release: bool = False
    after_run_pending: bool = False
    turn_number: int = 1
    outcome: str = "normal"
    error: str | None = None
@dataclass(frozen=True)
class _AttemptExit:
    outcome: str
    error: str | None = None
async def run_agent_attempt(orch, issue: Issue, attempt: int | None, cfg: ServiceConfig) -> None:
    BackendInit = _core().BackendInit
    SymphonyError = _core().SymphonyError
    TurnCancelled = _core().TurnCancelled
    TurnFailed = _core().TurnFailed
    TurnInputRequired = _core().TurnInputRequired
    TurnTimeout = _core().TurnTimeout
    ProviderCapacityError = _core().ProviderCapacityError
    _IssueDebug = _core()._IssueDebug
    _backend_agent_pid = _core()._backend_agent_pid
    _config_for_issue_agent = _core()._config_for_issue_agent
    _has_app_release_label = _core()._has_app_release_label
    _is_rewind_transition = _core()._is_rewind_transition
    _release_failure_target_state = _core()._release_failure_target_state
    _requested_agent_kind = _core()._requested_agent_kind
    _requested_agent_profile = _core()._requested_agent_profile
    _rewind_budget_target_state = _core()._rewind_budget_target_state
    _update_state_turn_counter = _core()._update_state_turn_counter
    build_continuation_prompt = _core().build_continuation_prompt
    build_first_turn_prompt = _core().build_first_turn_prompt
    evaluate_contract = _core().evaluate_contract
    git_inspect = _core().git_inspect
    linear_graphql_tool = _core().linear_graphql_tool
    log = _core().log
    normalize_state = _core().normalize_state
    redact_session_id = _core().redact_session_id
    render_skill_block = _core().render_skill_block
    resolve_agent_config = _core().resolve_agent_config
    running_issue_id = issue.id
    outcome: str = "normal"
    error: str | None = None
    try:
        running = orch._running.get(running_issue_id)
        if running is not None and not running.release_authority_resolved:
            try:
                release_authority = orch._prepare_release_dispatch(issue, cfg)
            except SymphonyError as exc:
                outcome = "error"
                error = str(exc)
                log.error(
                    "release_execution_refused",
                    issue_id=issue.id,
                    identifier=issue.identifier,
                    tracker_kind=cfg.tracker.kind,
                    error=error,
                )
                return
            issue = release_authority.issue
            running.issue = issue
            running.known_app_release = release_authority.app_release
            running.known_release_cycle_verifier = release_authority.cycle_verifier
            running.known_app_release_finalizer = release_authority.finalizer
            if release_authority.gate is not None:
                running.release_gate_finalizer = (
                    release_authority.gate.finalizer_identifier
                )
                running.release_gate_expected_contract_sha256 = (
                    release_authority.gate.expected_contract_sha256
                )
                running.release_gate_cycle_fingerprint = (
                    release_authority.gate.cycle_fingerprint
                )
                running.release_gate_generation = release_authority.gate.generation
            if release_authority.finalizer:
                running.release_finalizer_rewind_state = issue.state
            running.release_authority_resolved = True
        # Keep the *unrouted* workflow config: `agent.stage_kinds` must be
        # re-resolved at every in-run phase transition, and re-resolving
        # against an already-routed cfg would pin the first lane's backend
        # for the whole dispatch (the normal Todo→…→Document path).
        base_cfg = cfg
        cfg = _config_for_issue_agent(base_cfg, issue)
        running = orch._running.get(running_issue_id)
        if running is not None:
            running.agent_kind = cfg.agent.kind
        assert orch._workspace_manager is not None
        workspace = await orch._workspace_manager.create_or_reuse(issue.identifier)
        running = orch._running.get(running_issue_id)
        if running is None:
            # Slot was reclaimed externally between dispatch and the
            # first await completing. Surface the orphan path instead
            # of crashing on `KeyError(running_issue_id)` — that crash
            # was the source of the worker_task_finished_without_cleanup
            # cascade observed on OLV-002.
            outcome = "orphaned"
            error = "running entry vanished before workspace bind"
            log.warning(
                "worker_running_entry_vanished",
                issue_id=running_issue_id,
                site="workspace_bind",
            )
            return
        running.workspace_path = workspace.path
        if (
            running.known_app_release
            or running.known_release_cycle_verifier
            or running.known_app_release_finalizer
        ):
            if not orch._heartbeat_run_lease(running_issue_id, running):
                outcome = "release_authority_error"
                error = "application release lease was lost before workspace use"
                return
            try:
                running.issue = orch._require_running_release_authority(
                    cfg=cfg,
                    entry=running,
                    workspace_path=workspace.path,
                )
                issue = running.issue
            except Exception as exc:
                outcome = "release_authority_error"
                error = str(exc)
                return
        try:
            await orch._workspace_manager.before_run(workspace.path)
        except Exception as exc:
            outcome = "before_run_error"
            error = str(exc)
            return

        tools = []
        if cfg.tracker.kind == "linear" and cfg.agent.kind == "codex":
            tools.append(linear_graphql_tool())

        selection = cfg.selection_for_state(
            issue.state,
            ticket_profile=_requested_agent_profile(issue),
            ticket_kind=_requested_agent_kind(issue),
        )
        resolved_cfg = resolve_agent_config(cfg, selection)

        pool_id = "codex"
        if selection.profile and selection.profile in cfg.agent_profiles:
            prof = cfg.agent_profiles[selection.profile]
            pool_id = prof.usage_pool or prof.kind or cfg.agent.kind
        else:
            pool_id = selection.kind or cfg.agent.kind

        client = orch._build_agent_backend(
            BackendInit(
                cfg=cfg,
                cwd=workspace.path,
                workspace_root=cfg.workspace_root,
                on_event=lambda ev, issue_id=running_issue_id: orch._on_codex_event(
                    issue_id, ev
                ),
                on_process_started=lambda pid, issue_id=running_issue_id: (
                    orch._sync_backend_agent_pid(issue_id, pid)
                ),
                client_tools=tools,
                selection=selection,
                resolved_backend_config=resolved_cfg.active_config,
                usage_manager=orch._usage_manager,
                usage_pool=pool_id,
                env=orch._dispatch_env(issue=issue, cfg=cfg, is_rewind=False),
            )
        )

        # Expose the live backend to `_on_codex_event` so the stall-progress
        # predicate routes through `client.is_progress_event(...)`.
        running.client = client
        after_run_pending = False
        try:
            orch._sync_backend_agent_pid(
                running_issue_id, _backend_agent_pid(client)
            )
            try:
                await client.start()
            finally:
                orch._sync_backend_agent_pid(
                    running_issue_id, _backend_agent_pid(client)
                )
            await client.initialize()

            turn_number = 1
            debug = orch._issue_debug.setdefault(running_issue_id, _IssueDebug())
            # `cfg.tui.language` is the operator-chosen language for
            # both TUI chrome AND artefact docs. Resolution already
            # honours `SYMPHONY_LANG` (build_service_config call).
            doc_language = cfg.tui.language
            # Skill files are read off-loop; dispatch shares the event
            # loop with every other running worker.
            skill_context = await asyncio.to_thread(
                render_skill_block, cfg.workflow_path.parent, issue.skills
            )
            first_prompt, _ = build_first_turn_prompt(
                prompt_template=cfg.prompt_template_for_state(issue.state),
                issue=issue,
                attempt=attempt,
                language=doc_language,
                turn_number=debug.completed_turn_count + turn_number,
                max_turns=cfg.agent.max_total_turns,
                max_attempts=cfg.agent.max_attempts,
                auto_merge_on_done=cfg.agent.auto_merge_on_done,
                token_ema=orch._token_ema_for_state(issue.state),
                token_budget=orch._token_budget_for_state(cfg, issue.state),
                rewind_scope=None,
                compact_issue_context=cfg.agent.compact_issue_context,
                full_ticket_path=orch._ticket_prompt_path(cfg, issue),
                artifacts_dir=orch._prompt_artifacts_dir(cfg),
                extra_context=skill_context,
            )
            resumed_checkpoint = False
            checkpoint = running.continuation_checkpoint
            if checkpoint is not None:
                try:
                    resumed_checkpoint = await client.resume_session(
                        checkpoint.resume_session_id
                    )
                except Exception as exc:
                    log.error(
                        "session_continuation_resume_error",
                        issue_id=running_issue_id,
                        issue_identifier=issue.identifier,
                        agent_kind=cfg.agent.kind,
                        error_type=type(exc).__name__,
                    )
                    raise SymphonyError(
                        "exact session continuation failed before turn start"
                    ) from None
                running.recovery_session_resumed = resumed_checkpoint
                if resumed_checkpoint:
                    running.resume_session_id = checkpoint.resume_session_id
                    orch._append_run_event(running, "session_started", {})
                    log.info(
                        "session_continuation_resumed",
                        issue_id=running_issue_id,
                        issue_identifier=issue.identifier,
                        checkpoint_turn=checkpoint.turn,
                    )
                else:
                    log.info(
                        "session_continuation_fresh_fallback",
                        issue_id=running_issue_id,
                        issue_identifier=issue.identifier,
                        checkpoint_turn=checkpoint.turn,
                        agent_kind=cfg.agent.kind,
                    )
            if not resumed_checkpoint:
                await client.start_session(
                    initial_prompt=first_prompt,
                    issue_title=f"{issue.identifier}: {issue.title}",
                )

            # Track which kanban state the backend is currently
            # operating on. When the issue moves to a new state mid-run
            # we tear the backend down and rebuild it so the next phase
            # starts with a fresh context — shared knowledge flows only
            # through the markdown artefacts under
            # `docs/<identifier>/<stage>/` plus the ticket body.
            prev_phase_state = normalize_state(issue.state)
            # Canonical-cased mirror of `prev_phase_state`. Trackers
            # like Linear and Jira match state names case-sensitively
            # on writes, so a contract-failure rewind needs the
            # original casing rather than the lowercased form.
            prev_phase_state_raw = issue.state or ""
            # Minimal state refreshes intentionally omit labels. Retain
            # the last full-body app-release signal until the next full
            # refresh so stage-contracts=off cannot erase the machine gate.
            running_entry = orch._running.get(running_issue_id)
            known_app_release = (
                running_entry.known_app_release
                if running_entry is not None
                else False
            ) or _has_app_release_label(issue)

            while True:
                # Operator pause gate — `pause_worker` clears the event,
                # `resume_worker` sets it. Honoured at the turn boundary
                # so we never tear down a turn the model is mid-way
                # through. On resume, re-fetch issue state because the
                # operator may have moved the ticket while it was held.
                pause_event = orch._pause_events.get(running_issue_id)
                if pause_event is not None and not pause_event.is_set():
                    log.info(
                        "worker_paused",
                        issue_id=running_issue_id,
                        identifier=issue.identifier,
                        turn=turn_number,
                    )
                    await pause_event.wait()
                    log.info(
                        "worker_resumed",
                        issue_id=running_issue_id,
                        identifier=issue.identifier,
                        turn=turn_number,
                    )
                    refreshed = await orch._refresh_issue_state(
                        cfg, running_issue_id
                    )
                    if refreshed is not None:
                        issue = refreshed
                        running_entry = orch._running.get(running_issue_id)
                        if running_entry is not None:
                            running_entry.issue = issue

                current_state = normalize_state(issue.state)
                debug = orch._issue_debug.setdefault(
                    running_issue_id, _IssueDebug()
                )
                if (
                    cfg.agent.max_total_turns > 0
                    and debug.completed_turn_count + turn_number
                    > cfg.agent.max_total_turns
                ):
                    log.warning(
                        "worker_total_turn_budget_boundary",
                        issue_id=running_issue_id,
                        issue_identifier=issue.identifier,
                        completed_turns=debug.completed_turn_count,
                        next_turn=turn_number,
                        max_total_turns=cfg.agent.max_total_turns,
                    )
                    break
                is_phase_transition = (
                    turn_number > 1 and current_state != prev_phase_state
                )

                if is_phase_transition:
                    try:
                        is_rewind = _is_rewind_transition(
                            prev_phase_state,
                            current_state,
                            cfg.tracker.active_states,
                        )
                        # v0.6.7 — contract validator. When the agent
                        # moved forward (not a rewind), check that
                        # the producing stage actually wrote the
                        # sections its prompt promised. On failure:
                        # write the tracker state back to the
                        # producing stage, append a ## Contract
                        # Failure note, and treat the situation as
                        # a forced rewind so the rebuild + budget
                        # bookkeeping below still apply.
                        if not is_rewind and cfg.agent.stage_contracts_enabled(
                            cfg.tracker.active_states
                        ):
                            if prev_phase_state in {
                                "in progress",
                                "verify",
                                "document",
                                # legacy lane name (pre-rename boards)
                                "learn",
                                "done",
                            }:
                                # IMPORTANT: contract eval reads
                                # `issue.description`, so we MUST use
                                # the full-body refresh — not the
                                # minimal `_refresh_issue_state`, which
                                # returns description=None for every
                                # tracker adapter and would falsely
                                # fail every forward transition. See
                                # tests/test_orchestrator_contract_
                                # integration.py for the regression
                                # the v0.6.7 release surfaced.
                                refreshed_for_contract = (
                                    await orch._refresh_issue_full(
                                        cfg, running_issue_id
                                    )
                                )
                                if refreshed_for_contract is not None:
                                    issue = refreshed_for_contract
                                    known_app_release = (
                                        known_app_release
                                        or _has_app_release_label(issue)
                                    )
                                    running_entry = orch._running.get(
                                        running_issue_id
                                    )
                                    if running_entry is not None:
                                        running_entry.issue = issue
                                    current_state = normalize_state(issue.state)
                            contract = evaluate_contract(
                                producing_state=prev_phase_state,
                                ticket_body=issue.description or "",
                                identifier=issue.identifier,
                                docs_root=workspace.path / "docs",
                                artifact_store_root=(
                                    orch._artifact_store.root
                                    if (
                                        cfg.artifacts.require_for_done
                                        and orch._artifact_store is not None
                                    )
                                    else None
                                ),
                            )
                            if not contract.passed:
                                log.warning(
                                    "stage_contract_failed",
                                    issue_id=issue.id,
                                    identifier=issue.identifier,
                                    producing_state=prev_phase_state,
                                    advanced_to=current_state,
                                    missing=contract.missing,
                                )
                                await asyncio.to_thread(
                                    orch._tracker_call_append_note,
                                    cfg,
                                    issue,
                                    contract.note_heading,
                                    contract.note_body,
                                )
                                await asyncio.to_thread(
                                    orch._tracker_call_update_state,
                                    cfg,
                                    issue,
                                    prev_phase_state_raw or prev_phase_state,
                                )
                                # Pull the freshly-rewound body so the
                                # next backend rebuild's first prompt
                                # sees the ## Contract Failure note we
                                # just appended (full-body fetch — see
                                # the comment above the preflight
                                # refresh for why minimal would erase
                                # description).
                                refreshed = await orch._refresh_issue_full(
                                    cfg, running_issue_id
                                )
                                if refreshed is not None:
                                    issue = refreshed
                                issue = replace(
                                    issue,
                                    state=(
                                        prev_phase_state_raw or prev_phase_state
                                    ),
                                )
                                running_entry = orch._running.get(running_issue_id)
                                if running_entry is not None:
                                    running_entry.issue = issue
                                current_state = normalize_state(issue.state)
                                is_rewind = True
                            elif contract.warnings:
                                # Soft S2 advisories (e.g. a non-passing AC
                                # Scorecard row): surface as a ticket note
                                # without rewinding so the pipeline proceeds.
                                log.warning(
                                    "stage_contract_warn",
                                    issue_id=issue.id,
                                    identifier=issue.identifier,
                                    producing_state=prev_phase_state,
                                    advanced_to=current_state,
                                    warnings=contract.warnings,
                                )
                                await asyncio.to_thread(
                                    orch._tracker_call_append_note,
                                    cfg,
                                    issue,
                                    "Contract Warning",
                                    contract.warning_note.split("\n", 1)[1],
                                )
                        if is_rewind:
                            debug = orch._issue_debug.setdefault(
                                running_issue_id, _IssueDebug()
                            )
                            debug.rewind_count += 1
                            if (
                                cfg.agent.max_attempts > 0
                                and debug.rewind_count > cfg.agent.max_attempts
                            ):
                                rewind_target = _rewind_budget_target_state(cfg)
                                if rewind_target:
                                    await asyncio.to_thread(
                                        orch._tracker_call_update_state,
                                        cfg,
                                        issue,
                                        rewind_target,
                                    )
                                    issue = replace(issue, state=rewind_target)
                                running_entry = orch._running.get(running_issue_id)
                                if running_entry is not None:
                                    running_entry.issue = issue
                                log.warning(
                                    "rewind_budget_exceeded",
                                    issue_id=issue.id,
                                    identifier=issue.identifier,
                                    from_state=prev_phase_state,
                                    to_state=current_state,
                                    rewind_count=debug.rewind_count,
                                    max_attempts=cfg.agent.max_attempts,
                                    # F-32: a board with no block/human
                                    # terminal lane keeps its state; the
                                    # worker still stops.
                                    target_state=rewind_target or "(none)",
                                )
                                break
                        running_entry = orch._running.get(running_issue_id)
                        if running_entry is not None:
                            running_entry.consecutive_empty_turns = 0
                            running_entry.hit_empty_response_loop = False
                        # F-01: route the *new* lane's backend. The ticket
                        # walks several states inside one dispatch, so the
                        # kind must be re-resolved from the unrouted config
                        # here — not reused from the lane we started in.
                        phase_cfg = _config_for_issue_agent(base_cfg, issue)
                        phase_selection = phase_cfg.selection_for_state(
                            issue.state,
                            ticket_profile=_requested_agent_profile(issue),
                            ticket_kind=_requested_agent_kind(issue),
                        )
                        phase_resolved_agent = resolve_agent_config(
                            phase_cfg, phase_selection
                        )
                        to_kind = phase_selection.kind
                        to_profile = phase_selection.profile or ""
                        to_model = (
                            getattr(phase_resolved_agent.active_config, "model", "")
                            or ""
                        )
                        to_reasoning_effort = (
                            getattr(
                                phase_resolved_agent.active_config,
                                "reasoning_effort",
                                "",
                            )
                            or ""
                        )
                        from_kind = (
                            running_entry.agent_kind
                            if running_entry is not None
                            and running_entry.agent_kind
                            else cfg.agent.kind
                        )
                        from_profile = (
                            running_entry.agent_profile
                            if running_entry is not None
                            else ""
                        )
                        from_model = (
                            running_entry.model if running_entry is not None else ""
                        )
                        from_reasoning_effort = (
                            running_entry.reasoning_effort
                            if running_entry is not None
                            else ""
                        )
                        if (
                            from_kind != to_kind
                            or from_profile != to_profile
                            or from_model != to_model
                            or from_reasoning_effort != to_reasoning_effort
                        ):
                            log.info(
                                "stage_backend_rerouted",
                                issue_id=issue.id,
                                identifier=issue.identifier,
                                from_state=prev_phase_state,
                                to_state=current_state,
                                from_kind=from_kind,
                                to_kind=to_kind,
                                from_profile=from_profile,
                                to_profile=to_profile,
                                from_model=from_model,
                                to_model=to_model,
                                to_reasoning_effort=to_reasoning_effort,
                            )
                        cfg = phase_cfg
                        if running_entry is not None:
                            running_entry.agent_kind = to_kind
                            running_entry.agent_profile = to_profile
                            running_entry.model = to_model
                            running_entry.reasoning_effort = to_reasoning_effort
                            if (
                                running_entry.run_id
                                and orch._run_registry is not None
                            ):
                                stage_registry = cast(
                                    Any, orch._run_registry
                                )
                                stage_run_id = running_entry.run_id
                                orch._registry_guard(
                                    "update_stage_agent_profile",
                                    lambda: (
                                        stage_registry.update_stage_agent_profile(
                                            issue_id=running_issue_id,
                                            run_id=stage_run_id,
                                            state=current_state,
                                            agent_kind=to_kind,
                                            agent_profile=to_profile,
                                            model=to_model,
                                            reasoning_effort=to_reasoning_effort,
                                        )
                                    ),
                                    False,
                                )
                        (
                            client,
                            first_prompt,
                        ) = await orch._rebuild_backend_for_phase(
                            issue=issue,
                            running_issue_id=running_issue_id,
                            cfg=cfg,
                            workspace_path=workspace.path,
                            attempt=attempt,
                            doc_language=doc_language,
                            old_client=client,
                            is_rewind=is_rewind,
                            turn_number=debug.completed_turn_count + turn_number,
                        )
                        running_entry = orch._running.get(running_issue_id)
                        if running_entry is not None:
                            running_entry.client = client
                            running_entry.thread_id = None
                            running_entry.session_id = None
                            running_entry.turn_id = None
                            running_entry.resume_session_id = None
                            running_entry.last_completed_turn_event = 0
                            running_entry.last_reported_input_tokens = 0
                            running_entry.last_reported_cache_input_tokens = 0
                            running_entry.last_reported_output_tokens = 0
                            running_entry.last_reported_total_tokens = 0
                            running_entry.codex_state_input_tokens = 0
                            running_entry.codex_state_cache_input_tokens = 0
                            running_entry.codex_state_output_tokens = 0
                            running_entry.codex_state_total_tokens = 0
                            running_entry.last_ema_state_total_tokens = 0
                            running_entry.hit_token_budget = False
                            running_entry.token_budget_cap = 0
                            debug.state_turn_state = current_state
                            debug.state_turn_count = 0
                        log.info(
                            "worker_phase_transition",
                            issue_id=issue.id,
                            identifier=issue.identifier,
                            from_state=prev_phase_state,
                            to_state=current_state,
                            turn=turn_number,
                            attempt=attempt,
                            is_rewind=is_rewind,
                            workspace=str(workspace.path),
                        )
                        if running_entry is not None:
                            orch._append_run_event(
                                running_entry,
                                "phase_transition",
                                {
                                    "from_state": prev_phase_state,
                                    "to_state": current_state,
                                    "turn": turn_number,
                                    "attempt": attempt,
                                    "is_rewind": is_rewind,
                                },
                            )
                        orch._record_stats_transition(
                            issue.identifier, prev_phase_state, current_state
                        )
                    except Exception as exc:
                        outcome = "phase_transition_error"
                        error = str(exc)
                        return

                running_entry = orch._running.get(running_issue_id)
                if (
                    running_entry is not None
                    and running_entry.hit_empty_response_loop
                ):
                    await orch._escalate_empty_response_loop(
                        cfg=cfg,
                        entry=running_entry,
                        issue_id=running_issue_id,
                        cancel_worker=False,
                    )
                    break

                is_continuation = (
                    running.recovery_session_resumed and turn_number == 1
                ) or (turn_number > 1 and not is_phase_transition)
                if is_continuation:
                    debug = orch._issue_debug.setdefault(
                        running_issue_id, _IssueDebug()
                    )
                    prompt = build_continuation_prompt(
                        language=doc_language,
                        turn_number=debug.completed_turn_count + turn_number,
                        max_turns=cfg.agent.max_total_turns,
                    )
                else:
                    prompt = first_prompt

                running = orch._running.get(running_issue_id)
                if running is None:
                    outcome = "orphaned"
                    error = "running entry vanished before turn start"
                    log.warning(
                        "worker_running_entry_vanished",
                        issue_id=running_issue_id,
                        site="turn_start",
                    )
                    return
                running.turn_count = turn_number
                if (
                    running.known_app_release
                    or running.known_release_cycle_verifier
                    or running.known_app_release_finalizer
                ):
                    if not orch._heartbeat_run_lease(running_issue_id, running):
                        outcome = "release_authority_error"
                        error = (
                            "application release lease was lost before agent turn"
                        )
                        return
                    try:
                        running.issue = orch._require_running_release_authority(
                            cfg=cfg,
                            entry=running,
                        )
                        issue = running.issue
                    except Exception as exc:
                        outcome = "release_authority_error"
                        error = str(exc)
                        return
                # Capture the state THIS turn is starting in. C3 EMA
                # samples need the source state, not the destination
                # the agent flips to mid-turn — without this, every
                # stage's tokens get attributed to the next stage.
                running.state_at_turn_start = (running.issue.state or "").lower()
                # Symmetry with worker_turn_completed — a single line per
                # turn-start so multi-turn runs (especially slow ones
                # like gemini -p where a single turn can take 60-90s)
                # don't look stuck between turns.
                log.info(
                    "worker_turn_started",
                    issue_id=running_issue_id,
                    identifier=running.issue.identifier,
                    turn=turn_number,
                    max_turns=cfg.agent.max_turns,
                    is_continuation=is_continuation,
                )
                orch._append_run_event(
                    running,
                    "turn_started",
                    {
                        "turn": turn_number,
                        "state": running.issue.state,
                        "continuation": is_continuation,
                    },
                )
                if turn_number > 1:
                    try:
                        await orch._workspace_manager.before_run(workspace.path)
                    except Exception as exc:
                        outcome = "before_run_error"
                        error = str(exc)
                        return
                orch._sync_backend_agent_pid(
                    running_issue_id, _backend_agent_pid(client)
                )
                after_run_pending = True
                try:
                    await client.run_turn(
                        prompt=prompt, is_continuation=is_continuation
                    )
                except ProviderCapacityError as exc:
                    outcome = "provider_usage_exhausted"
                    error = str(exc)
                    from ..backends.usage import ProviderUsageSnapshot, UsageWindow

                    windows = {}
                    if exc.resets_at:
                        windows["default"] = UsageWindow(
                            key="default",
                            used_percent=100.0,
                            remaining_percent=0.0,
                            resets_at=exc.resets_at,
                        )
                    snap = ProviderUsageSnapshot(
                        pool_id=exc.pool_id,
                        source=exc.pool_id,
                        windows=windows,
                        hard_limit_reached=True,
                        authoritative=True,
                        observed_at=datetime.now(timezone.utc),
                    )
                    orch._usage_manager.set_snapshot(exc.pool_id, snap)
                    return
                except (
                    TurnTimeout,
                    TurnFailed,
                    TurnCancelled,
                    TurnInputRequired,
                ) as exc:
                    outcome = "turn_error"
                    error = str(
                        redact_session_id(str(exc), running.resume_session_id)
                    )
                    return

                finally:
                    orch._sync_backend_agent_pid(
                        running_issue_id, _backend_agent_pid(client)
                    )

                # Synchronous log on the worker's hot path — the
                # listener-side `agent_turn_completed` log fires from
                # `_on_codex_event` via the EVENT_TURN_COMPLETED emit,
                # but reconcile can cancel the worker between the emit
                # and the listener running, swallowing the visibility
                # signal. Logging here guarantees one line per
                # successful turn even when reconcile races us.
                running_entry = orch._running.get(running_issue_id)
                if running_entry is not None:
                    log.info(
                        "worker_turn_completed",
                        issue_id=running_issue_id,
                        identifier=running_entry.issue.identifier,
                        turn=turn_number,
                        input_tokens=running_entry.codex_input_tokens,
                        cache_input_tokens=running_entry.codex_cache_input_tokens,
                        output_tokens=running_entry.codex_output_tokens,
                        total_tokens=running_entry.codex_total_tokens,
                    )

                await orch._workspace_manager.after_run_best_effort(workspace.path)
                after_run_pending = False
                # Collect before the next loop iteration evaluates the
                # stage contract, so `artifacts.require_for_done` sees
                # this turn's deliverables, and before Done removes the
                # workspace they live in.
                await orch._collect_ticket_artifacts(
                    cfg,
                    identifier=issue.identifier,
                    workspace_path=workspace.path,
                    run_id=(
                        running_entry.run_id if running_entry is not None else ""
                    ),
                    turn=turn_number,
                )
                # The hook may commit or amend the turn's changes. Resolve
                # HEAD only after it finishes so the explorer never reports
                # the base/prior-turn commit as this turn's result.
                commit_sha = None
                if (workspace.path / ".git").exists():
                    commit_sha = await asyncio.to_thread(
                        git_inspect.resolve_commit, workspace.path, "HEAD"
                    )
                if commit_sha:
                    running_entry = orch._running.get(running_issue_id)
                    if running_entry is not None:
                        orch._append_run_event(
                            running_entry,
                            "workspace_updated",
                            {"turn": turn_number, "commit_sha": commit_sha},
                        )

                running_entry = orch._running.get(running_issue_id)
                registry = orch._run_registry
                if (
                    cfg.agent.crash_continuation
                    and registry is not None
                    and running_entry is not None
                    and running_entry.run_id
                    and running_entry.resume_session_id
                    and running_entry.last_completed_turn_event == turn_number
                    and not running_entry.known_app_release
                    and not running_entry.known_release_cycle_verifier
                    and not running_entry.known_app_release_finalizer
                ):
                    checkpoint_turn = debug.completed_turn_count + turn_number
                    checkpoint_registry = cast(Any, registry)
                    checkpoint_run_id = running_entry.run_id
                    checkpoint_session_id = running_entry.resume_session_id
                    checkpoint_state = running_entry.issue.state
                    orch._registry_guard(
                        "checkpoint_completed_turn",
                        lambda: checkpoint_registry.checkpoint_completed_turn(
                            issue_id=running_issue_id,
                            run_id=checkpoint_run_id,
                            resume_session_id=checkpoint_session_id,
                            state=checkpoint_state,
                            turn=checkpoint_turn,
                        ),
                        False,
                    )

                # Record the state the backend just operated on so the
                # next iteration can detect a phase transition against
                # the freshly refreshed state below.
                prev_phase_state = current_state
                prev_phase_state_raw = (
                    running.issue.state if running is not None else issue.state
                ) or ""

                # Refresh issue state.
                refreshed = await orch._refresh_issue_state(cfg, running_issue_id)
                if refreshed is None:
                    outcome = "issue_state_refresh_failed"
                    error = "could not refresh issue state"
                    return
                issue = refreshed
                running = orch._running.get(running_issue_id)
                if running is None:
                    outcome = "orphaned"
                    error = "running entry vanished after issue refresh"
                    log.warning(
                        "worker_running_entry_vanished",
                        issue_id=running_issue_id,
                        site="post_refresh",
                    )
                    return
                running.issue = issue
                state = normalize_state(issue.state)
                active = {s.lower() for s in cfg.tracker.active_states}
                release_rewound = False
                if (
                    running.known_app_release_finalizer
                    and state != prev_phase_state
                ):
                    try:
                        finalizer_identifier = (
                            running.release_gate_finalizer or issue.identifier
                        )
                        finalizer_gate = cast(
                                Any,
                            orch._release_registry_call(
                                cfg,
                                "read_finalizer_gate_after_turn",
                                lambda registry: registry.get_release_gate(
                                    finalizer_identifier
                                ),
                            ),
                        )
                        if finalizer_gate is None:
                            raise SymphonyError(
                                "application release finalizer authority disappeared",
                                finalizer=issue.identifier,
                            )
                        issue = orch._guard_release_finalizer(
                            cfg=cfg,
                            issue=issue,
                            gate=finalizer_gate,
                            rewind_state=(prev_phase_state_raw or prev_phase_state),
                            expected_run_id=running.run_id,
                            require_run_authority=True,
                        )
                    except Exception as exc:
                        try:
                            issue = await orch._rewind_app_release_transition(
                                cfg=cfg,
                                issue=issue,
                                producing_state=(
                                    prev_phase_state_raw or prev_phase_state
                                ),
                                note_body=(
                                    "Final delivery was stopped because the "
                                    f"host-owned release approval is invalid: {exc}"
                                ),
                            )
                            running.issue = issue
                        except Exception as rewind_exc:
                            log.error(
                                "release_finalizer_rewind_failed",
                                issue_id=issue.id,
                                identifier=issue.identifier,
                                gate_error=str(exc),
                                rewind_error=str(rewind_exc),
                            )
                        outcome = "phase_transition_error"
                        error = str(exc)
                        return
                    if state in active:
                        running.release_finalizer_rewind_state = issue.state
                if (
                    state != prev_phase_state
                    and prev_phase_state == "verify"
                    and not _is_rewind_transition(
                        prev_phase_state,
                        state,
                        cfg.tracker.active_states,
                    )
                ):
                    try:
                        (
                            issue,
                            release_rewound,
                        ) = await orch._enforce_app_release_transition(
                            cfg=cfg,
                            issue=issue,
                            workspace_path=workspace.path,
                            producing_state=(
                                prev_phase_state_raw or prev_phase_state
                            ),
                            known_app_release=known_app_release,
                            running_entry=running,
                        )
                    except Exception as exc:
                        outcome = "phase_transition_error"
                        error = str(exc)
                        return
                    known_app_release = known_app_release or _has_app_release_label(
                        issue
                    )
                    running.issue = issue
                    state = normalize_state(issue.state)
                if running.release_verifier_handoff_complete:
                    break
                if release_rewound:
                    debug.rewind_count += 1
                    if (
                        cfg.agent.max_attempts > 0
                        and debug.rewind_count > cfg.agent.max_attempts
                    ):
                        rewind_target = _release_failure_target_state(cfg)
                        if rewind_target:
                            await asyncio.to_thread(
                                orch._tracker_call_update_state,
                                cfg,
                                issue,
                                rewind_target,
                            )
                            issue = replace(issue, state=rewind_target)
                            running.issue = issue
                        else:
                            running.release_gate_exhausted = True
                        log.warning(
                            "rewind_budget_exceeded",
                            issue_id=issue.id,
                            identifier=issue.identifier,
                            from_state=prev_phase_state,
                            to_state=state,
                            rewind_count=debug.rewind_count,
                            max_attempts=cfg.agent.max_attempts,
                            target_state=rewind_target or "(none)",
                        )
                    break
                if state not in active:
                    break
                state_turn_count = _update_state_turn_counter(debug, state)
                max_state_turns = orch._max_state_turns_for_state(cfg, state)
                if max_state_turns > 0 and state_turn_count >= max_state_turns:
                    running.hit_no_stage_change = True
                    log.warning(
                        "no_stage_change_watchdog",
                        issue_id=running_issue_id,
                        issue_identifier=running.issue.identifier,
                        state=running.issue.state,
                        state_turn_count=state_turn_count,
                        effective_max_state_turns=max_state_turns,
                        global_max_state_turns=cfg.agent.max_state_turns,
                    )
                    break
                if turn_number >= cfg.agent.max_turns:
                    # Per-attempt ceiling reached without a terminal
                    # transition. Mark explicitly so `_on_worker_exit`
                    # doesn't auto-schedule a continuation — the ticket
                    # waits for operator action instead of looping
                    # silently against the ceiling.
                    running.hit_max_turns = True
                    log.warning(
                        "worker_max_turns_exhausted",
                        issue_id=running_issue_id,
                        issue_identifier=running.issue.identifier,
                        turns=turn_number,
                        max_turns=cfg.agent.max_turns,
                    )
                    break
                turn_number += 1
        finally:
            # Defensive: a phase transition may have left `client`
            # pointing to a half-initialized backend, or to one whose
            # earlier `stop()` already failed. Either way, exiting the
            # worker without after_run_best_effort would leak workspace
            # state, so swallow stop() errors here too.
            try:
                await client.stop()
            except Exception as stop_exc:
                running = orch._running.get(running_issue_id)
                if running is not None:
                    running.backend_cleanup_unconfirmed = True
                log.warning(
                    "worker_final_stop_failed",
                    issue_id=issue.id,
                    identifier=issue.identifier,
                    error=str(stop_exc),
                )
            else:
                running = orch._running.get(running_issue_id)
                if running is not None and running.backend_cleanup_unconfirmed:
                    log.warning(
                        "worker_final_stop_cleanup_unconfirmed",
                        issue_id=issue.id,
                        identifier=issue.identifier,
                        pid=running.agent_pgid,
                    )
                else:
                    orch._sync_backend_agent_pid(running_issue_id, None)
            if after_run_pending:
                await orch._workspace_manager.after_run_best_effort(workspace.path)
            # Salvage deliverables written before an abnormal exit (turn
            # timeout, TurnFailed, stall eviction). The per-turn call runs
            # only on the success path, and the workspace is torn down at
            # Done, so without this the file is gone for good — worst
            # under `artifacts.require_for_done`, where the deliverable
            # turn is the long, timeout-prone one. Unshielded and
            # best-effort, exactly like the `after_run` hook above.
            entry_for_run = orch._running.get(running_issue_id)
            await orch._collect_ticket_artifacts(
                cfg,
                identifier=issue.identifier,
                workspace_path=workspace.path,
                run_id=entry_for_run.run_id if entry_for_run else "",
                turn=None,  # salvage pass: the turn it came from is unknown
            )
    except asyncio.CancelledError:
        outcome = "shutdown_interrupted" if orch._stopping else "cancelled"
        error = None
        raise
    except SymphonyError as exc:
        outcome = "error"
        running = orch._running.get(running_issue_id)
        private_session_id = (
            running.resume_session_id if running is not None else None
        )
        error = str(redact_session_id(str(exc), private_session_id))
    except Exception as exc:
        outcome = "error"
        running = orch._running.get(running_issue_id)
        private_session_id = (
            running.resume_session_id if running is not None else None
        )
        error = str(redact_session_id(str(exc), private_session_id))
        log.error(
            "worker_unhandled_error",
            issue_id=running_issue_id,
            error=error,
            exc_type=type(exc).__name__,
            traceback=str(
                redact_session_id(traceback.format_exc(), private_session_id)
            ),
        )
    finally:
        # Diagnostic marker — pairs with `worker_task_done_without_cleanup`
        # to localize the path that leaves entries in `_running`. If
        # this line is missing from the log right before that error,
        # the outer finally never ran (Python contract violation =
        # interpreter shutdown / OS-level kill). If it IS present,
        # the bypass is inside `_on_worker_exit` itself.
        log.info(
            "worker_finally_entered",
            issue_id=running_issue_id,
            outcome=outcome,
            error=error,
        )
        # AF-01 — a force-ejected zombie's `finally` can run after a
        # retry already installed a fresh entry under this issue id
        # (the zombie task is never cancelled by force-eject, only its
        # bookkeeping is dropped). Only the task that actually owns the
        # current entry may stamp `exit_started_at` or enter
        # `_on_worker_exit`; a foreign owner must not touch either.
        # The handler keeps its own identity check as the single guard
        # around the eventual pop.
        # `entry.worker_task is None` counts as owned — many existing
        # tests drive this coroutine directly against a hand-installed
        # entry that never went through `_dispatch`.
        owning_task = asyncio.current_task()
        entry = orch._running.get(running_issue_id)
        stale_entry = (
            entry is not None
            and owning_task is not None
            and orch._dispatch_state.entry_foreign_to(running_issue_id, owning_task)
        )
        if stale_entry:
            log.warning(
                "worker_finally_stale_entry",
                issue_id=running_issue_id,
                reason=outcome,
            )
        elif entry is not None:
            entry.exit_started_at = datetime.now(timezone.utc)
            await asyncio.shield(
                orch._on_worker_exit(
                    running_issue_id, outcome, error, owning_task=owning_task
                )
            )
