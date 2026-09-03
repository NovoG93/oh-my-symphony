# Usage-Aware Agent Profiles — Implementation Plan

## 1. Global implementation idea

Usage must be modeled **per shared usage pool/provider quota**, not per named agent profile.

Multiple profiles such as `codex-builder`, `codex-reviewer`, and `codex-planner` can consume the same Codex subscription quota, so they must share one quota state and one configured cap.

The profile system should remain concerned with **how an agent runs** (model, reasoning effort, command, timeouts, etc.), while usage pools define **whether that provider/account is currently allowed to start new work**.

The high-level model is:

```text
Agent Profile
      │
      │ resolves
      ▼
Usage Pool
      │
      ├── usage source / provider
      ├── configured caps
      └── runtime quota snapshot
              │
              ├── actual used %
              ├── remaining %
              └── reset time
      │
      ▼
Scheduler eligibility
```

For dedicated backends, the usage pool can default to the backend kind:

```text
codex  → codex
claude → claude
gemini → gemini
agy    → agy
kiro   → kiro
```

For multiplexing backends such as:

```text
opencode
pi
prime-agent
```

the profile should be able to explicitly select a shared usage pool.

### Example configuration

```yaml
usage_pools:
  codex:
    source: codex
    caps:
      five_hour: 80
      weekly: 70

  claude:
    source: claude
    caps:
      five_hour: 80
      weekly: 70

  gemini:
    source: gemini
    caps:
      daily: 80

  kiro:
    source: kiro
    caps:
      monthly: 85

agent_profiles:
  codex-builder:
    kind: codex
    model: gpt-5.6
    # usage_pool defaults to "codex"

  codex-reviewer:
    kind: codex
    model: gpt-5.6
    # shares the same "codex" usage pool

  claude-reviewer:
    kind: claude
    model: sonnet
    # usage_pool defaults to "claude"

  pi-codex:
    kind: pi
    usage_pool: codex

  pi-copilot:
    kind: pi
    usage_pool: github-copilot
```

### Core scheduling behavior

```text
No usage pool/policy configured
        ↓
normal scheduling

Usage pool configured
        ↓
obtain cached/provider usage
        ↓
usage unavailable
        ↓
FAIL OPEN → normal scheduling

usage available
        ↓
provider hard-limit reached?
        ├── yes → WAIT_PROVIDER_USAGE
        │
        └── no
             ↓
configured cap reached?
        ├── yes → WAIT_PROVIDER_USAGE
        └── no  → READY
```

Running workers must **never be interrupted** merely because a configured cap is crossed.

Configured caps only prevent **new dispatches**.

If the provider itself terminates a running task because its quota is exhausted, that should be classified as a provider-capacity wait rather than a normal retry failure.

---

# Stage 1 — Configuration and normalized usage model

## `src/symphony/workflow/config.py`

Add a generic usage-pool model:

```python
@dataclass(frozen=True)
class UsagePoolConfig:
    source: str
    caps: dict[str, float]
```

Add usage pools to the service configuration:

```python
usage_pools: dict[str, UsagePoolConfig] = field(default_factory=dict)
```

Add an optional usage-pool reference to profiles:

```python
@dataclass(frozen=True)
class AgentProfileConfig:
    ...
    usage_pool: str | None = None
```

The profile does **not** contain cap values. It only identifies which shared quota pool it consumes.

For dedicated backends:

```python
usage_pool_id = profile.usage_pool or profile.kind
```

This allows all `kind: codex` profiles to share the same subscription quota by default while still supporting future setups such as:

```yaml
usage_pools:
  codex-personal:
    source: codex
    caps:
      five_hour: 80
      weekly: 70

  codex-work:
    source: codex
    caps:
      five_hour: 90
      weekly: 90
```

---

## `src/symphony/workflow/builder.py`

Add validation for `usage_pools`.

Validation rules:

```text
usage_pools must be a mapping

usage_pools.<name>.source:
    required
    string
    known usage source or explicitly supported future source

usage_pools.<name>.caps:
    mapping

usage_pools.<name>.caps.<window>:
    numeric
    0 < value <= 100
```

