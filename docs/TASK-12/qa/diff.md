# TASK-12 Verify: full reviewed diff `develop..symphony/TASK-12`

Generated 2026-08-17 (Verify stage) with: `git diff develop..HEAD > docs/TASK-12/qa/diff.md` (exit 0).
`develop` = `764ec47a6226681ed06723459ca68f228bee1c6e`, HEAD = `d76191e8a71c76f42fb91ee3138ee07657019d6b`.

```diff
diff --git a/docs/TASK-12/work/details.md b/docs/TASK-12/work/details.md
new file mode 100644
index 0000000..b010dbe
--- /dev/null
+++ b/docs/TASK-12/work/details.md
@@ -0,0 +1,21 @@
+# TASK-12 Stage 1 Work Details: Usage-Aware Agent Profiles Config & Normalized Usage Types
+
+## Overview
+Implemented Stage 1 of the Usage-Aware Agent Profiles architecture:
+- `UsagePoolConfig` dataclass and `usage_pools` mapping in `ServiceConfig`.
+- `usage_pool` reference in `AgentProfileConfig`.
+- `PROFILE_FIELDS_BY_KIND` allowlist update to include `usage_pool` for all agent kinds.
+- Strict validation in `src/symphony/workflow/builder.py`:
+  - `usage_pools` mapping validation.
+  - `source` string requirement and `caps` percentage range validation `(0 < v <= 100)`.
+  - Rejection of unknown `usage_pool` references in `agent_profiles`.
+- Normalized provider-usage types and probe protocol in `src/symphony/backends/usage.py`:
+  - `UsageWindow`
+  - `ProviderUsageSnapshot`
+  - `UsageProbe` protocol
+  - `USAGE_PROBES` registry and `get_usage_probe` with fail-open semantics.
+
+## Test Results
+- Suite: 30 unit tests in `tests/test_usage_limits.py` + 16 unit tests in `tests/test_workflow_agent_profiles.py`.
+- Full regression suite: 2451 passed, 9 skipped in 169s.
+- Type checks: `symphony-pyright` 0 errors, 0 warnings.
diff --git a/docs/TASK-12/work/stage-1-model-and-validation.md b/docs/TASK-12/work/stage-1-model-and-validation.md
new file mode 100644
index 0000000..b360447
--- /dev/null
+++ b/docs/TASK-12/work/stage-1-model-and-validation.md
@@ -0,0 +1,25 @@
+# Stage 1 Work Notes: Usage-Aware Agent Profiles Config & Normalized Usage Model
+
+## Background & Objectives
+Stage 1 of the Usage-Aware Agent Profiles plan introduces:
+1. `UsagePoolConfig` value type:
+   - `source: str`
+   - `caps: dict[str, float]`
+2. `ServiceConfig` extension:
+   - `usage_pools: dict[str, UsagePoolConfig] = field(default_factory=dict)`
+   - Backwards compatible with existing configurations.
+3. `AgentProfileConfig` extension:
+   - `usage_pool: str | None = None`
+   - Backwards compatible (None = default to profile kind).
+4. Validation rules in `src/symphony/workflow/builder.py`:
+   - `usage_pools` must be a mapping.
+   - `source` is required and non-empty string.
+   - `caps` is a mapping of window name to numeric value `0 < value <= 100`.
+   - Arbitrary window names (e.g. `five_hour`, `weekly`, `daily`, `monthly`, `rolling_7d`) allowed.
+   - Unknown `usage_pool` reference in `agent_profiles` is rejected at load time with `ConfigValidationError`.
+   - Field `usage_pool` is allowed for all backend kinds in `PROFILE_FIELDS_BY_KIND`.
+5. Normalized quota types in `src/symphony/backends/usage.py`:
+   - `UsageWindow(key, used_percent, remaining_percent, resets_at)`
+   - `ProviderUsageSnapshot(pool_id, source, windows, hard_limit_reached, authoritative, observed_at, stale)`
+   - `UsageProbe` protocol (`async def fetch_usage(self) -> ProviderUsageSnapshot | None`)
+   - `USAGE_PROBES` registry and fail-open lookup (`get_usage_probe(source)` returning `None` for missing probes).
diff --git a/src/symphony/backends/usage.py b/src/symphony/backends/usage.py
new file mode 100644
index 0000000..b2359c3
--- /dev/null
+++ b/src/symphony/backends/usage.py
@@ -0,0 +1,46 @@
+"""Normalized provider quota and usage telemetry model.
+
+Provides provider-independent usage snapshot structures, probe interface,
+and the USAGE_PROBES registry for usage-aware agent profiles.
+"""
+
+from __future__ import annotations
+
+from dataclasses import dataclass, field
+from datetime import datetime
+from typing import Any, Protocol, runtime_checkable
+
+
+@dataclass(frozen=True)
+class UsageWindow:
+    key: str
+    used_percent: float | None
+    remaining_percent: float | None
+    resets_at: datetime | None = None
+
+
+@dataclass(frozen=True)
+class ProviderUsageSnapshot:
+    pool_id: str
+    source: str
+    windows: dict[str, UsageWindow] = field(default_factory=dict)
+    hard_limit_reached: bool = False
+    # Only authoritative telemetry may block scheduling.
+    authoritative: bool = True
+    observed_at: datetime | None = None
+    stale: bool = False
+
+
+@runtime_checkable
+class UsageProbe(Protocol):
+    async def fetch_usage(self) -> ProviderUsageSnapshot | None: ...
+
+
+# Registry mapping source name -> UsageProbe class/factory.
+# Missing or unsupported probes return None (fail open).
+USAGE_PROBES: dict[str, type[UsageProbe]] = {}
+
+
+def get_usage_probe(source: str) -> type[UsageProbe] | None:
+    """Retrieve the probe class for a given usage pool source, or None if unsupported (fail open)."""
+    return USAGE_PROBES.get(source)
diff --git a/src/symphony/workflow/__init__.py b/src/symphony/workflow/__init__.py
index 2c51545..846eebc 100644
--- a/src/symphony/workflow/__init__.py
+++ b/src/symphony/workflow/__init__.py
@@ -107,6 +107,7 @@ from .config import (
     SystemConfig,
     TrackerConfig,
     TuiConfig,
+    UsagePoolConfig,
     WikiConfig,
 )
 from .profiles import (
@@ -149,6 +150,7 @@ __all__ = [
     "TuiConfig",
     "ProgressConfig",
     "SystemConfig",
+    "UsagePoolConfig",
     "WikiConfig",
     "PromptConfig",
     "ServiceConfig",
diff --git a/src/symphony/workflow/builder.py b/src/symphony/workflow/builder.py
index a13bc26..1e26b1b 100644
--- a/src/symphony/workflow/builder.py
+++ b/src/symphony/workflow/builder.py
@@ -54,6 +54,7 @@ from .config import (
     SystemConfig,
     TrackerConfig,
     TuiConfig,
+    UsagePoolConfig,
     WikiConfig,
 )
 from .constants import (
@@ -297,7 +298,10 @@ def build_service_config(workflow: WorkflowDefinition) -> ServiceConfig:
         ),
     )
 
-    agent_profiles = _validated_agent_profiles(cfg.get("agent_profiles"))
+    usage_pools = _validated_usage_pools(cfg.get("usage_pools"))
+    agent_profiles = _validated_agent_profiles(
+        cfg.get("agent_profiles"), usage_pools=usage_pools
+    )
 
     agent_raw = cfg.get("agent") or {}
     if not isinstance(agent_raw, dict):
@@ -876,6 +880,7 @@ def build_service_config(workflow: WorkflowDefinition) -> ServiceConfig:
         preview=preview,
         artifacts=artifacts,
         agent_profiles=agent_profiles,
+        usage_pools=usage_pools,
     )
 
 
@@ -984,15 +989,106 @@ _ALL_PROFILE_FIELDS = {
     "read_timeout_ms",
     "stall_timeout_ms",
     "resume_across_turns",
+    "usage_pool",
 }
 
 
-def _validated_agent_profiles(raw: Any) -> dict[str, AgentProfileConfig]:
+def _validated_usage_pools(raw: Any) -> dict[str, UsagePoolConfig]:
+    """Parse and validate top-level `usage_pools:` mapping.
+
+    Enforces non-empty pool names, unique pool names, required string source,
+    and valid caps mapping with numeric values in range (0, 100].
+    """
+    if raw is None:
+        return {}
+    if not isinstance(raw, dict):
+        raise ConfigValidationError(
+            "usage_pools must be a mapping",
+            value=raw,
+        )
+    out: dict[str, UsagePoolConfig] = {}
+    for pool_name, pool_data in raw.items():
+        if not isinstance(pool_name, str) or not pool_name.strip():
+            raise ConfigValidationError(
+                "usage_pools pool name must be a non-empty string",
+                value=pool_name,
+            )
+        name = pool_name.strip()
+        if name in out:
+            raise ConfigValidationError(
+                f"usage_pools has duplicate pool name {name!r}",
+                value=name,
+            )
+        if not isinstance(pool_data, dict):
+            raise ConfigValidationError(
+                f"usage_pools[{name!r}] must be a mapping",
+                value=pool_data,
+            )
+        allowed_keys = {"source", "caps"}
+        for k in pool_data:
+            if k not in allowed_keys:
+                raise ConfigValidationError(
+                    f"usage_pools[{name!r}] has unsupported field '{k}'",
+                    value=k,
+                )
+
+        source_raw = pool_data.get("source")
+        if source_raw is None:
+            raise ConfigValidationError(
+                f"usage_pools[{name!r}].source is required",
+                value=pool_data,
+            )
+        if not isinstance(source_raw, str) or not source_raw.strip():
+            raise ConfigValidationError(
+                f"usage_pools[{name!r}].source must be a non-empty string",
+                value=source_raw,
+            )
+        source = source_raw.strip()
+
+        caps_raw = pool_data.get("caps")
+        if caps_raw is None:
+            raise ConfigValidationError(
+                f"usage_pools[{name!r}].caps is required",
+                value=pool_data,
+            )
+        if not isinstance(caps_raw, dict):
+            raise ConfigValidationError(
+                f"usage_pools[{name!r}].caps must be a mapping",
+                value=caps_raw,
+            )
+        caps: dict[str, float] = {}
+        for window_raw, val_raw in caps_raw.items():
+            if not isinstance(window_raw, str) or not window_raw.strip():
+                raise ConfigValidationError(
+                    f"usage_pools[{name!r}].caps window name must be a non-empty string",
+                    value=window_raw,
+                )
+            window = window_raw.strip()
+            if isinstance(val_raw, bool) or not isinstance(val_raw, (int, float)):
+                raise ConfigValidationError(
+                    f"usage_pools[{name!r}].caps.{window} must be a number between 0 and 100 (exclusive 0, inclusive 100)",
+                    value=val_raw,
+                )
+            num_val = float(val_raw)
+            if not (0.0 < num_val <= 100.0):
+                raise ConfigValidationError(
+                    f"usage_pools[{name!r}].caps.{window} must be between 0 and 100 (exclusive 0, inclusive 100)",
+                    value=val_raw,
+                )
+            caps[window] = num_val
+
+        out[name] = UsagePoolConfig(source=source, caps=caps)
+    return out
+
+
+def _validated_agent_profiles(
+    raw: Any, *, usage_pools: dict[str, UsagePoolConfig] | None = None
+) -> dict[str, AgentProfileConfig]:
     """Parse and validate top-level `agent_profiles:` mapping.
 
     Enforces non-empty profile names, supported backend kinds (canonicalizing
     'antigravity' to 'agy'), allowlist checking against PROFILE_FIELDS_BY_KIND,
-    and type checking of individual fields.
+    and type checking of individual fields including usage_pool references.
     """
     if raw is None:
         return {}
@@ -1104,6 +1200,23 @@ def _validated_agent_profiles(raw: Any) -> dict[str, AgentProfileConfig]:
                 value=resume_across_turns,
             )
 
+        usage_pool_raw = profile_data.get("usage_pool")
+        if usage_pool_raw is not None:
+            if not isinstance(usage_pool_raw, str) or not usage_pool_raw.strip():
+                raise ConfigValidationError(
+                    f"agent_profiles[{name!r}].usage_pool must be a non-empty string",
+                    value=usage_pool_raw,
+                )
+            usage_pool_name = usage_pool_raw.strip()
+            if usage_pools is not None and usage_pool_name not in usage_pools:
+                raise ConfigValidationError(
+                    f"agent_profiles[{name!r}].usage_pool references unknown usage pool {usage_pool_name!r}",
+                    value=usage_pool_name,
+                )
+            usage_pool = usage_pool_name
+        else:
+            usage_pool = None
+
         out[name] = AgentProfileConfig(
             name=name,
             kind=kind,
@@ -1114,6 +1227,7 @@ def _validated_agent_profiles(raw: Any) -> dict[str, AgentProfileConfig]:
             read_timeout_ms=read_timeout_ms,
             stall_timeout_ms=stall_timeout_ms,
             resume_across_turns=resume_across_turns,
+            usage_pool=usage_pool,
         )
     return out
 
diff --git a/src/symphony/workflow/config.py b/src/symphony/workflow/config.py
index a0909bd..46fc573 100644
--- a/src/symphony/workflow/config.py
+++ b/src/symphony/workflow/config.py
@@ -117,6 +117,15 @@ class AgentProfileConfig:
     read_timeout_ms: int | None = None
     stall_timeout_ms: int | None = None
     resume_across_turns: bool | None = None
+    usage_pool: str | None = None
+
+
+@dataclass(frozen=True)
+class UsagePoolConfig:
+    """Shared usage pool configuration defining quota source and capacity caps."""
+
+    source: str
+    caps: dict[str, float]
 
 
 @dataclass(frozen=True)
@@ -763,6 +772,8 @@ class ServiceConfig:
     artifacts: ArtifactsConfig = field(default_factory=ArtifactsConfig)
     # Named agent profiles map (profile_name -> AgentProfileConfig)
     agent_profiles: dict[str, AgentProfileConfig] = field(default_factory=dict)
+    # Shared usage pools map (pool_id -> UsagePoolConfig)
+    usage_pools: dict[str, UsagePoolConfig] = field(default_factory=dict)
 
     def prompt_template_for_state(self, state: str) -> str:
         """Return the runtime prompt template for one tracker state."""
diff --git a/src/symphony/workflow/constants.py b/src/symphony/workflow/constants.py
index 70e35d4..ecdc61a 100644
--- a/src/symphony/workflow/constants.py
+++ b/src/symphony/workflow/constants.py
@@ -103,6 +103,7 @@ PROFILE_FIELDS_BY_KIND: dict[str, set[str]] = {
         "turn_timeout_ms",
         "read_timeout_ms",
         "stall_timeout_ms",
+        "usage_pool",
     },
     "claude": {
         "model",
@@ -111,6 +112,7 @@ PROFILE_FIELDS_BY_KIND: dict[str, set[str]] = {
         "turn_timeout_ms",
         "read_timeout_ms",
         "stall_timeout_ms",
+        "usage_pool",
     },
     "gemini": {
         "command",
@@ -118,6 +120,7 @@ PROFILE_FIELDS_BY_KIND: dict[str, set[str]] = {
         "turn_timeout_ms",
         "read_timeout_ms",
         "stall_timeout_ms",
+        "usage_pool",
     },
     "agy": {
         "command",
@@ -125,6 +128,7 @@ PROFILE_FIELDS_BY_KIND: dict[str, set[str]] = {
         "turn_timeout_ms",
         "read_timeout_ms",
         "stall_timeout_ms",
+        "usage_pool",
     },
     "kiro": {
         "command",
@@ -132,6 +136,7 @@ PROFILE_FIELDS_BY_KIND: dict[str, set[str]] = {
         "turn_timeout_ms",
         "read_timeout_ms",
         "stall_timeout_ms",
+        "usage_pool",
     },
     "opencode": {
         "command",
@@ -139,6 +144,7 @@ PROFILE_FIELDS_BY_KIND: dict[str, set[str]] = {
         "turn_timeout_ms",
         "read_timeout_ms",
         "stall_timeout_ms",
+        "usage_pool",
     },
     "pi": {
         "command",
@@ -146,6 +152,7 @@ PROFILE_FIELDS_BY_KIND: dict[str, set[str]] = {
         "turn_timeout_ms",
         "read_timeout_ms",
         "stall_timeout_ms",
+        "usage_pool",
     },
     "prime-agent": {
         "command",
@@ -153,6 +160,7 @@ PROFILE_FIELDS_BY_KIND: dict[str, set[str]] = {
         "turn_timeout_ms",
         "read_timeout_ms",
         "stall_timeout_ms",
+        "usage_pool",
     },
 }
 DEFAULT_CLAUDE_COMMAND = (
diff --git a/tests/test_usage_limits.py b/tests/test_usage_limits.py
new file mode 100644
index 0000000..7bd8a1c
--- /dev/null
+++ b/tests/test_usage_limits.py
@@ -0,0 +1,370 @@
+from __future__ import annotations
+
+from dataclasses import FrozenInstanceError
+from datetime import datetime, timezone
+from pathlib import Path
+import textwrap
+import pytest
+
+from symphony.errors import ConfigValidationError
+from symphony.workflow.builder import build_service_config
+from symphony.workflow.config import (
+    AgentProfileConfig,
+    ServiceConfig,
+    UsagePoolConfig,
+)
+from symphony.workflow.parser import parse_workflow_text
+from symphony.backends.usage import (
+    ProviderUsageSnapshot,
+    UsageProbe,
+    UsageWindow,
+    USAGE_PROBES,
+    get_usage_probe,
+)
+
+
+def _parse(workflow_text: str) -> ServiceConfig:
+    dedented = textwrap.dedent(workflow_text).strip()
+    if not dedented.startswith("---"):
+        dedented = f"---\n{dedented}\n---\n"
+    definition = parse_workflow_text(
+        dedented, source_path=Path("/tmp/WORKFLOW.md")
+    )
+    return build_service_config(definition)
+
+
+# --- Stage 6.1 Configuration Tests ---
+
+
+def test_usage_pool_config_dataclass_fields() -> None:
+    pool = UsagePoolConfig(source="codex", caps={"five_hour": 80.0, "weekly": 70.0})
+    assert pool.source == "codex"
+    assert pool.caps == {"five_hour": 80.0, "weekly": 70.0}
+
+    with pytest.raises(FrozenInstanceError):
+        pool.source = "claude"  # type: ignore[misc]
+
+
+def test_usage_limit_is_shared_by_profiles_of_same_kind() -> None:
+    cfg = _parse("""
+    usage_pools:
+      codex:
+        source: codex
+        caps:
+          five_hour: 80
+          weekly: 70
+
+    agent_profiles:
+      builder:
+        kind: codex
+
+      reviewer:
+        kind: codex
+    """)
+
+    assert "codex" in cfg.usage_pools
+    assert cfg.usage_pools["codex"].source == "codex"
+    assert cfg.usage_pools["codex"].caps["weekly"] == 70.0
+    assert cfg.usage_pools["codex"].caps["five_hour"] == 80.0
+    assert cfg.agent_profiles["builder"].usage_pool is None
+    assert cfg.agent_profiles["reviewer"].usage_pool is None
+
+
+def test_pi_profile_can_explicitly_share_codex_pool() -> None:
+    cfg = _parse("""
+    usage_pools:
+      codex:
+        source: codex
+        caps:
+          weekly: 70
+
+    agent_profiles:
+      pi-builder:
+        kind: pi
+        usage_pool: codex
+    """)
+
+    assert cfg.agent_profiles["pi-builder"].usage_pool == "codex"
+    assert cfg.agent_profiles["pi-builder"].kind == "pi"
+
+
+def test_opencode_and_prime_agent_profiles_can_explicitly_bind_usage_pool() -> None:
+    cfg = _parse("""
+    usage_pools:
+      copilot:
+        source: github-copilot
+        caps:
+          monthly: 85
+
+    agent_profiles:
+      opencode-worker:
+        kind: opencode
+        usage_pool: copilot
+
+      prime-worker:
+        kind: prime-agent
+        usage_pool: copilot
+    """)
+
+    assert cfg.agent_profiles["opencode-worker"].usage_pool == "copilot"
+    assert cfg.agent_profiles["prime-worker"].usage_pool == "copilot"
+
+
+@pytest.mark.parametrize(
+    "cap_repr",
+    [
+        "-1",
+        "0",
+        "0.0",
+        "101",
+        "100.1",
+        "-0.5",
+        '"80"',
+        "'70%'",
+        "true",
+        "false",
+        "null",
+    ],
+)
+def test_usage_cap_rejects_invalid_percent(cap_repr: str) -> None:
+    text = f"""
+    usage_pools:
+      test-pool:
+        source: codex
+        caps:
+          weekly: {cap_repr}
+    """
+    with pytest.raises(ConfigValidationError, match="caps\\.weekly"):
+        _parse(text)
+
+
+def test_unknown_usage_pool_reference_is_rejected() -> None:
+    text = """
+    agent_profiles:
+      builder:
+        kind: codex
+        usage_pool: non-existent-pool
+    """
+    with pytest.raises(ConfigValidationError, match="references unknown usage pool"):
+        _parse(text)
+
+
+def test_missing_usage_pools_is_backward_compatible() -> None:
+    text = """
+    tracker:
+      kind: file
+    agent:
+      kind: codex
+    agent_profiles:
+      simple:
+        kind: codex
+    """
+    cfg = _parse(text)
+    assert cfg.usage_pools == {}
+    assert cfg.agent_profiles["simple"].usage_pool is None
+
+
+def test_partial_usage_policy_is_valid() -> None:
+    text = """
+    usage_pools:
+      codex:
+        source: codex
+        caps:
+          weekly: 75.5
+    """
+    cfg = _parse(text)
+    assert "codex" in cfg.usage_pools
+    assert cfg.usage_pools["codex"].caps == {"weekly": 75.5}
+
+
+def test_generic_daily_window_is_valid() -> None:
+    text = """
+    usage_pools:
+      gemini-daily:
+        source: gemini
+        caps:
+          daily: 85.0
+    """
+    cfg = _parse(text)
+    assert cfg.usage_pools["gemini-daily"].caps == {"daily": 85.0}
+
+
+def test_generic_monthly_window_is_valid() -> None:
+    text = """
+    usage_pools:
+      kiro-monthly:
+        source: kiro
+        caps:
+          monthly: 90
+    """
+    cfg = _parse(text)
+    assert cfg.usage_pools["kiro-monthly"].caps == {"monthly": 90.0}
+
+
+def test_arbitrary_window_names_are_supported() -> None:
+    text = """
+    usage_pools:
+      custom:
+        source: custom-source
+        caps:
+          five_hour: 80
+          rolling_7d: 70
+          ten_minute: 50
+    """
+    cfg = _parse(text)
+    assert cfg.usage_pools["custom"].caps == {
+        "five_hour": 80.0,
+        "rolling_7d": 70.0,
+        "ten_minute": 50.0,
+    }
+
+
+def test_usage_pools_validation_rejects_non_mapping() -> None:
+    text = """
+    usage_pools:
+      - not
+      - a
+      - mapping
+    """
+    with pytest.raises(ConfigValidationError, match="usage_pools must be a mapping"):
+        _parse(text)
+
+
+def test_usage_pools_validation_rejects_empty_name() -> None:
+    text = """
+    usage_pools:
+      "":
+        source: codex
+        caps:
+          weekly: 80
+    """
+    with pytest.raises(ConfigValidationError, match="pool name"):
+        _parse(text)
+
+
+def test_usage_pools_validation_rejects_non_mapping_pool_entry() -> None:
+    text = """
+    usage_pools:
+      codex: "invalid-string"
+    """
+    with pytest.raises(ConfigValidationError, match="usage_pools\\['codex'\\] must be a mapping"):
+        _parse(text)
+
+
+def test_usage_pools_validation_rejects_missing_or_empty_source() -> None:
+    text_missing = """
+    usage_pools:
+      codex:
+        caps:
+          weekly: 80
+    """
+    with pytest.raises(ConfigValidationError, match="source is required"):
+        _parse(text_missing)
+
+    text_empty = """
+    usage_pools:
+      codex:
+        source: "  "
+        caps:
+          weekly: 80
+    """
+    with pytest.raises(ConfigValidationError, match="source must be a non-empty string"):
+        _parse(text_empty)
+
+
+def test_usage_pools_validation_rejects_non_mapping_caps() -> None:
+    text = """
+    usage_pools:
+      codex:
+        source: codex
+        caps: "80%"
+    """
+    with pytest.raises(ConfigValidationError, match="caps must be a mapping"):
+        _parse(text)
+
+
+def test_usage_pools_validation_rejects_unsupported_field() -> None:
+    text = """
+    usage_pools:
+      codex:
+        source: codex
+        caps:
+          weekly: 80
+        extra_field: 123
+    """
+    with pytest.raises(ConfigValidationError, match="unsupported field 'extra_field'"):
+        _parse(text)
+
+
+# --- Normalized backend usage types & probe protocol tests ---
+
+
+def test_usage_window_dataclass() -> None:
+    now = datetime.now(timezone.utc)
+    w = UsageWindow(
+        key="five_hour",
+        used_percent=65.5,
+        remaining_percent=34.5,
+        resets_at=now,
+    )
+    assert w.key == "five_hour"
+    assert w.used_percent == 65.5
+    assert w.remaining_percent == 34.5
+    assert w.resets_at == now
+
+    # Frozen
+    with pytest.raises(FrozenInstanceError):
+        w.used_percent = 50.0  # type: ignore[misc]
+
+
+def test_provider_usage_snapshot_dataclass() -> None:
+    now = datetime.now(timezone.utc)
+    snapshot = ProviderUsageSnapshot(
+        pool_id="codex",
+        source="codex",
+        windows={
+            "five_hour": UsageWindow(
+                key="five_hour",
+                used_percent=60.0,
+                remaining_percent=40.0,
+                resets_at=now,
+            )
+        },
+        hard_limit_reached=False,
+        authoritative=True,
+        observed_at=now,
+        stale=False,
+    )
+    assert snapshot.pool_id == "codex"
+    assert snapshot.source == "codex"
+    assert snapshot.hard_limit_reached is False
+    assert snapshot.authoritative is True
+    assert snapshot.stale is False
+    assert "five_hour" in snapshot.windows
+
+    # Defaults check
+    minimal = ProviderUsageSnapshot(
+        pool_id="claude",
+        source="claude",
+        windows={},
+    )
+    assert minimal.hard_limit_reached is False
+    assert minimal.authoritative is True
+    assert minimal.observed_at is None
+    assert minimal.stale is False
+
+    with pytest.raises(FrozenInstanceError):
+        snapshot.hard_limit_reached = True  # type: ignore[misc]
+
+
+def test_usage_probe_protocol_and_registry_fail_open() -> None:
+    # A missing or unsupported probe returns None (fail open)
+    assert get_usage_probe("non-existent-source") is None
+    assert USAGE_PROBES.get("non-existent-source") is None
+
+    # Verify a class implementing fetch_usage matches UsageProbe Protocol
+    class DummyProbe:
+        async def fetch_usage(self) -> ProviderUsageSnapshot | None:
+            return None
+
+    assert isinstance(DummyProbe(), UsageProbe)
diff --git a/tests/test_workflow_agent_profiles.py b/tests/test_workflow_agent_profiles.py
index cf10b69..0da4932 100644
--- a/tests/test_workflow_agent_profiles.py
+++ b/tests/test_workflow_agent_profiles.py
@@ -32,6 +32,7 @@ def test_agent_profile_config_dataclass_fields() -> None:
         read_timeout_ms=5000,
         stall_timeout_ms=20000,
         resume_across_turns=False,
+        usage_pool="codex-shared",
     )
     assert profile.name == "test-profile"
     assert profile.kind == "codex"
@@ -42,6 +43,7 @@ def test_agent_profile_config_dataclass_fields() -> None:
     assert profile.read_timeout_ms == 5000
     assert profile.stall_timeout_ms == 20000
     assert profile.resume_across_turns is False
+    assert profile.usage_pool == "codex-shared"
 
     # Defaults for optional fields are None
     minimal = AgentProfileConfig(name="min", kind="claude")
@@ -54,6 +56,7 @@ def test_agent_profile_config_dataclass_fields() -> None:
     assert minimal.read_timeout_ms is None
     assert minimal.stall_timeout_ms is None
     assert minimal.resume_across_turns is None
+    assert minimal.usage_pool is None
 
     # Frozen
     with pytest.raises(FrozenInstanceError):
@@ -136,6 +139,7 @@ def test_profile_fields_by_kind_allowlist_structure() -> None:
         assert "turn_timeout_ms" in allowed
         assert "read_timeout_ms" in allowed
         assert "stall_timeout_ms" in allowed
+        assert "usage_pool" in allowed
 
     assert "model" in PROFILE_FIELDS_BY_KIND["codex"]
     assert "reasoning_effort" in PROFILE_FIELDS_BY_KIND["codex"]
```
