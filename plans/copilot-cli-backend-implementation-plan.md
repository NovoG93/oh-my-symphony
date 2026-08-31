# GitHub Copilot CLI Backend — Repository Cleanup & Implementation Plan

## 1. Target architecture

Make **GitHub Copilot CLI a first-class Symphony backend** and remove all Copilot-specific implementation from `pi.py`.

The target backend layout should be:

```text
src/symphony/backends/
├── __init__.py
├── per_turn.py
├── plain_cli.py
├── approval_policy.py
├── usage.py
│
├── codex.py
├── claude_code.py
├── gemini.py
├── agy.py
├── kiro.py
├── opencode.py
├── pi.py
├── prime_agent.py
└── copilot.py
       │
       ├── CopilotBackend
       ├── CopilotUsageProbe
       ├── Copilot JSONL parser
       └── Copilot quota/error normalization
```

The ownership rule should become:

```text
pi.py
  → only Pi behavior

prime_agent.py
  → only Prime Agent behavior

copilot.py
  → all GitHub Copilot behavior
```

The current inconsistency comes from having a Copilot usage probe inside `pi.py`, even though Pi is only one possible consumer of Copilot.

Use a single canonical name everywhere:

```yaml
kind: copilot
usage_pool: copilot
source: copilot
```

If existing deployed configuration already contains:

```yaml
source: github-copilot
```

keep it temporarily as a deprecated alias:

```python
USAGE_SOURCE_ALIASES = {
    "github-copilot": "copilot",
}
```

but remove `GithubCopilot...` implementation classes from the codebase.

---

# 2. Recommended implementation backend

Use the **native GitHub Copilot CLI** for agent execution rather than the Python Copilot SDK.

The CLI already provides the primitives Symphony needs:

```bash
copilot -p "prompt"
copilot --output-format=json
copilot --model ...
copilot --reasoning-effort ...
copilot --session-id <uuid>
copilot --resume ...
copilot --allow-all-tools
copilot --no-ask-user
copilot --add-dir ...
```

This maps closely to Symphony's existing `PerTurnCliBackend` abstraction.

Recommended architecture:

```text
Agent execution
    ↓
native Copilot CLI

Quota acquisition
    ↓
CopilotUsageProbe

No Python Copilot SDK dependency required
```

The Python SDK adds another lifecycle layer around the same CLI runtime and is not necessary for the initial integration.

---

# Stage 1 — Architecture cleanup

## 3. Create `src/symphony/backends/copilot.py`

Add a new backend:

```python
class CopilotBackend(PerTurnCliBackend):
    ...
```

Keep all Copilot-specific behavior here:

- CLI command construction
- JSONL parsing
- session handling
- final response extraction
- quota-exhaustion classification
- Copilot usage probing

Do not subclass Pi or OpenCode.

---

## 4. Remove Copilot code from `src/symphony/backends/pi.py`

Delete any Copilot-specific classes such as:

```python
class GithubCopilotUsageProbe(...):
    ...
```

and remove any probe registration such as:

```python
USAGE_PROBES["github-copilot"] = GithubCopilotUsageProbe
```

After this cleanup, `pi.py` should contain **zero Copilot-specific symbols**.

Pi should remain provider-agnostic.

---

## 5. Adapt `src/symphony/backends/usage.py`

Replace any lazy import from `pi.py` with:

```python
from .copilot import CopilotUsageProbe
```

Register:

```python
USAGE_PROBES["copilot"] = CopilotUsageProbe
```

Optionally normalize the legacy source:

```python
USAGE_SOURCE_ALIASES = {
    "github-copilot": "copilot",
}
```

before probe lookup. `get_usage_probe(source)` is a lazy `if`/`elif` import
chain (mirroring the backend factory) — keep that structure rather than
switching to a bare dict-get:

```python
def get_usage_probe(source: str) -> type[UsageProbe] | None:
    source = USAGE_SOURCE_ALIASES.get(source, source)  # normalize legacy alias

    if source == "codex" and "codex" not in USAGE_PROBES:
        from .codex import CodexUsageProbe
        USAGE_PROBES["codex"] = CodexUsageProbe
    # ... existing elif branches for claude/gemini/agy/kiro/opencode ...
    elif source == "copilot" and "copilot" not in USAGE_PROBES:
        from .copilot import CopilotUsageProbe
        USAGE_PROBES["copilot"] = CopilotUsageProbe

    return USAGE_PROBES.get(source)
```