Examples of valid window names:

```text
five_hour
weekly
daily
monthly
```

Do not hardcode scheduler logic to only 5-hour and weekly windows.

Validate `agent_profiles.<name>.usage_pool` when explicitly configured.

For `opencode`, `pi`, and `prime-agent`, explicit `usage_pool` should be supported because these backends can front multiple providers.

---

## New: `src/symphony/backends/usage.py`

Add provider-independent normalized quota types:

```python
@dataclass(frozen=True)
class UsageWindow:
    key: str
    used_percent: float | None
    remaining_percent: float | None
    resets_at: datetime | None


@dataclass(frozen=True)
class ProviderUsageSnapshot:
    pool_id: str
    source: str
    windows: dict[str, UsageWindow]

    hard_limit_reached: bool = False

    # Only authoritative telemetry may block scheduling.
    authoritative: bool = True

    observed_at: datetime | None = None
    stale: bool = False
```

Add:

```python
class UsageProbe(Protocol):
    async def fetch_usage(self) -> ProviderUsageSnapshot | None:
        ...
```

Add a probe registry:

```python
USAGE_PROBES = {
    "codex": CodexUsageProbe,
    "claude": ClaudeUsageProbe,
    "agy": AgyUsageProbe,
    "gemini": GeminiUsageProbe,
    "kiro": KiroUsageProbe,
    "opencode-go": OpenCodeGoUsageProbe,
    "github-copilot": GithubCopilotUsageProbe,
}
```

Missing or unsupported probes are valid:

```python
probe = registry.get(pool.source)

if probe is None:
    return None  # fail open
```

### Important rule

Only authoritative quota information may block dispatch:

```text
authoritative=True
    → configured cap may block scheduling

authoritative=False
    → UI may show estimate
    → MUST NOT block scheduling
```

This prevents local token estimates from becoming scheduler-blocking data when they do not represent the provider's real rolling quota.

---

# Stage 2 — Provider/backend integrations

## 2.1 Codex — full authoritative support

**Support level: HIGH**

Codex is the cleanest implementation.

Codex App Server exposes:

```text
account/rateLimits/read
account/rateLimits/updated
```

with fields such as:

```text
usedPercent
windowDurationMins
resetsAt
rateLimitReachedType
```

The existing Symphony Codex backend already uses the App Server model and already consumes rate-limit update notifications.

### `src/symphony/backends/codex.py`

Add:

```python
class CodexUsageProbe(UsageProbe):
    async def fetch_usage(self) -> ProviderUsageSnapshot:
        ...
```

Call:

```text
account/rateLimits/read
```

Normalize windows by duration rather than by `primary`/`secondary` position:

```python
window_key = {
    300: "five_hour",
    10080: "weekly",
}.get(window_duration, f"{window_duration}_minutes")
```

Do not assume that `primary == five_hour` and `secondary == weekly`.

Subsequent:

```text
account/rateLimits/updated
```

notifications should update the shared cache immediately.

Also distinguish ChatGPT subscription authentication from API-key Codex usage. Subscription quota limits should only apply when the backend is using the subscription-backed account.

### Result

```text
Preflight percentage     YES
Reset timestamp          YES
Hard-limit detection     YES
Scheduler cap            YES
Polling                  YES
```

---

## 2.2 Claude Code — passive/cached subscription telemetry

**Support level: MEDIUM**

Claude Code exposes subscription fields such as:

```json
{
  "rate_limits": {
    "five_hour": {
      "used_percentage": 42,
      "resets_at": 1234567890
    },
    "seven_day": {
      "used_percentage": 61,
      "resets_at": 1234567890
    }
  }
}
```

The Symphony backend runs Claude using headless/print mode, so the implementation should avoid relying on undocumented endpoints or dummy quota-consuming requests.

### `src/symphony/backends/claude_code.py`

Add:

```python
class ClaudeUsageProbe(UsageProbe):
    ...
```

Initially treat this as a passive/cached probe.

Normalize:

