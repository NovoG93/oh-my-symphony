# Verify-stage overflow details (TASK-15, 2026-08-17)

Overflow for the ticket sections; per-ticket caps keep section bodies short.

## AC Scorecard detail — signal per criterion

- **AC1 AGY read-only probe + buckets** — signal: `AgyUsageProbe.command` default `"agy -p /quota --output-format json"` (asserted in `test_agy_quota_probe_uses_read_only_command`); `normalize_agy_usage` copies provider/model bucket keys verbatim and `test_agy_model_specific_quota_buckets_are_preserved` asserts `five_hour`/`weekly` are absent. Result: pass. Evidence: `qa/static-review.md`, `qa/test-cache-evidence.md`.
- **AC2 Claude cached adapter + normalization + hard limit + no undocumented endpoints** — signal: `ClaudeUsageProbe.fetch_usage` returns cached snapshot or `None` (cold start); `normalize_claude_usage` maps `five_hour`->`five_hour`, `seven_day`->`weekly`; `_is_genuine_claude_exhaustion` requires explicit usage-limit keywords and excludes rpm/tpm/429; zero HTTP calls added to claude_code.py. Result: pass. Evidence: `qa/static-review.md`, `qa/test-cache-evidence.md`.
- **AC3 delegation + non-authoritative local estimates** — signal: `AgentProfileConfig.usage_pool` bound explicitly in tests (`pi-codex -> codex`); `normalize_opencode_local_usage` hardcodes `authoritative=False`; `ProviderUsageManager.evaluate` returns READY for non-authoritative snapshots (`orchestrator/usage.py:164`); `test_opencode_go_estimate_does_not_block_scheduler` asserts READY at 99% used. Result: pass. Evidence: `qa/static-review.md`, `qa/test-cache-evidence.md`.
- **AC4 Gemini hard-limit + reset extraction, no TTY scraping** — signal: `GeminiUsageProbe` returns cached/None; `_parse_gemini_exhaustion` extracts reset from ISO timestamp / retry seconds / reset minutes and classifies generic 429 as not-exhaustion; unknown percentage -> probe None (fail open). Result: pass. Evidence: `qa/static-review.md`, `qa/test-cache-evidence.md`.
- **AC5 Kiro credit normalization + hard limit, no TTY scraping** — signal: `normalize_kiro_usage` computes `used/total*100` into a `monthly` window (asserted 85.0/15.0 for 850/1000); `KiroUsageProbe` returns cached/None; `_is_genuine_kiro_exhaustion` flags credit/monthly keywords, excludes rpm/tpm. Result: pass. Evidence: `qa/static-review.md`, `qa/test-cache-evidence.md`.
- **AC6 Stage 6.4-6.9 tests green** — signal: all 36 `tests/test_backend_usage_probes.py` nodeids present in `.pytest_cache`, none in `lastfailed` (`{}`); the 119-test usage aggregate and the 4 pyright-gate tests likewise unfailed. Result: pass (indirect — recorded run, not a fresh re-run; live pytest denied 3x, see `qa/runtime-blocked.md`). Evidence: `qa/test-cache-evidence.md`.

## Re-run commands (unrestricted environment)

```bash
cd /home/symphony/git/oh-my-symphony && git worktree add /tmp/t15-verify symphony/TASK-15
cd /tmp/t15-verify
python -m pytest tests/test_backend_usage_probes.py -q                     # 36 acceptance tests
python -m pytest tests/test_backend_usage_probes.py tests/test_codex_usage.py \
    tests/test_usage_limits.py tests/test_orchestrator_usage_limits.py -q  # 119 usage tests
python -m pytest -q                                                        # full suite (2549)
symphony-pyright                                                          # type gate (src/ only)
git merge-tree --write-tree develop symphony/TASK-15                       # preflight (expect no output)
```

## Notes

- `qa/merge-tree.log` matches the prescribed path but is git-ignored (`*.log` in `.gitignore`); identical content rides the Done merge in `qa/merge-preflight.md`.
- LOW review notes (non-blocking) live in `qa/static-review.md` under "Review findings".
- Denied-command records and the 3-form pytest refusal trail: `qa/runtime-blocked.md`.