The existing `github-copilot` branch (which today imports
`GithubCopilotUsageProbe` from `.pi`) is removed in favour of the alias above.

---

# Stage 2 — Copilot backend implementation

## 6. `CopilotBackend(PerTurnCliBackend)`

The backend should use the same lifecycle abstraction as the other per-turn CLI agents.

### Command construction

The actual `PerTurnCliBackend` hook returns a **command string** (not a list of
args), is keyword-only, and takes `is_continuation`:

```python
def _command_for_turn(self, *, prompt: str, is_continuation: bool) -> str:
    parts = [
        self._cfg.command,          # e.g. "copilot"
        "--output-format=json",
        "--no-ask-user",
        "--allow-all-tools",
    ]

    if self._cfg.model:
        parts += ["--model", self._cfg.model]

    if self._cfg.reasoning_effort:
        parts += ["--reasoning-effort", self._cfg.reasoning_effort]

    if self._cfg.resume_across_turns and self._session_id:
        parts += ["--session-id", self._session_id]

    for root in self._git_writable_roots:
        parts += ["--add-dir", root]

    parts += ["-p", prompt]

    return shlex.join(parts)
```

Copilot should receive the prompt using `-p` (not stdin), so:

```python
def _stdin_payload(self, prompt: str) -> str | None:
    return None
```

(Note the real signatures are `_command_for_turn(self, *, prompt: str,
is_continuation: bool) -> str` — keyword-only, returns `str`, includes
`is_continuation` — and `_stdin_payload` returns `str | None`, not
`bytes | None`.)

---

## 7. Permission handling

Do **not** default to:

```text
--allow-all
```

Instead use:

```text
--allow-all-tools
--no-ask-user
--add-dir <allowed root>
```

This allows autonomous tool execution while retaining Symphony's filesystem boundary.

Translate Symphony's writable-root configuration into repeated Copilot `--add-dir` arguments.

Example:

```text
SYMPHONY_GIT_WRITABLE_ROOTS
        ↓
CopilotBackend
        ↓
--add-dir /workspace/project
--add-dir /workspace/shared
```

---

## 8. Session handling

Copilot supports explicit UUID session IDs.

Have Symphony create and own the ID:

```python
self._session_id = str(uuid.uuid4())
```

First turn:

```bash
copilot   --session-id 76356ee1-...   -p "Implement the feature"
```

Next turn:

```bash
copilot   --session-id 76356ee1-...   -p "Review the changes"
```

This allows Symphony to preserve Copilot conversation context across workflow stages or turns.

Implement:

```python
resume_session(session_id: str)
```

to:

1. validate UUID format;
2. assign the existing ID;
3. reuse it on the next turn.

When:

```yaml
resume_across_turns: false
```

generate a fresh session for each turn.

---

## 9. Copilot JSONL parsing

Run Copilot using:

```text
--output-format=json
```

and parse output line-by-line (the format is JSONL: one JSON object per line).

Implement:

```python
def _parse_event(line: str) -> dict | None:
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None
```

### Verified event schema (v1.0.80, `-p` run)

Captured from a real `copilot --output-format=json -p "…"` run. Every event has
`type`, `data`, `id`, `timestamp`, and `parentId`; many also carry
`"ephemeral": true`.

| Event type | Authoritative field | Meaning |
|---|---|---|
| `session.mcp_servers_loaded` / `session.mcp_server_status_changed` / `session.skills_loaded` / `session.tools_updated` | — | session setup, ephemeral — ignore |
| `user.message` | `data.content`, `data.transformedContent` | prompt echo (the actual text sent to the model lives in `transformedContent`) |
| `assistant.turn_start` / `assistant.turn_end` | `data.turnId` | turn lifecycle |
| `assistant.message_start` | `data.messageId` | message begins (ephemeral) |
| `assistant.message_delta` | `data.deltaContent` | streaming text (note: `deltaContent`, **not** `content`) |
| `assistant.message` | `data.content` | **authoritative final response**; also `data.outputTokens`, `data.toolRequests`, `data.model`, `data.turnId` |
| `model.call_start` | `data.model`, `data.turnId` | model dispatch (ephemeral) |
| `session.usage_checkpoint` | `data.totalNanoAiu`, `data.totalPremiumRequests` | usage checkpoint |
| `result` | top-level `sessionId`, `exitCode`, `usage` | **authoritative completion signal** — parse this for exit code / session id / usage |
| `assistant.idle` | — | idle (ephemeral) |