```text
five_hour → five_hour
seven_day → weekly
```

Behavior:

```text
known cached authoritative quota
    → enforce cap

no reliable quota snapshot yet
    → fail open

explicit "usage limit reached"
    → mark provider exhausted
    → wait until known reset
```

Do not build the feature around an undocumented Anthropic endpoint.

If Anthropic later adds a documented headless quota command/API, only `ClaudeUsageProbe` should need to change.

### Result

```text
Preflight percentage     PARTIAL
Cached percentage        YES
Reset timestamp          YES when telemetry exists
Hard-limit detection     YES
Scheduler cap            YES when authoritative snapshot exists
Cold-start behavior      FAIL OPEN
```

---

## 2.3 AGY / Antigravity — authoritative support

**Support level: HIGH**

AGY can expose usage/quota information through read-only commands such as:

```bash
agy -p "/usage"
agy -p "/quota"
```

with structured output.

### `src/symphony/backends/agy.py`

Add:

```python
class AgyUsageProbe(UsageProbe):
    async def fetch_usage(self):
        result = await run(
            ["agy", "-p", "/quota", "--output-format", "json"]
        )
        return normalize_agy_usage(result)
```

Preserve provider/model-specific buckets rather than forcing them into a fabricated 5-hour/weekly structure.

### Result

```text
Preflight percentage     YES
Reset information        YES where returned
Hard-limit detection     YES
Scheduler cap            YES
Safe polling             YES
```

---

## 2.4 Gemini CLI — partial support

**Support level: MEDIUM/LOW**

Gemini CLI exposes quota information interactively, but there is not currently a sufficiently stable machine-readable headless quota source for scheduler-critical polling.

### `src/symphony/backends/gemini.py`

Add:

```python
class GeminiUsageProbe(UsageProbe):
    ...
```

Initially return `None` unless a documented authoritative machine-readable quota source is available.

Do **not** make a pseudo-TTY scraper of interactive `/stats` output part of normal scheduling logic.

Instead:

1. parse quota-exhaustion errors from normal Gemini runs;
2. extract reset information where available;
3. mark the affected Gemini pool as exhausted;
4. fail open when percentage telemetry is unavailable.

Gemini quota may be model-oriented and daily rather than 5-hour/weekly, which is why quota windows must be generic.

### Result

```text
Preflight percentage     NOT RELIABLY
Hard-limit detection     YES
Reset extraction         BEST EFFORT
Scheduler cap            NO until authoritative telemetry
Fail open                YES
```

---

## 2.5 Kiro — credit-based support

**Support level: MEDIUM/LOW**

Kiro uses credits rather than a fixed five-hour/weekly quota model.

Its usage can conceptually be normalized into a monthly/credit usage window:

```text
monthly used % =
    used credits / total available credits * 100
```

### `src/symphony/backends/kiro.py`

Add:

```python
class KiroUsageProbe(UsageProbe):
    ...
```

Do not scrape interactive terminal output by default.

Until a supported programmatic quota endpoint is available:

```text
usage percentage unavailable
        → fail open

normal Kiro run reports credit exhaustion
        → mark hard_limit_reached
        → scheduler waits
```

### Result

```text
Preflight percentage     NOT OFFICIALLY SCRIPTABLE
Hard-limit detection     YES
Scheduler cap            NO until authoritative telemetry
Quota type               MONTHLY/CREDITS
```

---

## 2.6 OpenCode — provider-aware delegation

**Support level: depends on selected provider**

OpenCode itself is a provider multiplexer.

Therefore:

```text
kind: opencode
```

must not automatically imply:

```text
usage_pool: opencode
```

### External provider example

```yaml
agent_profiles:
  opencode-codex:
    kind: opencode
    usage_pool: codex
```

That profile delegates quota checks to `CodexUsageProbe`.

### OpenCode Go

OpenCode Go has its own subscription limits, but local OpenCode stats should not be treated as authoritative provider quota.

Any locally estimated usage should be represented as:

```python
authoritative=False
```

and must never block scheduling.

Until an authoritative OpenCode Go quota API exists:

