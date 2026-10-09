# PM Workspace operations

PM state is stored in the existing `.symphony/state.db` schema v9 and in the selected planning worktree. Keep the worktree and database together when recovering an interrupted operation.

## Recovery rules

- A missing or moved planning worktree sets the claim to `REPAIR_REQUIRED`. Rebind it through the repair action; do not edit the database by hand.
- `EXTERNAL_EDIT` blocks browser and assistant writes. End the mode to re-index. Invalid content remains repair-required.
- A staged handoff is not runnable. Reconciliation publishes only when every ticket still matches the durable approved title, body, request, backend, dependencies, provenance, and content hash.
- Missing, malformed, or removed `symphony_pm` metadata stays managed and non-runnable when a durable mapping exists. It never falls back to an ordinary ticket.
- Revocation and actual process launch share a cross-process per-change lock. An expired, superseded, revoked, or drifted permit cannot start another process generation.
- A `requires_code` successor may launch only after a human accepts the predecessor's exact task revision with the complete approved procedure receipt set, and that accepted commit is present in the successor's actual Git ancestry.
- Cleanup uncertainty retains the owned PID/handle and blocks a retry until process-group cleanup can be confirmed.
- Manual evidence submission does not stop a native attempt and does not complete a task. Acceptance returns `native_attempt_active` while a lease, launch permit, owned process, or uncertain cleanup remains. Stop the native attempt through the existing control, wait for confirmed cleanup, then repeat the acceptance preview and confirmation.
- `order_only` predecessors still block native ordering until terminal, but they do not require accepted-code ancestry. `requires_code` predecessors retain both human acceptance and Git ancestry checks.

## Verification procedures and manual completion

Native command definitions remain under `pm.verification_procedures`. Manual definitions use `pm.manual_verification_procedures` with immutable instructions, a required report, and optional or required artifacts. A managed task opts into an exact set with `verification_policy.version: 1` in its task YAML. If the whole field is absent, the compatibility policy requires every configured native command and no manual procedure. The human-readable `### Verification` bullets are descriptive and never define authority.

For work completed without a native agent run, choose a server-issued procedure in Code Files:

- **Run verification** executes a native procedure in an isolated checkout at the selected tested commit. Symphony derives its exit code, output hashes, timeout, and result.
- **Submit completion evidence** records a manual reported result with a checked-in report blob at the tested commit and any policy-required immutable artifacts. It never invents a run or zero exit code.

Both paths create inspectable evidence first. The task changes only after the complete approved procedure set is ready at one commit and a human confirms the existing acceptance preview. Report downloads require normal authentication and the same stable project identity.

Coverage is a lazy derived view. Requirements and scenarios show specified, linked, implemented, tested, accepted, and waived independently, with per-task evidence and partial counts. Every required linked task must qualify before implemented, tested, or accepted is fully satisfied. A waiver is a revision- and scope-bound review record; it never satisfies execution, acceptance, ancestry, completion, or archive gates.

## Planning assistance

The seven planning actions run in the dedicated tool-disabled proposal client. The server owns document and section selection, reloads the requested revision, and returns reviewable per-document patches. New REQ, SC, SB, API, DEC, T, and G identities use bounded model placeholders; the host reserves and substitutes permanent IDs before review. Replaying one operation retains its IDs. Cancelling or discarding retires them permanently.

Apply checks the operation digest, selected paths, base hashes, permanent identities, WBS structure, and approved-task cancellation rules. It writes all selected documents under one recovery journal and one revision. An incremental draft may apply while the overall change still has readiness findings; those findings are returned and remain blocking at approval and handoff. If planning process cleanup is uncertain, later assistance remains unavailable until the owned process group is confirmed stopped.

## Project and package identity

PM authority uses the registered host project ID throughout HTTP, repository, handoff, dispatch, and verification records. Tracker slugs remain tracker configuration and may repeat across registered projects; a stale request carrying another project ID is refused. Standalone services retain one deterministic local identity.

Release evidence binds the installed distribution RECORD, METADATA, package code, and static assets. A metadata or version byte change invalidates the cached binding and requires fresh source-bound evidence.

## Accepted baseline identities

Later OpenSpec `MODIFIED` and `REMOVED` deltas may reuse an exact accepted baseline REQ/SC identity only with the same capability, semantic kind, and scenario parent. Supported requirement renames retain the original IDs through the next real archive. `ADDED` blocks still receive new server IDs. Cross-capability, fabricated, retyped, reparented, duplicate, and retired IDs are refused.

## Managed source newline behavior

Symphony writes newly generated managed files with explicit UTF-8/LF bytes on every platform. It preserves UTF-8 BOM and mixed LF/CRLF bytes outside an edited span. A no-op save does not rewrite the file or revision. An all-CRLF managed WBS is currently unsupported by the canonical parser, so external-edit re-index places it in repair-required/read-only state before any application write. Convert it to supported LF or mixed source outside the application, then re-index; Symphony never silently normalizes it.

Workbook import requests carry the original plain filename together with base64 content. Only `.xlsx` is accepted, case-insensitively; path-like names, `.xls`, `.xlsm`, and renamed unsupported media are refused before workbook parsing. The validated filename participates in the preview digest and persisted import snapshot. Every XML member is parsed before workbook loading; malformed XML, DTD/entity declarations, hidden rows outside a deceptive declared dimension, and duplicate task identities fail as `unsafe_workbook` without creating a preview.

## Downgrade

Turning PM flags off hides mutations but preserves planning files, approvals, handoff mappings, evidence, and dispatch denials. Ordinary tickets retain their existing behavior. Do not delete v9 rows to downgrade.

The PM v9 schema is still additive relative to released v1-v8. Use a fresh v8→v9 disposable fixture for validation. A historical pre-release v9 candidate that lacks current v9 tables is reported as an incomplete registry and fails affected actions closed; Symphony does not silently adopt or repair that candidate.

## Archive

Archive is a separate confirmed operation after completion evidence and conflict checks. OpenSpec 1.14.0 writes `openspec/changes/archive/YYYY-MM-DD-<locator>` (or preserves a locator already carrying a date prefix). The adapter accepts only the exact previewed contained destination. Delivery verification archives only disposable fixtures; archiving this repository or another real project requires separate authorization.

An existing `pmChange=<change-id>` link remains available after archive as read-only history, including its planning documents, WBS, managed tickets, verification evidence, and ArtifactStore links. Archived changes stay out of the active change list, and their original IDs cannot be reclaimed, repaired, edited, approved, handed off, or archived again.
