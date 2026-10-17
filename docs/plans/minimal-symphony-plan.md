# Minimal Symphony — Debloat + Chat→DAG Delivery Plan (v2, owner-decided)

**Owner decisions (2026-02):**
1. **All 7 agent backends stay** (codex, claude, gemini, agy, kiro, opencode, pi).
2. **Governed `flow/` engine: DELETE** (approved). One DAG substrate: board-level `blocked_by`.
3. **TUI stays** (optional feature).
4. **Linear/Jira stay** — Symphony is also a ticket-integrated maintenance engine
   (external bug/feature tickets → automated resolution), not only greenfield app delivery.
5. **New final lane: Document** — after Verify, a documentation/learning step:
   record what was done, update docs/policies/changelogs, write insights to the
   llm-wiki so future tickets reuse learnings from previous commits.
   → therefore `wiki_sweep` + `docs/llm-wiki` are **KEPT** (they ARE this feature).
6. **i18n/translation KEPT**; **continuous_improvement (UI/UX & product improvement loop) KEPT**.

**Revised thesis:** This is no longer a mass-deletion project. It is:
(a) delete the *duplicate machinery* (second DAG engine, second web board, repo debris),
(b) keep every operator-facing feature, and
(c) build the connective tissue that turns chat into a gated ticket-DAG pipeline
with a closing Document/learning step.

---

## KEEP (features, per owner)

| Feature | Modules | Note |
|---|---|---|
| 7 backends | `backends/*` | all `kind` branches stay |
| TUI | `tui/`, `keep_awake`, launchers | keep one launcher script, drop bit-rotted duplicates only if unused |
| Web admin UI + chat | `webapi.py`, `server.py`, `web/static`, `chat.py` | primary chat surface |
| Linear/Jira trackers | `trackers/linear.py`, `trackers/jira.py`, `docs/symphony-prompts/linear/` | maintenance-mode entry: external tickets flow through the same lanes |
| Document/learning loop | `utils/wiki_sweep.py`, `docs/llm-wiki/`, Learn stage prompt | becomes the **Document** lane |
| Improvement loop | `continuous_improvement.py` + its webapi endpoints | UI/UX & product improvement proposals |
| Translation | `i18n.py`, `scripts/check_i18n.py` | |
| Ops | `doctor`, `service.py`, `stats.py`, `progress_md.py`, `notifications/` | |

## CUT (duplicates + debris only)

| Item | Size | Why |
|---|---|---|
| `flow/`, `cli/flow.py`, `orchestrator/flow_store.py` + seams in `core.py`/`run_registry.py`/`main.py`/`doctor.py` + flow tests + `workflow_engine` config | ~6.6K LOC + tests | second DAG engine; approved. Recoverable: merged at cd67c24, one revert away |
| `tools/board-viewer/` | ~6K LOC | second standalone web board, duplicates `web/static` |
| `mock_codex.py` | 297 | zero src importers → move under `tests/` |
| `factory/` | 0 | empty package dir |
| `docs/SMA-*`, `docs/REL-066/`, `docs/changelog/`, `docs/qa/`, `docs/dispatch-stability/`, `docs/superpowers/`, old `docs/improvements` archives | ~4.5 MB | this repo's own per-ticket work archives; history lives in git |
| Root debris: `PLAN.md`, `WORKFLOW-PROGRESS.md` (generated), `.omc/RELEASE_RULE.md` (untrack), `tmp_workspaces/`, `log/`, caches, `.coverage`, `.playwright-mcp/`, `.bkit/ .omx/ .serena/ .domain-agent/` | ~17 MB | agent-harness droppings + runtime debris |
| ~~`WORKFLOW.file.example.md`~~ (keep one canonical example + jira example) | — | duplicate. **DEVIATION (kept):** it is the only example configured for a *file* board (host-board `--add-dir`, board symlink hook, `kanban/` excludes) and is referenced by `README:345`, `docs/PIPELINE.md:134`, two tests and the llm-wiki. Bootstrapping now branches on tracker kind: file → `WORKFLOW.file.example.md`, linear → `WORKFLOW.example.md`. |
| `scripts/static_todo_browser_acceptance.py`, `capture_tui_screenshot.py`, `ui_shots.py` (+ mirror tests) | ~600 | one-off screenshot/demo tooling; regenerable |

Net: `src/` ~40.5K → **~33K LOC**; repo −~22 MB. Everything else earns its keep as a feature.

---

## Target board — the dev-cycle DAG (both greenfield and maintenance)

```
active_states:   [Intake, Research, Plan, Review, Build, QA, Verify, Document]
terminal_states: [Done, Human Review, Blocked, Cancelled]
```

- **Intake** — chat agent (or an external Linear/Jira ticket sync) files one Intake
  ticket per request with a `request:` group ID. Worker writes `brief.md` and
  **routes the request**: app-delivery / feature / bugfix / research / docs
  (routing table ported from `oneshot/reference/decomposition.md`). A bugfix
  routes to a short DAG (reproduce → fix → regression-verify → document); a
  greenfield app gets the full pipeline. **This is how "all software dev" fits
  one board.**
