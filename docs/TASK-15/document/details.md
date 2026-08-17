# Document-stage brief-vs-reality comparison (TASK-15, 2026-08-17)

**What**: Record of what the Document stage actually verified against the branch, and the doc writes it made.
**Why**: The ticket body's sections are capped; this is the durable comparison detail.
**As-Is -> To-Be**: Claims read from the ticket -> Verified facts + recorded checks.

## Brief vs reality — checks run and results

| Check | Result | Detail |
|---|---|---|
| Diff scope vs `## Implementation` | pass | `git diff develop...HEAD --name-only` = 17 paths: the 11 implementation paths (9 src/tests + 2 work docs) plus 6 `qa/*` evidence files. Every source file named in `## Implementation` is present; no orphan edits. |
| Ticket's "11 files, +1674/-1" | pass (snapshot-bound) | Claim describes implementation commit `4d3b1a1` (11 files, +1674/-1 — confirmed via `git show 4d3b1a1 --stat`). Tip `a206f14` adds only the 6 Verify evidence files (`git diff 4d3b1a1 a206f14 --stat` = +203, 6 files); implementation code is byte-identical between the two commits. |
| AC1 AGY read-only probe + preserved buckets | pass | `agy.py:150` default command `"agy -p /quota --output-format json"`; `fetch_usage` fails open on non-zero exit / non-dict JSON / exception (`agy.py:158-185`); `normalize_agy_usage` keeps bucket keys verbatim (`agy.py:89-132`), no `five_hour`/`weekly` fabrication. |
| AC2 Claude passive/cached + normalization | pass | `ClaudeUsageProbe.fetch_usage` returns `cached_snapshot`/None (`claude_code.py:251-253`); `normalize_claude_usage` maps variants to `five_hour`/`weekly` (`claude_code.py:181-186`); `_is_genuine_claude_exhaustion` excludes rpm/tpm, flags usage-limit keywords (`claude_code.py:105-133`); no HTTP added. |
| AC3 explicit usage_pool; non-authoritative never blocks | pass | Runtime fallback = agent's own kind (`per_turn.py:94-96`); `normalize_opencode_local_usage` hardcodes `authoritative=False` (`opencode.py:458`); tests assert `usage_pool is None` when omitted and explicit `pi-codex -> codex` / `prime-codex -> codex` binding (`test_backend_usage_probes.py:464-517`); `test_opencode_go_estimate_does_not_block_scheduler` asserts READY at 99% used. |
| AC4 Gemini hard-limit + reset, no TTY | pass | `GeminiUsageProbe` returns cached/None (`gemini.py:205-207`); `_parse_gemini_exhaustion` extracts reset from ISO / retry seconds / reset minutes (`gemini.py:82-106`); generic 429 -> not exhaustion. |
| AC5 Kiro credit monthly + fail open | pass | `normalize_kiro_usage` computes `used/total*100` into `monthly` window (`kiro.py:72-94`); `KiroUsageProbe` returns cached/None (`kiro.py:118-120`); `_is_genuine_kiro_exhaustion` credit/monthly keywords, excludes rpm/tpm (`kiro.py:20-42`). |
| AC6 Stage 6.4-6.9 tests green | pass (indirect) | 29 `def test_` in `tests/test_backend_usage_probes.py` = 28 named + 1 parametrized over 8 kinds -> 36 nodeids in `.pytest_cache` (confirmed via grep), `lastfailed = {}` (21:04), 2549 collected (21:05). Fresh re-run not proven — `qa/runtime-blocked.md`. |
| Merge preflight | pass | `git merge-base develop HEAD` = `af8d685` = develop tip -> develop is an ancestor; branch = develop + ticket commits -> conflict-free by construction (`qa/merge-preflight.md`). |
| Plan spec Stages 2.2-2.8 / 6.4-6.9 | n/a (cross-checked) | `/home/symphony/usage-aware-agent-profiles-plan.md` is outside the session's allowed directories (Read + shell both denied). Stage requirements cross-checked via ticket ACs, `work/plan.md`, and the wiki page; no contradiction found. |

## Doc writes (this stage)

- `docs/llm-wiki/usage-aware-agent-profiles.md` — appended Stage 2.2-2.8 section (per-backend probes, delegation invariant, registry, evidence), TASK-15 decision-log row, refreshed title and "Last updated".
- `docs/llm-wiki/INDEX.md` — refreshed `usage-aware-agent-profiles` row (summary + last touched TASK-15).
- `docs/features/agent-profiles.md` — extended the provider-probes paragraph with the Stage 2.2-2.8 lineup and the explicit-pool delegation rule. Done at Document because the implementation commit did not include the feature-doc update (TASK-13/14 both carried theirs in the implementation commit — pattern noted in Learnings).
- `kanban/TASK-15.md` — appended `## Learnings`, `## Wiki Updates`, `## As-Is -> To-Be Report`; frontmatter `state: Done`.

Not touched: `README.md` / `README.ko.md` (no probe-lineup statements that went stale — the TASK-13 pool-caps paragraph remains accurate), `CHANGELOG.md` (feature line has no per-stage entries; TASK-12/13/14 precedent), source and tests (Document is read-only for behavior).

## No Document Defect

No evidence contradicted a ticket claim; the change does what the ticket promised; no shipped doc was wrong about behavior (the feature doc was incomplete about the new probes — updated in this lane, no rewind needed).