```text
local usage estimate
      ↓
UI only
      ↓
NEVER scheduler blocking
```

### Result

```text
OpenCode + supported provider     delegate to provider probe
OpenCode Go percentage            unavailable authoritatively
OpenCode Go hard limit            detect from runtime error
Local usage estimate              UI only
```

---

## 2.7 Pi — usage-pool delegation

**Support level: depends on selected provider**

Pi supports multiple providers and subscription authentication mechanisms.

The current Symphony Pi backend receives token/cost data but not authoritative provider quota information.

Profiles therefore explicitly bind to a usage pool:

```yaml
agent_profiles:
  pi-codex:
    kind: pi
    usage_pool: codex

  pi-copilot:
    kind: pi
    usage_pool: github-copilot
```

### Pi + Codex

A stronger future integration is possible by obtaining Pi's OpenAI/Codex OAuth bearer token and using Codex App Server's externally managed token mode.

Potential flow:

```text
Pi
 │
 ├─ obtain/refresh Codex OAuth token
 │
 ▼
temporary Codex App Server
 │
 └─ account/rateLimits/read
        │
        ▼
shared codex usage snapshot
```

This should be implemented only after the regular Codex probe is stable.

### Pi + Claude

Do not assume Claude Code-specific status-line telemetry can be reused. Pi talks directly to providers and does not automatically expose Claude Code's subscription quota data.

Initially:

```text
Pi + Claude
→ percentage unknown
→ hard-limit detection only
→ fail open
```

### Pi + GitHub Copilot

Treat as:

```text
hard-limit detection
percentage unknown
fail open
```

until a suitable user-level real-time quota API is available.

---

## 2.8 Prime Agent — same delegation architecture as Pi

**Support level: depends on selected provider**

`PrimeAgentBackend` already follows the same JSON event model as Pi, so usage-pool resolution should be shared.

Example:

```yaml
agent_profiles:
  prime-codex:
    kind: prime-agent
    usage_pool: codex

  prime-copilot:
    kind: prime-agent
    usage_pool: github-copilot
```

The usage pool represents the actual billing/quota source, not simply the model vendor.

For provider combinations where Prime Agent does not consume the normal provider subscription quota, do not bind them to that subscription pool.

---

## Backend support summary

| Symphony backend | Authoritative preflight | Recommended source | Scheduler cap now |
|---|---:|---|---:|
| `codex` | Yes | Codex App Server | **Yes** |
| `claude` | Partial/cached | Claude subscription telemetry | **Yes, when known** |
| `agy` | Yes | read-only AGY quota command | **Yes** |
| `gemini` | No stable headless source | Runtime errors / future CLI API | **No** |
| `kiro` | No stable headless source | Runtime errors / future usage API | **No** |
| `opencode` | Provider-dependent | Delegate to bound usage pool | **Provider-dependent** |
| `pi` | Provider-dependent | Delegate to bound usage pool | **Provider-dependent** |
| `prime-agent` | Provider-dependent | Delegate to bound usage pool | **Provider-dependent** |

Recommended backend implementation order:

```text
1. Codex
2. AGY
3. Claude
4. Provider delegation for OpenCode / Pi / Prime Agent
5. Gemini hard-limit detection
6. Kiro hard-limit detection
7. OpenCode Go once an authoritative usage API exists
```

---

# Stage 3 — Shared usage manager and scheduler integration

## New: `src/symphony/orchestrator/usage.py`

Keep cache/polling out of the already large orchestrator core.

Add:

```python
class ProviderUsageManager:
    snapshots: dict[str, ProviderUsageSnapshot]

    async def refresh(self, pool_id: str) -> None:
        ...

    def snapshot(self, pool_id: str) -> ProviderUsageSnapshot | None:
        ...

    def evaluate(
        self,
        pool_id: str,
        pool: UsagePoolConfig,
    ) -> UsageDecision:
        ...
```

Use a short cache TTL, e.g. approximately 60 seconds.

Behavior:

