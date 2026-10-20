# Upstream Synchronization & Value Assessment Plan

**Date**: 2026-10-19  
**Upstream Range Evaluated**: `aaf2b72da3b97fb9d0b71fb881fafea38f59de6d` .. `f69e7cd7540296aedac856c9a7338653a47e6bb2` (`upstream/dev` / `upstream/main` / `v0.25.0`)  
**Base Repository / Fork**: `oh-my-symphony` (`develop` branch at `7917524`)  
**Evaluation Scope**: 54 unintegrated upstream commits across Orchestrator Core, Chat/Intent Gate, Security, Backends, Trackers, and Tooling.

---

## 1. Executive Summary & Strategy

Upstream development (`v0.22.0` through `v0.25.0`) delivered significant stability hardening, critical security fixes, and workflow governance features. However, upstream also executed a massive architectural refactoring of `src/symphony/orchestrator/core.py` (extracting `attempt.py`, `worker_exit.py`, and `release_transition.py`) that is tightly bound to an external `_AgentPhaseState` machine which our fork deliberately deferred in commit `7981dc1`.

### Core Directive: Selective Adaptation over Blind Merge
- **Do NOT perform a blind git merge of `upstream/dev`**: A naive merge would clobber our fork's ~10.6k-line `core.py`, destroying mission-critical fork capabilities:
  - **Named Agent Profiles & Tiered Routing** (`TIER-001`/`TIER-002`: OpenCode/AGY routing, dynamic model & reasoning effort resolution).
  - **Provider Usage Pools & Quotas** (`ProviderUsageManager`, `usage_pools`, `ProviderCapacityError`, quota event classification).
  - **Copilot Backend & Session Resumption** (`src/symphony/backends/copilot.py`).
  - **App-Release Lifecycle & Release Authority** (`release_contracts.py`, `release_cycle.py`).
  - **Inline Phase Transitions** (avoiding the upstream `_AgentPhaseState` machine).
- **Strategy**: Execute an **adapted selective porting plan**. All high-value bug fixes, security patches, and governance features operate on standard orchestrator state dictionaries and helper functions and can be cleanly integrated into our existing fork architecture without adopting upstream's core gutting.

---

## 2. High-Value Changes Inventory

### A. Critical Bug Fixes & Security (P0 — Immediate Value)

| Commit | Component | Defect / Problem | Solution & Fork Value |
| :--- | :--- | :--- | :--- |
| **`86d8b73`** | `src/symphony/web_policy.py:441` | **Body-less Mutation CSRF Gate**: `web_policy.py` skips checking `Content-Type: application/json` when `request.body_exists` is false. Cross-origin HTML forms (`<form action="/api/v1/issues/.../pause" method="POST">`) bypass CORS preflights and trigger mutations. | **Critical Security Fix**: Remove `and request.body_exists` in `web_policy.py` (enforce 415 on non-JSON mutations) and update `app.js` `apiRequest` to send `{}`. |
| **`58a9537`** | `src/symphony/orchestrator/core.py` | **Dispatch Slot Deadlock on Cancelled Workers**: When a running worker is cancelled due to terminal ticket state (Archive/Cancelled/Blocked), exit handling falls into `_handle_worker_error` with `holds_slot=True`. On bounded concurrency (`max_concurrent: 1`), this **permanently leaks the dispatch slot**. | **Critical Scheduler Fix**: Upon exit with `reason == "cancelled"`, refresh tracker state; if terminal, discard claim and cancel pending retries immediately without holding slot. |
| **`ed389a3`** | `src/symphony/orchestrator/attempt.py`<br>`src/symphony/orchestrator/contracts.py` | **1. Contract Rewind No-Op**: In `attempt.py:910-912`, `prev_phase_state_raw` reads `running.issue.state`. A mid-turn poll updates this to `Verify`, so contract failure attempts a `Verify -> Verify` rewind (silent no-op stranding ticket).<br>**2. Heading Regex Failure**: `contracts.py:307` strict regex `r"^##\s+heading\s*:?\s*$"` rejects compliant inline headers like `## Merge Status: clean`. | **Critical Contract Fix**: Fix `prev_phase_state_raw` assignment and loosen regex to accept trailing text on header line. Fixes stranded tickets in `Verify`. |
| **`0126a2a`** | `src/symphony/backends/codex.py:1473-1505` | **Codex Token Usage Double-Counting**: Codex app-server v2 sends `cachedInputTokens` as a subset of `inputTokens` and `reasoningOutputTokens` as a subset of `outputTokens`. Fork currently adds them, inflating token metrics. | **Bug Fix**: Update `_update_tokens_from_v2_block` to avoid summing subsets. Restores accurate telemetry and dashboard stats. |
| **`86d8b73`** | `src/symphony/service.py` | **Service Lifecycle & Zombie Processes**:<br>1. `service start` polls `kill(pid, 0)` which is True for zombies, falsely reporting success on immediate crash.<br>2. `service stop` fails when process is not process group leader.<br>3. Stale `.lock` files from SIGKILL/reboot block future starts forever. | **Lifecycle Fix**:<br>1. Reaps with `waitpid(WNOHANG)` over 2s startup window.<br>2. Fallback to `os.kill` when `killpg` fails.<br>3. `_open_lock_reclaiming_stale` inspects lock PID and unlinks dead locks. |
| **`3f71d30`** | `src/symphony/orchestrator/release_contracts.py` | **Empty `auto_merge_target_branch` Resolution**: Default configs leave target branch empty (meaning current branch). Release binder refused empty strings, stranding verifiers in `Verify`. | **Fix**: Use `resolve_configured_target_branch` to resolve empty target to `current_branch(repository_root)`. |
| **`d67428e`** | `src/symphony/projects.py` | **Symlink Loop Protection on Python 3.13+**: `Path.resolve()` no longer raises `ELOOP` under Python 3.13+, treating cyclic symlinks as candidate repos. Also relaxes lease test timeouts from 1s to 10s for macOS. | **Fix**: Explicit parent loop detection and test stabilization on macOS runners. |

