# Autonomous development factory

Use this as the default beginner file-board path. It keeps ownership narrow:
Supergoal plans and delivers one Wayfinder slice; Symphony schedules tickets,
creates isolated worktrees, retries failures, and runs an independent Verify
turn.

```bash
git init /path/to/project
symphony factory init /path/to/project --agent opencode
# Commit the generated files before starting workers:
git -C /path/to/project add .
git -C /path/to/project commit -m "Initialize Symphony factory"
# Edit /path/to/project/wayfinder/tickets/*.md
symphony factory sync /path/to/project/wayfinder
symphony factory start /path/to/project/wayfinder
```

Wayfinder tickets use YAML frontmatter with stable `id`, `title`, `route`,
`blocked_by`, and `skills` fields, plus `## Acceptance criteria`, `## Proof
commands` (or `## Proof`), and `## Non-goals`. Routes are `GREENFIELD`,
`DEBUG`, or `LEGACY`. Every synchronized ticket receives `supergoal`. Add only
the `superdesign`, `superpm`, or `superqa` overlays when needed. Optional
`kind` values are `customer-research`, `research`, `design`, `product-spec`,
`qa`, and `ui`; optional `browser` must be a YAML boolean. `factory init`
prints a complete copy-ready schema.

Sync validates the full dependency graph before writing, imports every ticket
and its blocker edges by default, uses source provenance for idempotency, and
never rewrites a ticket after it leaves `Ready`. Use `--frontier-only` when an
operator deliberately wants to import only the currently actionable frontier.

The package owns pinned runtime copies of `supergoal`, `superdesign`,
`superpm`, and `superqa`; init and sync copy them into the project before
falling back to home-directory search for a nonstandard custom skill. Project
copies remain operator-customizable. Bundling the skills does not install
their browser runtimes. Superdesign gates require Node.js 18+,
`@playwright/cli` (`npm install -g @playwright/cli@latest`),
`playwright-cli install --skills`, and system Chrome or
`npx playwright install chromium`. SuperQA browser runs require `textual`,
`playwright`, `pyyaml`, and `pillow`, followed by
`python -m playwright install chromium`. First-time dependency or browser
installation may require network access.

`Ready` is a machine-owned dependency gate, not an agent stage. Sync validates
scope, acceptance checks, proof, and route; Symphony leaves unresolved
dependencies visible in `Ready` and promotes actionable tickets to `Build`
without creating a workspace or consuming an agent slot.

In `Build`, the Wayfinder ticket is the already approved scope and plan and its
sections are the run ledger. The worker reads Supergoal once and executes its
Build, full-spec, edge-case, adversarial-review, and exact-local-QA passes, but
does not create a second GOAL/PLAN/QA run vault. The Build contract requires a
short nonempty section for every pass before an independent `Verify` worker
can add the acceptance-criterion evidence table and move the ticket to Done.

Verification must use the exact commands declared in the ticket's Proof section.
One criterion may cite several inline-code commands in its table cell. Every
declared command needs evidence. The fresh Verify turn rewrites its Verification
section with current UTC time and output; browser work also supplies a fresh,
nonempty in-worktree report and explicit pass, failure, and side-effect counts.
These checks validate the worker's evidence and prevent bypassing Verify; they
do not independently attest that a model executed each reported command.

`Blocked` preserves the workspace for missing authority or environment and does
not create automatic repair tickets. Fix the named blocker, then use
`symphony board update <ID> --state Verify --workflow /path/to/WORKFLOW.md`
to retry proof, or `--state Build` when product work remains. Existing turn and
token budgets still apply; inspect the ticket's budget diagnostics before retrying.

If a Git snapshot fails (for example, an index lock or permission error), Symphony
keeps the product worktree and pauses the ticket instead of merging or removing
it. Inspect the `## Blocker` diagnostic and fix the named Git problem without
deleting the retained worktree. Return the ticket to `Verify` with the command
above, then clear its pause with the TUI's `P` control or
`POST /api/v1/<ID>/resume` on the running service. Moving the card alone does not
clear a persisted pause. Verify runs again before delivery; remote publishing
remains opt-in.

`factory init` prints the exact Supergoal WAYFINDER prompt to use next.
It merges runtime ignore rules into `.gitignore` without replacing user rules:
board cards, worktrees, logs, and `WORKFLOW-PROGRESS.md` stay local runtime
state. Commit the Wayfinder spec and product code, not `kanban/*.md`.
`factory start` runs sync, Doctor (including any `--port` override), then the
managed service. A Doctor failure stops before process launch. Verified Done
work merges locally into the branch that launched the factory. Remote publishing
is opt-in: configure an upstream and `agent.auto_merge_push_target: true`. Use
`examples/advanced/WORKFLOW.file.example.md` for the advanced production
pipeline.

Each worker's `kanban/` must link to the host board. The setup hook replaces
an empty checkout directory with that link and is safe to rerun. It stops with
a repair message if an older repository has tracked cards that produce a
nonempty real directory; continuing would let the worker edit a stale private
card that the scheduler cannot observe.