```text
successful probe
    → cache normalized result

probe failure
    → retain last-known telemetry for UI
    → mark stale

no usable telemetry
    → fail open

reset_at has passed + refresh fails
    → fail open
```

The last rule prevents an old blocking snapshot from preventing work forever after its reset.

### Evaluation logic

```python
if snapshot is None:
    return READY

if snapshot.stale:
    return READY

if not snapshot.authoritative:
    return READY

if snapshot.hard_limit_reached:
    return WAIT_PROVIDER_USAGE

for window, cap in pool.caps.items():
    actual = snapshot.windows.get(window)

    if actual and actual.used_percent is not None:
        if actual.used_percent >= cap:
            return WAIT_PROVIDER_USAGE

return READY
```

---

## `src/symphony/orchestrator/core.py`

Replace the current single global rate-limit state with provider/usage-pool-aware state.

Add:

```python
self._usage_manager = ProviderUsageManager(...)
```

Add:

```python
def _eligibility_usage_decision(
    self,
    issue: Issue,
    cfg: ServiceConfig,
) -> _EligibilityDecision | None:
    ...
```

Resolve the ticket's actual agent profile and usage pool using the same agent-selection logic used for dispatch.

Recommended eligibility order:

```text
ownership
    ↓
contract
    ↓
usage
    ↓
contention
```

Return:

```python
_EligibilityDecision(
    _EligibilityDisposition.WAIT_NON_SLOT,
    "waiting_provider_usage",
    "codex weekly usage cap reached; resets at ..."
)
```

Do not use the operator pause mechanism.

Quota waits are derived scheduler state and should disappear automatically when capacity returns.

### Re-dispatch behavior

```text
waiting_provider_usage
        ↓
scheduler tick
        ↓
usage manager refreshes when needed
        ↓
below cap?
        ├─ no  → remain waiting
        └─ yes → READY → normal dispatch
```

No persistent manual pause state is required.

---

# Stage 4 — Runtime hard-limit handling

## `src/symphony/backends/__init__.py`

Add a normalized event:

```python
EVENT_PROVIDER_USAGE_EXHAUSTED = "provider_usage_exhausted"
```

Optionally introduce:

```python
@dataclass
class ProviderCapacityError:
    pool_id: str
    resets_at: datetime | None
```

---

## Backend implementations

When a provider explicitly reports quota exhaustion during a running task:

```text
EVENT_PROVIDER_USAGE_EXHAUSTED
```

should be emitted.

Do not emit this for generic transient `429`, RPM, or networking errors unless they genuinely represent subscription/plan exhaustion.

---

## `src/symphony/orchestrator/core.py`

Handle provider exhaustion separately from generic retries:

```text
provider quota exhaustion
        ↓
current attempt terminates
        ↓
update shared provider snapshot
        ↓
DO NOT consume ordinary retry budget
        ↓
ticket returns to scheduler
        ↓
waiting_provider_usage
```

Configured caps themselves must never cancel an already-running worker.

---

# Stage 5 — API and UI

## `src/symphony/orchestrator/core.py`

Replace the current single rate-limit representation with usage-pool-aware runtime data:

```json
{
  "provider_usage": {
    "codex": {
      "source": "codex",
      "windows": {
        "five_hour": {
          "used_percent": 63,
          "remaining_percent": 37,
          "resets_at": "..."
        },
        "weekly": {
          "used_percent": 51,
          "remaining_percent": 49,
          "resets_at": "..."
        }
      },
      "status": "available",
      "stale": false,
      "authoritative": true
    }
  }
}
```

---

## `src/symphony/webapi.py`

Extend the workflow payload with configured pools:

```json
{
  "usage_pools": {
    "codex": {
      "source": "codex",
      "caps": {
        "five_hour": 80,
        "weekly": 70
      }
    }
  }
}
```

---

## `src/symphony/web/static/app.js`

Add a **Provider Usage** card near the existing Agent Policy area.

Example:

```text
Provider Usage

Codex                         Available

5 hour
████████████░░░░░░   63% used
37% remaining
Configured cap: 80%
Resets in 2h 14m

Weekly
██████████░░░░░░░░   51% used
49% remaining
Configured cap: 70%
Resets Thu 14:00
```

