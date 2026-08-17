# QA manifest — TASK-13 Verify (2026-08-17)

| Artifact | Content |
|---|---|
| `qa/runtime-blocked.md` | denied pytest / merge-tree attempts + re-run commands |
| `qa/pytest-cache-evidence.md` | full-suite collection incl. all 68 usage ids; lastfailed `{}`; proves/does-not-prove |
| `qa/diff.md` | diff scope, functional vs formatting hunks, AC anchor map |
| `qa/static-validation-review.md` | per-AC static walk (AC1-AC9) |
| `qa/security-static.md` | 7-area security audit with command results |
| `qa/test-inventory.md` | test counts mapped to AC list |
| `qa/merge-tree.md` | merge preflight: target develop, topology proof clean |
| `qa/merge-tree.log` | ignored mirror of the preflight log |

## Command manifest

| Command | Exit | Evidence | Proves | Does not prove |
|---|---|---|---|---|
| `git diff develop..HEAD` / `git show d250c37` | 0 | `qa/diff.md` | exact reviewed change set, chain order, fail-open branches | runtime behavior |
| `grep` anchor sweeps (usage.py, core.py, config.py, backends/usage.py) | 0 | `qa/static-validation-review.md` | each AC implemented at cited lines | execution results |
| security greps (secrets, exec/network) | 0/1 | `qa/security-static.md` | no secrets/injection surface added | live attack surface |
| pytest cache inspection | 0 | `qa/pytest-cache-evidence.md` | full-suite collected 68 usage ids; completed run had 0 failures | fresh-run pass counts |
| `.venv/bin/pytest tests/test_usage_limits.py -q` | denied | `qa/runtime-blocked.md` | — (recorded refusal) | green re-run |
| `git merge-tree --write-tree develop symphony/TASK-13` | denied | `qa/merge-tree.md` | — (recorded refusal) | — |
| `git rev-parse develop` / `git merge-base develop HEAD` | 0 | `qa/merge-tree.md` | develop is ancestor of HEAD -> conflict-free by construction | literal merge-tree output |

## How to re-run the full proof (unrestricted checkout)

```bash
cd /home/symphony/symphony_workspaces/TASK-13
.venv/bin/pytest tests/test_usage_limits.py tests/test_orchestrator_usage_limits.py -q   # expect 68 passed
.venv/bin/symphony-pyright                                                              # expect 0 errors, 0 warnings
.venv/bin/ruff check src/symphony/orchestrator/usage.py src/symphony/orchestrator/__init__.py src/symphony/orchestrator/core.py tests/test_usage_limits.py tests/test_orchestrator_usage_limits.py
.venv/bin/ruff format --check src/symphony/orchestrator/usage.py src/symphony/orchestrator/__init__.py src/symphony/orchestrator/core.py tests/test_usage_limits.py tests/test_orchestrator_usage_limits.py
git merge-tree --write-tree develop symphony/TASK-13
```
