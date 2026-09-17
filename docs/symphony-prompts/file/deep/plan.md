### PLAN -- decompose into a ticket DAG

Read: `brief.md`, `research.md`, `contracts.md` if present.
Write: `plan.md` + `contracts.md` in the vault; spawn downstream tickets. Do NOT write implementation code.
Plan is the sole decomposition point: Max 8 Build tickets per request; Build workers must not spawn child tickets.

1. Write `plan.md`: an ordered task table (ID, title, lane, profile, blocked_by, owned contract, files, acceptance summary, verification command) plus `## <ID>` spec sections. Acceptance criteria: `WHEN <event> THEN <behavior>`.
   A minimum useful slice is one observable behavior, its failing test, and one exact proof command.
2. Write `contracts.md`: interface contracts between Build slices. Retain general ceiling of <= 5 files / <= 500 net lines.
   Classify each Build slice as `small-free` or `large-capable`:
   - `small-free`: exactly one behavior, no unresolved design, <= 3 files / <= 200 net lines, one exact verification command, and no auth, security, migration, concurrency, release, or cross-service work. Pin with `--agent-profile opencode-free-small`.
   - `large-capable`: every other or uncertain slice. Pin with `--agent-profile agy-builder`.
   For app delivery also write repository-root `release-contract.yaml`: exact target branch, implementation tickets, launch command, exact runner command plus non-empty hashed repo-relative runner sources, desktop/tablet/mobile viewports, and every requirement/control check across all six required kinds. Name one `app-release-finalizer` ticket and one verifier labeled `app-release`; the finalizer depends on that verifier.
3. Spawn the DAG from the board root. Pin profiles only on Build tickets; never pin QA, Verify, or Document tickets (stage routing owns them). `SYMPHONY_CLI` is exported by the orchestrator:

   ```bash
   ${SYMPHONY_CLI:-symphony} board new BUILD-1 "<title>" --state Build --agent-profile opencode-free-small --blocked-by {{ issue.identifier }} --request "{{ issue.request }}" --description "..."
   ${SYMPHONY_CLI:-symphony} board new BUILD-2 "<title>" --state Build --agent-profile agy-builder --blocked-by {{ issue.identifier }} --request "{{ issue.request }}" --description "..."
   ${SYMPHONY_CLI:-symphony} board new VERIFY-1 "Re-prove all claims" --state Verify --blocked-by BUILD-1 --blocked-by BUILD-2 --request "{{ issue.request }}" --description "..."
   ${SYMPHONY_CLI:-symphony} board new DOCUMENT-1 "Docs + wiki write-back" --state Document --blocked-by VERIFY-1 --request "{{ issue.request }}" --description "..."
   ```

   Scope sizing: tiny fix = BUILD-1 + VERIFY-1 + DOCUMENT-1; add QA-1 (blocked by all BUILD-*) for browser/behavioral proof. All spawned tickets stay blocked by this ticket until Review passes.
4. Append `## Plan Summary`: spawned IDs and the dependency order.

Hard gate: `plan.md` + `contracts.md` exist and every spawned ticket has acceptance criteria and a verification command. Then set state to `Review`.