Use the final `assistant.message`'s `data.content` as the authoritative answer:

```python
if event["type"] == "assistant.message":
    final_message = event["data"].get("content")
```

Treat the `result` event as the authoritative completion signal (its
`exitCode` is the process result, and `sessionId` is the session to reuse on a
later `--resume`).

`tool.*` events (tool activity) and `session.error` (backend error) are
expected for tool-using / failing turns but were **not captured** in this spike
(a trivial prompt produced no tool calls and no error) — validate them on a
real multi-tool turn before relying on their exact shapes.

Unknown event types must never crash the backend:

```text
Known event   → normalize
Unknown event → ignore or emit EVENT_OTHER_MESSAGE; never fail the parser
```

### Token telemetry

Token counts **are** partially available: `assistant.message` carries
`data.outputTokens`, and `result.usage` carries `premiumRequests`,
`totalApiDurationMs`, `sessionDurationMs`, and `codeChanges`. Do **not**
synthesize token counts from `premiumRequests` or `totalNanoAiu` (different
metrics). Surface `outputTokens` where present; leave input tokens (not
exposed in this sample) unknown/zero.

---

# Stage 3 — Add Copilot as a supported backend kind

## 10. `src/symphony/backends/__init__.py`

Add Copilot to the backend factory:

```python
if kind == "copilot":
    from .copilot import CopilotBackend

    return CopilotBackend(init)
```

Also update the unsupported-kind error message.

Do not refactor the entire backend factory into a registry as part of this feature unless desired separately.

---

## 11. `src/symphony/workflow/constants.py`

Add:

```python
SUPPORTED_AGENT_KINDS = (
    ...
    "copilot",
)
```

Add Copilot-supported profile fields:

```python
PROFILE_FIELDS_BY_KIND["copilot"] = {
    "model",
    "reasoning_effort",
    "command",
    "resume_across_turns",
    "turn_timeout_ms",
    "read_timeout_ms",
    "stall_timeout_ms",
    "usage_pool",
}
```

Add:

```python
DEFAULT_COPILOT_COMMAND = "copilot"
```

Do not put permission flags into the command string.

Permissions should remain controlled by `CopilotBackend`.

---

# Stage 4 — Workflow configuration

## 12. `src/symphony/workflow/config.py`

Add:

```python
@dataclass(frozen=True)
class CopilotConfig:
    command: str
    turn_timeout_ms: int
    read_timeout_ms: int
    stall_timeout_ms: int
    resume_across_turns: bool = True
    model: str = ""
    reasoning_effort: str = ""
```

Add to `ServiceConfig` as a **defaulted** field (matching `agy`/`kiro`/
`opencode`/`prime_agent`), NOT a required one — a required field would break
every existing config that lacks a `copilot:` section:

```python
copilot: CopilotConfig | None = None
```

Also extend any backend-timeout helper such as:

```python
backend_timeouts(...)
```

to support:

```text
copilot
```

---

## 13. `src/symphony/workflow/builder.py`

Parse configuration such as:

```yaml
copilot:
  command: copilot
  resume_across_turns: true
  model: ""
  reasoning_effort: high
```

into `CopilotConfig`.

The existing named-profile overlay system should continue to handle profile-specific overrides.

Example:

```yaml
agent_profiles:
  copilot-builder:
    kind: copilot
    model: claude-sonnet-4.5
    reasoning_effort: high

  copilot-reviewer:
    kind: copilot
    model: gpt-5.6
    reasoning_effort: high
```

---

## 14. `src/symphony/workflow/profiles.py`

Add:

```python
copilot: CopilotConfig | None
```

to the resolved agent configuration structure.

