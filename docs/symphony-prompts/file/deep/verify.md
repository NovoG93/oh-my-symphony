### VERIFY -- adversary to claims.md

Read: `plan.md`, `claims.md`, `qa-report.md` if present. You re-prove; you do not trust.
Write: append to the vault `verification.md`; ticket comments. Do NOT edit application code.

1. Re-run every claim's run-to-prove command from a clean checkout state.
2. Run the FULL test suite, lint, and typecheck -- not just slice tests.
3. Exercise integration points across slices (start the app, probe the API).
4. Append to `verification.md`: per-claim result, full-suite result, integration probes, and a final line `verdict: GREEN` or `verdict: RED`.
5. RED -> set each failed slice's BUILD ticket back to `Build` with `## Verify Failure` (discrepancy + repro command), spawn a fresh `VERIFY-<n+1>` ticket blocked by those builds, then close this ticket.

Hard gate before closing:

```bash
grep -q '^verdict: GREEN' "<vault>/verification.md" || exit 1
```

(RED path: the gate is the recorded `verdict: RED` plus reopened builds + the fresh Verify ticket.) Then set state to `Done`.
