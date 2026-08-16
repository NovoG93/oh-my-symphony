# TASK-8 Document — Stage Record (retry 1)

Attempt 0 of this stage left no ticket sections (`## Learnings`, `## Wiki
Updates`, `## As-Is -> To-Be Report` absent; no `## Document Defect`) and no
tree changes — the retry fix is completing the stage in full, not repairing a
recorded failure. Everything below was re-verified on the clean tree
(`8307d02`, the turn auto-commit carrying the Implementation + Verify output).

## Brief vs reality

| Claim (brief / prior sections) | Reality check | Verdict |
|---|---|---|
| AC-1: docs cover the 8 §18 topics | README.md §Named Agent Profiles: kinds-vs-profiles, inheritance+fields, 8 tiers, ticket overrides, Example 1 (multi-model same backend), Example 2 (mixed backend), backward compat; same in README.ko.md; full reference `docs/features/agent-profiles.md`; `WORKFLOW.file.example.md` has precedence chain + `stage_profiles`/`default_profile` comments; `WORKFLOW.example.md` profile docs arrived via TASK-6 (grep: lines 182–393) | held |
| AC-2: migration guidance | README.md + README.ko.md "Backward Compatibility & Migration Guidance" (3 steps); `agent-profiles.md` §Migration with legacy→migrated YAML pair | held |
| AC-3: §20 config → mapping | `test_section_20_acceptance_config_parsing_and_resolution` asserts Research→claude/fable (`--model fable` injected), Plan→codex/sol high, Build→claude/sonnet, Review→codex/sol high, QA→codex/luna medium — verbatim AC-3 | held |
| AC-4: backward-compat regression | `test_backward_compatibility_legacy_workflow_with_stage_kinds` + `test_pure_legacy_workflow_with_no_stage_routing` in collected ids | held (indirect) |
| AC-5: full suite green | Re-verified cache forensics: `lastfailed` = `{}`, `wc -l nodeids` = 2,380 (last line unterminated), all 8 E2E ids present, pyc 05:07:07.134 > source 05:07:04.725 | held (indirect; live rerun gate-denied) |
| AC-6: no web UI changes | `git diff main...HEAD --name-only` = 13 paths: 4 user docs, 1 feature doc, 2 wiki, 1 work note, 5 qa notes, 1 test file — zero UI/source | held |
| LOW-1 fixed (relative links) | README.md:214 / README.ko.md link `docs/features/agent-profiles.md` — relative, present | held |
| LOW-2 fixed (gemini resume) | README.md/ko + feature doc now say "accepted but ignored/no resume support" for gemini | held |
| "2367 passed, 9 skipped" (Done Signals) | 2367+9=2376 vs 2,380 collected — 4-id delta, cache-union accumulation across sessions; consistent, not a contradiction; exact counts unverifiable this pass | noted, not a defect |
| Plan §16–20 re-read this pass | `/home/symphony/agent-profiles-plan.md` unreadable (outside allowed dirs, permission denied). Content quoted in `docs/TASK-8/work/details.md` by the In Progress stage; ticket ACs enumerate the same topics | constraint |

## Assumptions that held / broke

- Held: profile docs belong in README(+ko)/example/features; §20 mapping is
  the plan's table; backward compat needs zero runtime changes (Phases 1–4
  code on main, untouched).
- Held: `WORKFLOW.example.md` needs no branch edit (TASK-6 shipped its
  profile docs) — merged state satisfies the AC.
- Broke: "verify by running pytest" was never possible — the worktree
  permission gate refuses live execution; evidence is the recorded
  session + static/topology checks (same pattern as TASK-5/6/7).

## Stale wiki entries corrected this pass (knowledge base, not behavior)

- `agent-profile-observability-tooling.md`: two "Phase 5 = web UI surfacing"
  claims → UI deferred post-feature (plan §16); Phase 5 was docs+validation.
- `agent-profile-backend-execution.md`: "Remaining phases: Phase 4 ..., Phase
  5 (UI)" → both landed (TASK-7/TASK-8 pointers), UI deferred.
- `agent-profile-resolution.md`: "UI (Phase 5) remain out of scope" →
  Phase 3/5 landed, web UI profile editing deferred (plan §16).
- `agent-profiles-validation-and-docs.md`: added verification-evidence status
  (proven/not-proven, re-run commands, count delta).

## Not covered / Not proven

- Live pytest (ticket tests + full suite), `symphony doctor`, and
  `git merge-tree` — all gate-refused (`docs/TASK-8/qa/runtime-blocked.md`).
- Exact pass/skip counts of the recorded full-suite session (4-id delta).
- CHANGELOG.md entry: not in ticket ACs; TASK-6/7 precedent left `[Unreleased]`
  empty. Flag for the release-prep ticket, not added here (no drive-by scope).

## How to re-run (unrestricted checkout)

- `.venv/bin/pytest tests/test_workflow_agent_profiles_e2e.py -v`
- `.venv/bin/pytest -q`
- `symphony doctor WORKFLOW.md`
- `git -C /home/symphony/git/oh-my-symphony merge-tree --write-tree main symphony/TASK-8`