Extend backend config resolution:

```python
if kind == "copilot":
    return cfg.copilot
```

This allows named Copilot profiles to use the same existing workflow routing logic as the other agents.

---

# Stage 5 — Usage integration

## 15. Usage pool configuration

Copilot should use the same shared usage-pool architecture already deployed.

Example:

```yaml
usage_pools:
  copilot:
    source: copilot
    caps:
      monthly: 80

agent_profiles:
  copilot-builder:
    kind: copilot
    model: claude-sonnet-4.5

  copilot-reviewer:
    kind: copilot
    model: gpt-5.6
```

Both profiles share:

```text
usage_pool: copilot
```

implicitly when their kind is `copilot`.

---

## 16. `CopilotUsageProbe`

Place this inside:

```text
src/symphony/backends/copilot.py
```

not `pi.py`.

Add:

```python
class CopilotUsageProbe(UsageProbe):
    ...
```

Use two implementation levels:

1. **Stage A — runtime hard-limit detection** (primary): the *backend* parses
   the run's own output for quota exhaustion — see below.
2. **Stage B — authoritative quota probing** (secondary): the *probe* queries
   the CLI's internal RPC for remaining quota (§17).

### Stage A — runtime hard-limit detection

Immediately support runtime quota exhaustion, driven entirely by the run's own
output (no separate query).

Flow:

```text
Copilot reports plan/AI-credit exhaustion
        ↓
CopilotBackend._check_provider_exhaustion()
        ↓
PerTurnCliBackend
        ↓
EVENT_PROVIDER_USAGE_EXHAUSTED
        ↓
ProviderCapacityError
        ↓
Copilot usage pool blocked
```

Do not classify every generic:

```text
429
rate limit
too many requests
```

as account exhaustion.

Only strong quota/plan exhaustion signals should mark the usage pool unavailable.

---

## 17. Authoritative quota probing

Copilot CLI exposes an **undocumented but stable-in-practice** JSON-RPC server
mode (verified on v1.0.80). It is absent from `copilot --help` and GitHub does
not contract it to third parties — but it is the CLI's *own internal protocol*
(the interactive TUI talks to the Copilot core through it), so it is the only
machine-readable source of authoritative quota and is expected to survive
routine CLI updates. The *supported* public agent protocol, `--acp`, exposes no
quota method.

```bash
copilot --server --stdio --no-auto-update --log-level error
```

It speaks LSP-framed JSON-RPC (`Content-Length` header + JSON body). No
`initialize` handshake is needed — `account.getQuota` can be called directly
(an `initialize` request returns `-32601 Unhandled method initialize`).

Verified authenticated response (v1.0.80):

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "quotaSnapshots": {
      "chat":                { "isUnlimitedEntitlement": true,  "entitlementRequests": 0,    "usedRequests": 0,  "remainingPercentage": 100,  "resetDate": "…", "hasQuota": true, "tokenBasedBilling": true },
      "completions":         { "isUnlimitedEntitlement": true,  "entitlementRequests": 0,    "usedRequests": 0,  "remainingPercentage": 100,  "resetDate": "…", "hasQuota": true, "tokenBasedBilling": true },
      "premium_interactions": { "isUnlimitedEntitlement": false, "entitlementRequests": 1500, "usedRequests": 74, "remainingPercentage": 95.1, "resetDate": "…", "hasQuota": true, "tokenBasedBilling": true }
    }
  }
}
```

The meaningful bucket for agent work is `premium_interactions` (the "premium
requests" / AI-credit quota); `chat` and `completions` are unlimited.

Conceptual flow:

```text
CopilotUsageProbe
      ↓
copilot --server --stdio
      ↓
account.getQuota
      ↓
result.quotaSnapshots.premium_interactions.remainingPercentage
      ↓
used_percent = 100 - remainingPercentage
      ↓