When blocked:

```text
Codex                     Capacity paused

Weekly      71% used
Configured cap: 70%

New Codex tasks paused
Available after: Thu 14:00
```

Add:

```javascript
waiting_provider_usage: t('schedule.reasonProviderUsage')
```

to the schedule reason map.

For wrapper profiles, optionally show which pool they consume:

```text
pi-codex
Backend: Pi
Usage pool: Codex
Status: Available
```

---

## `src/symphony/web/static/i18n.js`

Add labels for:

```text
Provider Usage
Usage pool
5-hour usage
Weekly usage
Daily usage
Monthly usage
Remaining
Configured cap
Usage unavailable
Usage data stale
Waiting for provider capacity
Resets at
Estimated usage
Authoritative usage
```

---

## `src/symphony/web/static/style.css`

Add only lightweight styling for:

```text
usage bars
status badges
stale/estimated indicators
```

No new page is necessary for the initial feature.

---

# Stage 6 — Tests

## 6.1 Configuration tests

Create or extend:

```text
tests/test_usage_limits.py
tests/test_workflow_agent_profiles.py
```

### Shared pool between profiles

```python
def test_usage_limit_is_shared_by_profiles_of_same_kind():
    cfg = load("""
    usage_pools:
      codex:
        source: codex
        caps:
          five_hour: 80
          weekly: 70

    agent_profiles:
      builder:
        kind: codex

      reviewer:
        kind: codex
    """)

    assert cfg.usage_pools["codex"].caps["weekly"] == 70
    assert cfg.agent_profiles["builder"].usage_pool is None
    assert cfg.agent_profiles["reviewer"].usage_pool is None
```

### Wrapper binding

```python
def test_pi_profile_can_explicitly_share_codex_pool():
    cfg = load("""
    usage_pools:
      codex:
        source: codex
        caps:
          weekly: 70

    agent_profiles:
      pi-builder:
        kind: pi
        usage_pool: codex
    """)

    assert cfg.agent_profiles["pi-builder"].usage_pool == "codex"
```

### Validation

```python
@pytest.mark.parametrize("value", [-1, 0, 101, "80"])
def test_usage_cap_rejects_invalid_percent(value):
    ...
```

Also add:

```python
test_unknown_usage_pool_reference_is_rejected()
test_missing_usage_pools_is_backward_compatible()
test_partial_usage_policy_is_valid()
test_generic_daily_window_is_valid()
test_generic_monthly_window_is_valid()
```

---

## 6.2 Generic usage-pool tests

### Same pool blocks all consumers

```python
def test_profiles_with_same_usage_pool_share_limit():
    builder = profile(kind="codex")
    reviewer = profile(kind="codex")

    usage["codex"].windows["weekly"].used_percent = 80

    assert blocked(builder)
    assert blocked(reviewer)
```

### Independent pools

```python
def test_pi_copilot_is_not_blocked_by_codex_limit():
    codex.used_percent = 100

    profile = AgentProfileConfig(
        kind="pi",
        usage_pool="github-copilot",
    )

    assert not blocked(profile)
```

### Generic windows

```python
@pytest.mark.parametrize(
    ("window", "used", "cap"),
    [
        ("five_hour", 80, 80),
        ("weekly", 70, 70),
        ("daily", 90, 80),
        ("monthly", 95, 90),
    ],
)
def test_any_configured_window_can_block(window, used, cap):
    ...
```

### Authoritative-only blocking

```python
def test_estimated_usage_never_blocks_scheduler():
    snapshot = ProviderUsageSnapshot(
        pool_id="opencode-go",
        source="local-estimate",
        authoritative=False,
        windows={
            "weekly": UsageWindow(
                key="weekly",
                used_percent=99,
                remaining_percent=1,
                resets_at=None,
            )
        },
    )

    assert eligibility(snapshot).code == "ready"
```

---

## 6.3 Codex tests