- **Research** — evidence gathering (stack, prior art, real data shapes,
  reproduction for bugs) → `research.md`. Reads `docs/llm-wiki` INDEX first —
  insight reuse from previous work.
- **Plan** — decomposition brain: `plan.md` + `contracts.md`; spawns Build/QA/Verify
  tickets via the **structured ticket tool** with `blocked_by` edges; all Build
  tickets blocked by Review.
- **Review (adversarial)** — fresh-context red-team of the plan: domain-logic
  mismatch, end-to-end data shapes, missing/superfluous tickets. Concrete
  objections → back to Plan (max 2 rounds, then Human Review). Pass → `review.md: verdict: PASS`.
- **Build** — TDD implementation; existing wip-per-turn commits with
  `[no-test]`/`[scope-expand]` markers; appends `claims.md`.
- **QA** — behavioral/browser QA on the running app → `qa-report.md` + screenshots.
- **Verify** — re-run every claim; mechanical bash gate (`grep verdict: GREEN`);
  single merge story = orchestrator `auto_merge` on transition (Verify prompt no
  longer merges by hand).
- **Document** *(new final lane, owner requirement)* — after code is merged:
  update user-facing docs/README/CHANGELOG/policies touched by the change,
  write insight entries to `docs/llm-wiki` (what worked, gotchas, decisions),
  update the request vault `docs/req/<REQ-ID>/delivery.md`. `wiki_sweep`
  keeps the wiki healthy every N Done transitions. Then → Done.
- Git automation unchanged: worktree per ticket, wip commits, squash on Done,
  auto-merge preflight.

Tracker parity: file board gets full DAG support first; Linear (native blocking
relations) and Jira (issue links) map `blocked_by` in a follow-up milestone —
until then external-tracker boards run the same lanes with manual ordering.

---

## Phases

### Phase 0 — Safety baseline (½ day)
Tag `pre-minimal`, branch `minimal`, record full test run + doctor snapshot.

### Phase 1 — Zero-seam deletions
board-viewer, docs archives, debris, mock_codex move, empty `factory/`,
duplicate examples, screenshot scripts. Suite green after each commit.

### Phase 2 — Remove the second DAG engine (approved)
Delete `flow/` + `cli/flow.py` + `flow_store.py`; excise seams
(`core.py`, `run_registry.py`, `main.py`, `doctor.py`, `workflow/config`);
delete flow tests; keep `docs/symphony-prompts/workflows/` only if still referenced.

### Phase 3 — Connective tissue (the new code, ~1.2–1.5K LOC)
1. **Structured ticket tool**: extend `cli/board.py` + one webapi endpoint —
   `create/update` with `--state --blocked-by --request`; validation: unique ID,
   legal state, existing blockers, **acyclic DAG**; dangling-ref warnings on
   tracker load. Chat agent and Plan workers call this — no freehand frontmatter.
2. **`request:` grouping field** (optional frontmatter, backward compatible) +
   per-request progress on the web board.
3. **New lane set + stage prompts** in `docs/symphony-prompts/file/` (and
   `linear/` mirror): port oneshot lane prompts; add `review.md` (adversarial)
   and `document.md` (docs + wiki write-back; supersedes/extends `learn.md`);
   mechanical bash gates per lane; strip manual merge from Verify prompt.
4. **Chat → intake wiring**: chat preamble build-request protocol — classify,
   confirm scope in ≤2 turns, file Intake via the ticket tool, reply with board
   link. Chat converses; the board delivers.
5. **DAG guardrails/visibility**: cycle check on load; dep list rendering in
   web board + TUI card badge.

### Phase 4 — Maintenance mode polish
Intake routing table covers external bug tickets (Linear/Jira poll already
exists): bug ticket → short DAG (reproduce/fix/verify/document) without
operator involvement. Verify wiki-read step in Research prompt (insight reuse).

### Phase 5 — Docs + acceptance
- README rewrite around the new story (greenfield chat delivery + automated
  maintenance); one canonical `WORKFLOW.example.md` + jira example; update
  symphony-skill (oneshot folds into the core lane model).
- **Acceptance A (greenfield):** scratch repo, chat "make a to-do app" →
  Intake→…→Document DAG appears with valid `blocked_by` → unattended run →
  merged, tested, documented app; wiki entries written.
- **Acceptance B (maintenance):** file a bug ticket on the board → short DAG →
  fix merged with regression test + changelog + wiki insight.
- Full suite + doctor green at every phase boundary.

## Adversarial review (v2)
1. *"Keeping all features contradicts 'minimal'."* — Owner intent: minimal = no
   duplicate machinery, not no features. The two genuine duplications (flow/
   engine, board-viewer) are the cut; every kept module is a distinct feature.
2. *"Document lane slows every ticket."* — It runs per *request* (and per bugfix
   DAG tail), not per Build ticket; gate is cheap (files exist + wiki entry).
   Skippable via existing skip-Learn-style toggle for trivial changes.