---

### B. High-Value Features & Platform Hardening (P1)

| Commit | Component | Feature Description | Value for Fork |
| :--- | :--- | :--- | :--- |
| **`56ff023`** | `src/symphony/orchestrator/core.py` | **Reopen Spending Cap (`agent.max_reopens`)**: Uses SQLite `RunRegistry` to track how many times a ticket cycled to `Done`. When `count >= max_reopens` (default: 3), parks ticket in `Blocked` with `## Reopen Budget`. | **High**: Prevents infinite automated reopen loops and runaway LLM spend across multi-agent pipelines. |
| **`f46a026`** | `src/symphony/orchestrator/attempt.py`<br>`src/symphony/orchestrator/contracts.py` | **Mechanical Contracts on Move into `Done`**: Moves into terminal `Done` bypassed `_transition_agent_phase`. Calls `_enforce_stage_contract` during terminal transition and validates deep preset artifacts. | **High**: Eliminates quality loophole where unverified code could transition into `Done` without contract checks. |
| **`babeba4`** | `src/symphony/stats.py`<br>`src/symphony/prompt.py` | **Autonomous Learning Loop (`board_health`)**: Ingests gate failures and dynamic lane rewinds (`from_index > to_index`) to produce a concise 8-line `{{ board_health }}` summary. Document stage prompt instructs agents to write preventive wiki learnings. | **High**: Creates a self-correcting feedback loop across agent runs without consuming prompt tokens on healthy boards. |
| **`5e97ba5`** | `src/symphony/trackers/jira.py` | **Jira ADF Formatting & Idempotent Notes**: Converts Markdown headings, bullets, and formatting into Atlassian Document Format (ADF). Uses unique marker hashes to prevent duplicate comments on retries. | **High**: Clean addition to Jira tracker; eliminates duplicate comment spam during retries. |
| **`34fe676`** | `src/symphony/cli/doctor.py` | **Agent Binary `--version` Probing**: Probes resolved binaries on `$PATH` with `--version` (15s timeout) to catch broken wrappers and empty npm postinstall stubs. | **High**: Upgrades preflight diagnostic accuracy for agent environments. Can be extended to fork's named profiles. |
| **`c025fa0`** | `src/symphony/orchestrator/core.py`<br>`src/symphony/webapi.py` | **Issue Budget Snapshots & UI Cards**: Exposes `/api/v1/issues/<id>/budgets` (turns, state tokens, retries, rewinds) and adds budget visibility cards in Web UI. | **High**: Direct observability boost for operators tracking ticket expenditure and rewind counts. |
| **`e828324`** | `src/symphony/intent.py` | **Intent Gate Trip-Wire Scans**: Scans proposed intent text for heuristic risk patterns (schema migrations, auth tokens, crypto) and forces `track: full` on hit. | **Medium-High**: Automated risk assessment before unattended dark factory runs. |
| **`fd5a7f6`** | `src/symphony/trackers/validate.py`<br>`src/symphony/backends/per_turn.py` | **Topological Sort Kahn's Algorithm & Subprocess Reap**: Replaces O(N²) topological order with Kahn's algorithm O(V+E) and converts cycle checks to iterative traversal. Wraps per-turn subprocess execution in `except BaseException` to reap processes immediately. | **High**: Prevents `RecursionError` on deep boards and eliminates leaked zombie agent processes. |
| **`6512e5a`** | `src/symphony/tui/screens.py`<br>`src/symphony/cli/main.py` | **TUI Modal Help & CLI Subcommand Improvements**: Modal scrollable `HelpScreen` on `?` in TUI, top-level `--help` listing all subcommands, and fast exit (code 2) on mistyped subcommands. | **Medium**: Great ergonomic and usability improvement. |