ProviderUsageSnapshot
```

Normalize:

```python
premium = snapshot["quotaSnapshots"]["premium_interactions"]
used_percent = 100.0 - premium["remainingPercentage"]
```

Represent Copilot's quota as a generic usage window keyed by the bucket (e.g.
`monthly` for `premium_interactions`), rather than forcing it into Codex-style
five-hour/weekly semantics.

If quota RPC parsing fails (or the mode disappears in a future CLI version):

```python
return None
```

and Symphony's existing **fail-open** behavior applies.

Keep the RPC implementation isolated entirely inside `CopilotUsageProbe` so scheduler logic stays provider-agnostic.

---

## 18. Reset handling

The `account.getQuota` response carries a per-bucket `resetDate`, and the
observed value was a **short rolling window, not a calendar month** — so do
**not** assume a monthly cycle. Prefer the provider-returned `resetDate` (parse
it into the `ProviderUsageSnapshot` window), and only fall back to a local
estimate when it is absent or unparseable:

```python
def next_month_first_day_utc(now: datetime) -> datetime:  # fallback only
    ...
```

---

# Stage 6 — Doctor checks

## 19. `src/symphony/cli/doctor.py`

Add a separate Copilot check:

```python
check_copilot_auth(...)
```

Do not reuse Pi authentication checks.

Doctor should:

1. verify `copilot` is installed;
2. detect authentication environment variables;
3. recognize existing Copilot login state;
4. avoid printing stored credentials;
5. verify required writable directories can be supplied via `--add-dir`.

Recognize token-based authentication such as:

```text
COPILOT_GITHUB_TOKEN
GH_TOKEN
GITHUB_TOKEN
```

where supported.

Keep:

```text
check_pi_auth()
check_prime_agent_auth()
check_copilot_auth()
```

as separate concerns.

---

# Stage 7 — Web/API/chat integration

## 20. Backend availability

Wherever the repo exposes:

```text
SUPPORTED_AGENT_KINDS
```

Copilot should automatically become available once it is added to the shared constant.

Check:

- workflow API
- agent profile API
- chat agent selector
- scheduler/profile validation
- web UI backend lists

---

## 21. `src/symphony/chat.py`

Optionally add a Copilot event summarizer:

```python
if agent_kind == "copilot":
    return _summarize_copilot_frame(payload)
```

Normalize:

```text
assistant.message
assistant.message_delta
tool.*
session.error
```

Keep the final `assistant.message` as authoritative.

---

# Stage 8 — Documentation cleanup

Update repo-wide references that hardcode the existing backend set.

At minimum review:

```text
README.md
pyproject.toml
docs/features/agent-profiles.md
WORKFLOW.file.example.md
backend docstrings
installation instructions
supported-agent lists
```

Avoid documentation such as:

```text
Supports eight coding agents
```

Prefer:

```text
Supports multiple coding-agent backends
```

so future backend additions do not require updating a hardcoded count.

---

# Stage 9 — Tests

## 22. Backend/factory tests

Add:

```python
def test_build_backend_returns_copilot_backend():
    ...
```

Ensure Copilot participates in generic `AgentBackend` contract tests.

---

## 23. Command construction tests

Add:

```python
def test_copilot_prompt_is_passed_with_p_flag():
    ...

def test_copilot_json_output_is_enabled():
    ...

def test_copilot_model_is_forwarded():
    ...

def test_copilot_reasoning_effort_is_forwarded():
    ...

def test_copilot_no_ask_user_is_enabled():
    ...

def test_copilot_allow_all_tools_is_enabled():
    ...

def test_writable_roots_become_add_dir_flags():
    ...
```

---

## 24. Session tests

Add:

```python
def test_copilot_first_session_gets_uuid():
    ...

def test_consecutive_turns_reuse_session_id():
    ...

def test_resume_session_uses_existing_uuid():
    ...

def test_invalid_resume_session_uuid_is_rejected():
    ...

def test_resume_across_turns_false_creates_new_session():
    ...
```

---

## 25. JSONL parser tests

Add:

```python
def test_assistant_message_becomes_final_output():
    ...

def test_malformed_json_line_does_not_crash_worker():
    ...

def test_unknown_event_is_tolerated():
    ...

def test_session_error_fails_turn():
    ...

def test_final_message_is_not_duplicated_from_deltas():
    ...
```

---

## 26. Usage tests

Add:

```python
def test_copilot_usage_probe_lives_in_copilot_module():
    ...

def test_pi_module_contains_no_copilot_symbols():
    ...