3. *"Two improvement loops (continuous_improvement vs Document lane)."* — They
   compose: Document captures per-request learnings; continuous_improvement
   consumes idle time to propose UI/UX work as new Intake tickets. Wire CI
   proposals through the same structured ticket tool so there is one entry path.
4. *"blocked_by parity on Linear/Jira missing."* — Scoped explicitly as a
   follow-up milestone; lanes work everywhere from day one.
5. *"Adversarial Review deadlock."* — 2-round cap → Human Review escalation.


---

## Appendix: Prime-Agent-inspired feature backlog (owner request, 2026-02)

Mechanisms proven in the Prime Agent harness, mapped to Symphony features.
P3 = lands in Phase 3 connective tissue; PB = post-plan backlog.

| # | Prime Agent mechanism | Symphony feature | Gain | When |
|---|---|---|---|---|
| 1 | Per-task model selection (child inherits model unless overridden) | **Per-stage backend override** in WORKFLOW.md: cheap/fast agent for Intake/Research/Document, strong agent for Plan/Review/Build | Large token-cost cut per request | P3 |
| 2 | Continual-harness memory: compact one-line summaries injected always, full entry loaded only when relevant; local vs global scoping | **Wiki INDEX injection**: every worker prompt gets only the llm-wiki INDEX (one-liners); worker fetches full entries on demand. Request-vault = local memory, llm-wiki = global memory | Insight reuse without context bloat | P3 (prompt change) |
| 3 | `refine.run()` discipline: smallest evidence-backed update after a repeated failure or reusable tactic; validate; record | **Document-lane refinement rules**: wiki entries must cite evidence from the ticket (diff, test log); stage-prompt improvements land as small addendum files, never wholesale rewrites | Learning that compounds instead of drifting | P3 (in document.md prompt) |
| 4 | Admission-return delegation: spawn returns immediately; results only via messages/files | **Any worker may spawn sub-tickets** via the structured ticket tool (not just Plan lane): Build hits hidden complexity -> spawns blocked_by sub-ticket instead of scope-creeping (pairs with existing [scope-expand] marker) | Scope discipline + parallelism | P3 |
| 5 | Bounded read-only observation of family agents (`agent_observe`) | **Review/Verify agents get bounded transcript access**: last-N run events of the Build ticket as evidence input | Stronger adversarial review, cheap | PB |
| 6 | Mid-task context compaction (`compact`: summarize, keep working) | **Rolling ticket-state summary**: past N turns, orchestrator injects a compacted "state so far" note into the next turn prompt | Fewer turn-limit stalls on long tickets | PB |
| 7 | Agent-owned heartbeats (scheduled autonomous work) | **Generalize continuous_improvement into a heartbeat scheduler**: recurring tickets (dep updates, security audit, wiki sweep, i18n sync, UI/UX review) fired through the same Intake routing | All maintenance flows through one gated pipeline | PB |
| 8 | Skill routing: trigger descriptions + progressive disclosure (open only the routed reference) | **Auto-attach skills to tickets**: match ticket text/labels against skill trigger phrases; inject only the matched skill body | Leaner worker prompts | PB |
| 9 | Restricted messaging topology (parent/siblings/direct children only) | **Same-request Q&A file**: tickets in one request group append questions to a shared vault file; Plan-owner ticket answers async | Unblocks workers without human ping-pong | PB |
| 10 | Goal-level budget accounting | **Per-request budget rollup** across the ticket DAG (extends existing per-ticket token EMA) with early warning in admin UI | Cost visibility per user request | PB |


---

## v2.1 owner decisions (lane presets, 2026-02)

1. **Default = 4-lane minimal board** (`Todo → In Progress → Verify → Learn`) with
   **succinct, short stage prompts** — today's agents are intelligent; keep base.md
   and stage files lean. Easy to begin.
2. **8-lane deep pipeline is an optional PRESET** (`Intake → Research → Plan → Review
   → Build → QA → Verify → Document`), chosen per task complexity. **Switchable in
   settings** (admin UI settings page / WORKFLOW.md), applied through the existing
   `workflow/mutate.py` round-trip machinery so user comments/customizations survive.
3. **Boards stay fully user-customizable** — existing lane CRUD + per-state prompt
   editing remain first-class; presets are starting points, not cages.
4. **Complexity routing on the default board** happens through ticket DAGs, not lanes:
   a simple request = one ticket; a complex request = a DAG of stage-tickets
   (RESEARCH-x ← PLAN-x ← REVIEW-x ← BUILD-x… via `blocked_by`, created with the
   validated board tool). Lanes stay minimal; expertise arrives as tickets.
5. Prompt-authoring rule for all presets: **succinct**. State the contract and the
   gate; skip narrative hand-holding. Contract validators (orchestrator/contracts.py)
   define the mechanical floor for the 4-lane preset; the 8-lane preset carries its
   own lean gates (oneshot-style literal bash where applicable).