---

## 3. High Risk / Incompatible Upstream Changes (Must NOT Merge Verbatim)

1. **Decomposition of `core.py` (`a85e68a`, `6e3a82a`, `6b73d3f`)**:
   - **Upstream change**: Stripped 3,157 lines from `core.py` into `attempt.py`, `worker_exit.py`, and `release_transition.py`.
   - **Why to defer**: Upstream's split modules rely on `_AgentPhaseState` / `_AgentPhaseTransition` from `7981dc1` which our fork deliberately deferred. Our fork already has its own phase-oriented `attempt.py` (`01115a5`) tailored to preserve profiles, usage pools, and Copilot. Blindly pulling upstream's files would destroy our fork's custom routing.
   - **Recommendation**: **DEFER** upstream file extractions; keep orchestrator logic in our existing `core.py` + tailored `attempt.py`.

2. **Upstream Quota Fallback (`20cd661`) vs Fork Usage Pools**:
   - **Upstream change**: Upstream mutates ticket markdown frontmatter (`force=True`) to set `agent.kind: claude` when quota errors occur.
   - **Why to adapt only**: In our fork, routing is governed by **Usage Pools** (`UsagePoolConfig`, `ProviderUsageManager`) and **Named Profiles** (`TIER-001/002`). Hardcoding `agent.kind` on the ticket breaks profile resolution.
   - **Recommendation**: **ADAPT** by extracting upstream's quota error regexes (`_QUOTA_WORKER_ERROR_MARKERS`) into our fork's `ProviderCapacityError` handler; reject the ticket frontmatter mutation.

3. **`JsonlStreamBackend` Refactor (`d51bfa2`)**:
   - **Upstream change**: Refactored `ClaudeCodeBackend` and `PiBackend` to inherit from `JsonlStreamBackend`.
   - **Why to adapt only**: Our fork heavily customizes `claude_code.py` and `pi.py` with `ClaudeUsageProbe`, `PiUsageProbe`, passive rate limit sniffing, and dynamic model injection. A naive merge would wipe out telemetry and profile routing.
   - **Recommendation**: Introduce `JsonlStreamBackend` in `per_turn.py` for process reaping, but preserve our fork's telemetry and profile hooks when updating `claude_code.py` and `pi.py`.

---

## 4. Already Ported / Reconciled in Fork

The following upstream changes are already resolved in our fork's `develop` branch:
- **`825dd69`** (`atomic_json.py` + state file namespacing) &rarr; Ported in commit `0adc222` with explicit `fsync` and clean tempfile handling.
- **`abb91db`** (Per-dispatch env isolation `BackendInit.env` & retry ticket conflict checks) &rarr; Ported in commits `aba633d` & `f36830f`.
- **`ea13231`** (Linear GraphQL inverse relation schema fix) &rarr; Ported in commit `84abe09` (`fix(linear): support current Linear inverse relation schema`).
- **`0145f36` / `060c2a7`** (Chat Intent Gate & web cards) &rarr; Ported in commits `0634870` & `f5f45e6` with fork security policy and i18n support.

---

## 5. Phased Implementation Roadmap