def test_source_copilot_resolves_copilot_usage_probe():
    ...

def test_legacy_github_copilot_alias_resolves_if_supported():
    ...

def test_copilot_quota_probe_failure_fails_open():
    ...

def test_remaining_percentage_converts_to_used_percentage():
    ...

def test_monthly_reset_is_calculated_correctly():
    ...
```

---

## 27. Capacity tests

Add:

```python
def test_genuine_copilot_credit_exhaustion_emits_provider_usage_exhausted():
    ...

def test_generic_rate_limit_does_not_mark_plan_exhausted():
    ...

def test_exhausted_copilot_pool_blocks_all_copilot_profiles():
    ...

def test_configured_copilot_cap_blocks_new_dispatch():
    ...

def test_running_copilot_worker_is_not_cancelled_when_cap_crossed():
    ...
```

---

## 28. Configuration tests

Add:

```python
def test_kind_copilot_is_accepted():
    ...

def test_copilot_profile_overrides_model():
    ...

def test_copilot_profile_overrides_reasoning_effort():
    ...

def test_copilot_profile_can_reference_usage_pool():
    ...

def test_invalid_copilot_profile_fields_are_rejected():
    ...
```

---

## 29. Doctor/API/UI tests

Add:

```python
def test_doctor_detects_copilot_binary():
    ...

def test_doctor_handles_copilot_auth_independently_from_pi():
    ...

def test_workflow_api_exposes_copilot_supported_kind():
    ...

def test_chat_agent_selector_contains_copilot():
    ...
```

Update any existing tests that import:

```python
GithubCopilotUsageProbe
```

from `pi.py`.

They should instead import:

```python
CopilotUsageProbe
```

from:

```text
symphony.backends.copilot
```

---

# Stage 10 — Recommended implementation sequence

Implement in four compact phases.

## Phase 1 — Architecture cleanup

1. Create `copilot.py`.
2. Move/remove every Copilot symbol from `pi.py`.
3. Rename canonical usage source to `copilot`.
4. Keep optional `github-copilot` alias for compatibility.
5. Add `copilot` to constants, config, factory, profiles, and doctor surfaces.

## Phase 2 — Working agent backend

1. Implement `CopilotBackend(PerTurnCliBackend)`.
2. Add `-p` prompt execution.
3. Add JSON output parsing.
4. Add session UUID handling.
5. Add model and reasoning-effort forwarding.
6. Add autonomous tool permissions.
7. Translate Symphony writable roots into `--add-dir`.

At the end of this phase, Copilot should already work as:

```text
planner
implementer
reviewer
```

inside Symphony workflows.

## Phase 3 — Usage awareness

1. Add runtime Copilot quota-exhaustion detection.
2. Implement isolated `CopilotUsageProbe`.
3. Use the CLI's internal RPC (`--server --stdio` + `account.getQuota`) for quota data.
4. Normalize remaining percentage to used percentage.
5. Prefer the provider `resetDate`; compute a local fallback where absent.
6. Preserve fail-open behavior on probe failure.

## Phase 4 — UI/docs/tests

1. Expose Copilot in supported backend lists.
2. Surface Copilot usage in existing provider-usage UI.
3. Add chat/event normalization.
4. Update README and example workflow configuration.
5. Add backend, usage, scheduler, doctor, API, and UI tests.

---

# Final configuration example

The resulting configuration should look like:

```yaml
copilot:
  command: copilot
  resume_across_turns: true

usage_pools:
  copilot:
    source: copilot
    caps:
      monthly: 80

agent_profiles:
  copilot-builder:
    kind: copilot
    model: claude-sonnet-4.5
    reasoning_effort: high

  copilot-reviewer:
    kind: copilot
    model: gpt-5.6
    reasoning_effort: high
```

Architecturally:

```text
copilot-builder ───┐
                   ├── CopilotBackend
copilot-reviewer ──┘        │
                            │
                            └── usage_pool: copilot
                                      │
                                      ▼
                               CopilotUsageProbe
```

The final design rule is:

> **Copilot is an independent Symphony backend. Pi stays provider-agnostic. Copilot quota belongs to the shared Copilot usage pool, and the native Copilot CLI is the execution engine.**
