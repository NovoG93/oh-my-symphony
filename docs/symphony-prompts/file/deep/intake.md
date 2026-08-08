### INTAKE -- turn the raw request into a routed brief

Read: the ticket description (the user's ask), repo README, `docs/llm-wiki/INDEX.md`.
Write: `brief.md` in the vault + ticket comments. Do NOT plan or implement.

1. Write `brief.md` with: Goal, Audience, Done criteria, Constraints, Out of scope, Proof requirements. Done criteria must be objective -- runnable commands, files that must exist, observable behaviors.
2. Route the work type and record it in `brief.md`: `app-delivery` / `feature` / `bugfix` / `research` / `docs`. A bugfix earns a short DAG (reproduce -> fix -> regression-verify -> document); a greenfield app earns the full pipeline. Match brief size to request size.
3. Browser-app products: note "Playwright QA required" under Proof requirements.
4. Append `## Brief` to the ticket: work type, vault path, one-line goal.

Hard gate: `brief.md` exists with objective Done criteria. Then set state to `Research` (for `docs`-only requests you may set `Plan` directly).
