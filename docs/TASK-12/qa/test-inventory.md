# TASK-12 Verify: Test Inventory

Committed test surface for Stage 6.1, checked 2026-08-17.

## Counts (re-run: `grep -c "^def test_" tests/test_usage_limits.py` -> 20; same for
`tests/test_workflow_agent_profiles.py` -> 16)

- `tests/test_usage_limits.py`: 20 test functions, one parametrized with 11 cases
  (`test_usage_cap_rejects_invalid_percent`: -1, 0, 0.0, 101, 100.1, -0.5, "80", '70%',
  true, false, null) = **30 tests**.
- `tests/test_workflow_agent_profiles.py`: **16 tests**; 2 extended
  (`test_agent_profile_config_dataclass_fields` asserts `usage_pool` set/default;
  `test_profile_fields_by_kind_allowlist_structure` asserts `usage_pool` allowed for every kind).

## AC mapping

| AC | Test(s) |
|---|---|
| AC1 UsagePoolConfig dataclass | `test_usage_pool_config_dataclass_fields` |
| AC2/AC3 model fields | `test_usage_pool_config_dataclass_fields`, `test_agent_profile_config_dataclass_fields` |
| AC4 validation rules | `test_usage_cap_rejects_invalid_percent` (11 cases), `test_usage_pools_validation_rejects_non_mapping`, `..._rejects_empty_name`, `..._rejects_non_mapping_pool_entry`, `..._rejects_missing_or_empty_source`, `..._rejects_non_mapping_caps`, `..._rejects_unsupported_field`, `test_arbitrary_window_names_are_supported`, `test_generic_daily_window_is_valid`, `test_generic_monthly_window_is_valid`, `test_partial_usage_policy_is_valid` |
| AC5 unknown pool reference | `test_unknown_usage_pool_reference_is_rejected` |
| AC6 usage.py types + fail-open | `test_usage_window_dataclass`, `test_provider_usage_snapshot_dataclass`, `test_usage_probe_protocol_and_registry_fail_open` |
| AC7 Stage 6.1 scenarios | `test_usage_limit_is_shared_by_profiles_of_same_kind`, `test_pi_profile_can_explicitly_share_codex_pool`, `test_opencode_and_prime_agent_profiles_can_explicitly_bind_usage_pool`, `test_missing_usage_pools_is_backward_compatible` |

## How to re-run

```
cd /home/symphony/symphony_workspaces/TASK-12
.venv/bin/python -m pytest tests/test_usage_limits.py tests/test_workflow_agent_profiles.py -q
```

Expected: 46 passed. Exec was denied by the workspace permission policy this session — see
`qa/runtime-blocked.md` and `qa/pytest-cache-evidence.md` for indirect run evidence.
