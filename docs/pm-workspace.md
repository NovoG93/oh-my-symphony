# PM Workspace

PM Workspace adds a local Artifacts screen for planning a change before implementation. It is disabled unless the corresponding `pm` switches are enabled in `WORKFLOW.md`.

The workflow is explicit:

1. Select an isolated Git worktree and create a planning change.
2. Draft and review Idea, Requirements, Design, Plan, and Code Files. Design contains UX, storyboard, architecture, ERD, API, and integration subviews.
3. Validate the managed WBS and the pinned OpenSpec 1.14.0 profile.
4. Approve one exact revision. Approval creates a local planning commit; it does not dispatch, merge, push, or deploy.
5. Preview and confirm handoff. Handoff creates ordinary file-board tickets with immutable PM provenance and native dependencies.
6. Record implementation evidence before an explicit archive operation.

Storyboard frames are ordered functional cards. Selecting one opens structured screen, actor, UI, action, response, transition, requirement, and design fields while the server preserves the stable frame identity and unrelated source bytes.

The planning assistant offers seven explicit proposal-only actions: Draft artifact, Refine selected section, Find gaps, Review consistency, Explain dependency, Propose WBS, and Update impacted artifacts. The server owns section and target scope; every proposed document diff is reviewed before apply. When a proposal needs new managed identities, the host reserves permanent IDs and shows them in the review. Apply activates those IDs. Discard or cancellation retires unused reservations.

## Verification and coverage

An approved task may require native host commands, manual observation procedures, or both. **Run verification** executes an approved native command at the selected tested commit and records the real result without inventing a worker run. **Submit completion evidence** records a configured manual procedure with an immutable report from that commit. Evidence submission never completes a task by itself; the complete procedure set for one commit must pass, then a person reviews and explicitly accepts it.

Requirement and scenario coverage displays specified, linked, implemented, tested, accepted, and waived independently. Partial task counts remain visible. A waiver records its reason and reviewer but grants no execution, acceptance, ancestry, completion, or archive authority.

Every mutation carries the registered project identity, permanent change ID, expected revision, and an idempotency key where the operation can be retried. Direct file editing is supported through External edit mode; application writes stay suspended until re-index and validation finish.

## Configuration

```yaml
pm:
  authoring: false
  exchange: false
  assistance: false
  handoff: false
  openspec_executable: openspec
  planning_workspace_root: /absolute/path/outside/the/protected/checkout
```

XLSX exchange requires the `pm-exchange` optional dependencies. Browser assets are local and lazy; no CDN is contacted. Automatic PM assistance uses a dedicated Codex app-server 0.160.0 process with GPT-5.6 Sol at medium reasoning, an isolated configuration home, no tools/connectors/network execution, read-only sandboxing, and a strict proposal-only output contract.

## Managed WBS

`tasks.md` is the sole task authority. Permanent `T-…` and `G-…` IDs survive title and order edits. Groups are non-executable. Executable tasks carry scope, acceptance, verification, integration impact, references, and typed dependencies. Unsupported markers, arbitrary checkboxes, duplicate YAML keys, cycles, missing references, and ambiguous nesting switch the document to a blocking source-repair state.

Authoritative verification selection uses versioned task metadata:

```yaml
verification_policy:
  version: 1
  procedures:
    - procedure_id: focused
      kind: native
    - procedure_id: manual-ux
      kind: manual
```

Human-readable Verification bullets remain descriptive. Only the structured policy selects procedures; an absent policy retains legacy native-command behavior.

Workbook imports accept a plain `.xlsx` filename and base64 workbook content. The preview uses a three-way comparison. Missing sheets or rows retain content; deletion requires an explicit `Action=Delete`. Formulas, duplicate IDs, external relationships, macros, embedded objects, hostile XML, oversized ZIPs, and oversized worksheet dimensions are rejected before mutation.