### Phase 1: Critical Security & Defect Fixes (P0)
1. **Web CSRF Gate**:
   - Modify `src/symphony/web_policy.py:441` to remove `and request.body_exists`.
   - Update `src/symphony/web/static/app.js:146` `apiRequest` to ensure mutating requests send `{}`.
2. **Scheduler Slot Deadlock**:
   - In `src/symphony/orchestrator/core.py` (`_on_worker_exit_impl`), add terminal state check for `reason == "cancelled"`. Discard claim and cancel pending retries without holding slot.
3. **Contract Rewind & Heading Regex**:
   - In `src/symphony/orchestrator/attempt.py:910-912`, fix `prev_phase_state_raw` assignment to preserve pre-turn state.
   - In `src/symphony/orchestrator/contracts.py:307`, loosen header regex to accept trailing text on same line.
4. **Codex Token Calculation**:
   - In `src/symphony/backends/codex.py:1473-1505`, remove double-addition of cached and reasoning token subsets.
5. **Service Lifecycle & Stale Locks**:
   - In `src/symphony/service.py`, port `waitpid(WNOHANG)` startup check, `_open_lock_reclaiming_stale`, and `os.kill` fallback.
6. **Release Binder Target Branch**:
   - In `src/symphony/orchestrator/release_contracts.py`, add `resolve_configured_target_branch`.

### Phase 2: Workflow Governance & Quality Hardening (P1)
1. **Reopen Spending Budget**:
   - Add `max_reopens` (default: 3) to `AgentConfig` in `workflow/config.py`.
   - Port `_prior_done_run_count` and `_hold_reopened_ticket_over_budget` into `core.py`.
2. **Mechanical Contracts on Move into `Done`**:
   - Call `_enforce_stage_contract` during terminal transitions to `Done` in `attempt.py`.
3. **Autonomous Learning Loop (`board_health`)**:
   - In `src/symphony/stats.py`, add `gate` event aggregation, dynamic rewind detection (`from_index > to_index`), and `board_health_summary`.
   - In `src/symphony/prompt.py`, inject `{{ board_health }}` into prompt context.
   - Update prompt templates under `docs/symphony-prompts/*/document.md` to capture learnings.
4. **Jira ADF Notes & Idempotency**:
   - Port ADF conversion and `append_note_once` into `src/symphony/trackers/jira.py`.

### Phase 3: Observability, Tooling & UX (P2)
1. **Observability**:
   - Port `issue_budget_snapshot` into `core.py` and expose `/api/v1/issues/<id>/budgets` in `webapi.py`.
2. **Doctor CLI**:
   - Add `_probe_agent_binary` with `--version` probe to `src/symphony/cli/doctor.py`.
3. **Dependency Graph & Process Safety**:
   - Port Kahn's algorithm and iterative DFS cycle detection into `src/symphony/trackers/validate.py`.
   - Port `except BaseException` subprocess reap into `src/symphony/backends/per_turn.py`.
4. **TUI & CLI Ergonomics**:
   - Port grouped `HelpScreen` modal into `src/symphony/tui/screens.py`.
   - Port subcommand listing and fast mistyped reject into `src/symphony/cli/main.py`.

---

## 6. Verification & Ledger Protocol

Per [SKILL.md](file:///Users/georg/git/workmateAI/thirdParty/oh-my-symphony/skills/symphony-upstream-sync/SKILL.md) and [integration-playbook.md](file:///Users/georg/git/workmateAI/thirdParty/oh-my-symphony/skills/symphony-upstream-sync/references/integration-playbook.md):

1. **Feature Branch Execution**:
   - Create a dedicated branch: `git switch -c feat/sync-upstream-v0.25-hardening develop`.
2. **Hermetic Gate Checks**:
   ```bash
   uv sync --locked
   uv lock --check
   uv run ruff check src tests
   uv run pyright
   uv run pytest
   uv run symphony doctor ./WORKFLOW.md
   git diff --check
   ```
3. **Authoritative Ledger Update**:
   - Update `docs/upstream-sync.md` documenting:
     - Range: `aaf2b72` .. `f69e7cd`.
     - Explicit dispositions for all 54 commits (`applied`, `adapted`, `partially deferred`).
     - Test evidence tallies, platform notes, and explicit waivers.
4. **Two-Stage Merge (M1 / M2)**:
   - When ready for full Git integration, execute M1 on feature branch, verify, and complete M2 into `develop` with `--no-ff`.
