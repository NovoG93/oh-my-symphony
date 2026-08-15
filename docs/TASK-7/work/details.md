# TASK-7 Work Details: Observability + Tooling for Named Agent Profiles

## Implementation Details

### 1. Database Migration & Run Records Persistence (PLAN §12)
- **Migration v9** (`src/symphony/orchestrator/migrations.py`):
  - Created `_migrate_009_agent_profile_columns` adding `agent_profile TEXT`, `model TEXT`, `reasoning_effort TEXT` to the `runs` table.
  - Bumped `RUN_AGENT_PROFILE_VERSION = 9` and registered in `MIGRATIONS`.
- **RunRecord & RunRegistry** (`src/symphony/orchestrator/run_registry.py`):
  - Added fields `agent_profile: str | None = None`, `model: str | None = None`, `reasoning_effort: str | None = None` to `RunRecord`.
  - Updated `acquire_run`, `acquire_continuation_run`, `_record`, `_run_summary`, and query functions to persist and query profile/model attributes.
- **Diagnostics & Orchestrator Core** (`src/symphony/orchestrator/diagnostics.py`, `entries.py`, `core.py`):
  - Added `agent_profile`, `model`, `reasoning_effort` to `_ALLOWED_FIELDS["run_acquired"]` in `diagnostics.py`.
  - Updated `RunningEntry` in `entries.py` to hold profile/model metadata.
  - Updated `_run_record_payload`, `_running_row`, `_try_acquire_run_lease`, and `_dispatch` in `core.py` to supply profile metadata upon run acquisition and stage routing.

### 2. Ticket-Level Profile Override (PLAN §13)
- **File Board Tracker** (`src/symphony/trackers/file.py`):
  - Updated `create`, `create_ticket`, `create_with_next_identifier`, `_new_ticket_front`, and `update_fields` to support `agent_profile`.
  - Added mutual exclusivity validation: setting both `agent_kind` and `agent_profile` raises `SymphonyError("ambiguous agent override: specify either agent_kind or agent_profile, not both")`.
  - Updated `record_agent_kind` to preserve pre-existing `agent_profile` without clobbering.

### 3. CLI Support for --agent-profile (PLAN §14)
- **Board CLI** (`src/symphony/cli/board.py`):
  - Added `--agent-profile` argument to `symphony board new` and `symphony board update`.
  - Added `--agent-kind` argument to `symphony board update` for parity.
  - Added validation against `cfg.agent_profiles`: warns or rejects unknown profiles when `WORKFLOW.md` is loaded.
  - Updated `symphony board show` to format `agent: profile=<profile>` when configured.

### 4. Symphony Doctor Preflight Checks (PLAN §15)
- **Doctor Check Function** (`src/symphony/cli/doctor.py`):
  - Implemented `check_agent_profiles(cfg: ServiceConfig) -> list[CheckResult]`.
  - Validates all defined profiles in `cfg.agent_profiles`:
    - Checks backend `kind` is supported in `SUPPORTED_AGENT_KINDS`.
    - Checks executable command binary exists on `$PATH` (WARN for command overrides, FAIL for missing binary).
    - Checks `model` syntax by rejecting whitespace/newlines; command must
      `shlex`-parse to a non-empty argv. (No regex exists in src; the
      `^[a-zA-Z0-9_.:/-]+$` claim in an earlier revision of this file was
      an overclaim — see `qa/static-review.md` LOW-3.)
  - Validates `cfg.agent.stage_profiles` references existing profiles in `cfg.agent_profiles`.
  - Validates `cfg.agent.default_profile` references existing profile in `cfg.agent_profiles`.
  - Registered `check_agent_profiles` in `run_checks()`.