```python
def test_codex_normalizes_five_hour_window():
    raw = {
        "primary": {
            "usedPercent": 61,
            "windowDurationMins": 300,
            "resetsAt": 12345,
        }
    }

    result = normalize_codex_rate_limits(raw)

    assert result.windows["five_hour"].used_percent == 61
```

Important ordering test:

```python
def test_codex_detects_windows_by_duration_not_position():
    raw = {
        "primary": {
            "usedPercent": 40,
            "windowDurationMins": 10080,
        },
        "secondary": {
            "usedPercent": 20,
            "windowDurationMins": 300,
        },
    }

    result = normalize_codex_rate_limits(raw)

    assert result.windows["weekly"].used_percent == 40
    assert result.windows["five_hour"].used_percent == 20
```

Also:

```python
test_codex_rate_limits_read_normalization()
test_codex_multiple_limit_ids_are_preserved()
test_codex_updated_notification_updates_shared_pool()
test_codex_unknown_window_is_preserved_or_ignored_safely()
test_codex_hard_limit_reached_is_normalized()
test_codex_api_key_auth_does_not_apply_chatgpt_cap()
```

---

## 6.4 AGY tests

```python
async def test_agy_quota_probe_uses_read_only_command():
    ...

def test_agy_structured_quota_is_normalized():
    ...

def test_agy_model_specific_quota_buckets_are_preserved():
    ...
```

---

## 6.5 Claude tests

```python
def test_claude_normalizes_subscription_limits():
    raw = {
        "rate_limits": {
            "five_hour": {
                "used_percentage": 35,
                "resets_at": 1000,
            },
            "seven_day": {
                "used_percentage": 65,
                "resets_at": 2000,
            },
        }
    }

    usage = normalize_claude_usage(raw)

    assert usage.windows["five_hour"].used_percent == 35
    assert usage.windows["weekly"].used_percent == 65
```

Also:

```python
test_claude_missing_rate_limits_returns_unknown()
test_claude_missing_single_window_is_supported()
test_claude_unknown_quota_fails_open()
test_claude_limit_error_sets_hard_limit()
```

---

## 6.6 Gemini tests

```python
def test_gemini_missing_programmatic_quota_fails_open():
    ...

def test_gemini_quota_exhaustion_is_not_normal_retry():
    ...

def test_gemini_reset_time_is_extracted_when_available():
    ...
```

---

## 6.7 Kiro tests

```python
def test_kiro_missing_usage_probe_fails_open():
    ...

def test_kiro_credit_exhaustion_blocks_new_dispatch():
    ...

def test_kiro_monthly_credit_window_can_be_normalized():
    ...
```

---

## 6.8 OpenCode tests

```python
def test_opencode_local_stats_are_non_authoritative():
    ...

def test_opencode_bound_to_codex_uses_codex_pool():
    ...

def test_opencode_go_estimate_does_not_block_scheduler():
    ...
```

---

## 6.9 Pi / Prime Agent tests

```python
def test_pi_requires_bound_pool_for_subscription_policy():
    ...

def test_pi_profile_can_share_codex_usage_pool():
    ...

def test_prime_agent_uses_same_usage_pool_resolution_as_pi():
    ...

def test_prime_claude_does_not_implicitly_use_claude_code_pool():
    ...
```

---

## 6.10 Scheduler tests

Create:

```text
tests/test_orchestrator_usage_limits.py
```

### All same-pool profiles blocked

```python
def test_all_profiles_of_same_pool_are_blocked_by_cap():
    usage["codex"].windows["weekly"].used_percent = 71
    config.usage_pools["codex"].caps["weekly"] = 70

    builder = issue_using_profile("codex-builder")
    reviewer = issue_using_profile("codex-reviewer")

    assert eligibility(builder).code == "waiting_provider_usage"
    assert eligibility(reviewer).code == "waiting_provider_usage"
```

### Other provider unaffected

```python
def test_other_provider_remains_schedulable():
    codex_usage.windows["weekly"].used_percent = 80

    assert eligibility(codex_issue).code == "waiting_provider_usage"
    assert eligibility(claude_issue).code == "ready"
```

### Cap semantics

