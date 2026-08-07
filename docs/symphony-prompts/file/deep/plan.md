### PLAN -- decompose into a ticket DAG

Read: `brief.md`, `research.md`, `contracts.md` if present.
Write: `plan.md` + `contracts.md` in the vault; spawn downstream tickets. Do NOT write implementation code.

1. Write `plan.md`: an ordered task table (ID, title, lane, blocked_by, owned contract, files, acceptance summary, verification command) plus a `## <ID>` spec section per ticket. Acceptance criteria are observable: `WHEN <event> THEN <behavior>`.
2. Write `contracts.md`: interface contracts between Build slices. One Build ticket = one contract boundary, independently testable, roughly <= 5 files / <= 500 net lines.
3. Spawn the DAG from the board root (descriptions must be self-contained):

   ```bash
   symphony board new BUILD-1 "<title>" --state Build --blocked-by {{ issue.identifier }} --request "{{ issue.request }}" --description "<goal / scope in-out / acceptance criteria / verification commands>"
   symphony board new VERIFY-1 "Re-prove all claims" --state Verify --blocked-by BUILD-1 --request "{{ issue.request }}" --description "..."
   symphony board new DOCUMENT-1 "Docs + wiki write-back" --state Document --blocked-by VERIFY-1 --request "{{ issue.request }}" --description "..."
   ```

   Scope sizing: tiny fix = BUILD-1 + VERIFY-1 + DOCUMENT-1; add BUILD-N per slice; add QA-1 (blocked by all BUILD-*) for browser/behavioral proof. All spawned tickets stay blocked by this ticket until Review passes.
4. Append `## Plan Summary`: spawned IDs and the dependency order.

Hard gate: `plan.md` + `contracts.md` exist and every spawned ticket has acceptance criteria and a verification command. Then set state to `Review`.
