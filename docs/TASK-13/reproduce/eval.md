# Verification & Reproduction Guide — TASK-13

## Test Execution Commands

### Unit & Scheduler Usage Tests
```bash
.venv/bin/pytest tests/test_usage_limits.py tests/test_orchestrator_usage_limits.py
```
Expected: 68 passed.

### Type Check
```bash
.venv/bin/symphony-pyright
```
Expected: 0 errors, 0 warnings.

### Lint & Formatting Check
```bash
.venv/bin/ruff check src/symphony/orchestrator/usage.py src/symphony/orchestrator/__init__.py src/symphony/orchestrator/core.py tests/test_usage_limits.py tests/test_orchestrator_usage_limits.py
.venv/bin/ruff format --check src/symphony/orchestrator/usage.py src/symphony/orchestrator/__init__.py src/symphony/orchestrator/core.py tests/test_usage_limits.py tests/test_orchestrator_usage_limits.py
```
Expected: All checks passed.