```python
test_usage_exactly_at_cap_blocks_dispatch()
test_usage_below_cap_allows_dispatch()
test_any_configured_window_can_block_provider()
test_missing_usage_snapshot_fails_open()
test_probe_exception_fails_open()
test_non_authoritative_usage_fails_open()
test_no_policy_does_not_block_dispatch()
```

### Reset behavior

```python
async def test_task_becomes_ready_after_usage_reset():
    manager.snapshot = usage(
        weekly=72,
        reset_at=T,
    )

    assert eligibility(issue).code == "waiting_provider_usage"

    clock.advance_past(T)
    manager.snapshot = usage(
        weekly=0,
        reset_at=NEXT_T,
    )

    assert eligibility(issue).code == "ready"
```

### Stale safety

```python
async def test_failed_refresh_after_reset_fails_open():
    manager.snapshot = stale_usage(
        weekly=72,
        reset_at=PAST,
    )

    manager.probe.side_effect = ProviderUnavailable()

    assert eligibility(issue).code == "ready"
```

---

## 6.11 Running-worker semantics

```python
async def test_configured_cap_does_not_cancel_running_worker():
    start_worker()

    usage.windows["weekly"].used_percent = 71
    pool.caps["weekly"] = 70

    await scheduler_tick()

    assert worker.cancelled is False
```

Provider exhaustion:

```python
async def test_provider_exhaustion_does_not_consume_retry_budget():
    retries_before = issue.retry_count

    emit(EVENT_PROVIDER_USAGE_EXHAUSTED)

    await reconcile()

    assert issue.retry_count == retries_before
    assert eligibility(issue).code == "waiting_provider_usage"
```

---

## 6.12 API/UI contract tests

Extend:

```text
tests/test_webapi.py
tests/test_web_static_contract.py
tests/test_i18n.py
```

Add:

```python
def test_workflow_api_exposes_usage_pools():
    ...

def test_snapshot_exposes_provider_usage():
    ...

def test_provider_usage_card_exists():
    ...

def test_waiting_provider_usage_has_translation():
    ...

def test_usage_unknown_is_rendered_without_error():
    ...

def test_remaining_percent_is_100_minus_used_percent():
    ...

def test_estimated_usage_is_visually_distinguished():
    ...
```

---

## 6.13 Global fail-open invariant

Add one parameterized regression test across every backend:

```python
@pytest.mark.parametrize(
    "kind",
    [
        "codex",
        "claude",
        "agy",
        "gemini",
        "kiro",
        "opencode",
        "pi",
        "prime-agent",
    ],
)
def test_usage_probe_failure_never_prevents_dispatch(kind):
    probe.raise_error()

    assert scheduler_decision(kind) == READY
```

This should remain a permanent invariant of the feature.

---

# Stage 7 — Recommended implementation order

Implement in this order:

```text
1. UsagePoolConfig + generic usage dataclasses
2. Profile → usage_pool resolution
3. ProviderUsageSnapshot / UsageProbe abstraction
4. Codex explicit rate-limit probe + normalization
5. ProviderUsageManager
6. Scheduler eligibility blocking
7. Automatic reset/re-evaluation
8. Runtime hard-limit classification
9. AGY quota probe
10. Claude cached/passive adapter
11. Provider delegation for OpenCode / Pi / Prime Agent
12. Gemini hard-limit detection
13. Kiro hard-limit detection
14. API projection
15. UI usage card
16. Full integration and regression tests
```

---

# Final architectural rule

The scheduler should never ask:

```text
"How much quota does this agent backend have?"
```

It should ask:

```text
"Which usage pool does this profile consume,
and what does the authoritative probe for that pool report?"
```

That distinction keeps the implementation correct for:

- multiple named profiles using one subscription,
- wrapper backends that can target different providers,
- different quota-window types,
- subscription-backed providers,
- future API-key budgets,
- credit-based plans,
- multiple provider accounts,
- and new backends added later.

The core design boundary is:

> **Agent profiles define how an agent runs. Usage pools define whether the underlying provider/account is currently allowed to start new work.**
