---
name: symphony-upstream-sync
description: Safely integrate upstream Symphony development commits into this fork while preserving fork-only behavior, upstream ancestry, audit evidence, and a visible no-ff boundary on develop. Use for requests such as "sync upstream", "merge upstream/dev", "update my fork", or "audit the next upstream integration" in this repository. Do not use for ordinary origin/develop pulls or isolated cherry-picks.
---

# Symphony upstream sync

Preserve two facts at once: the fork must retain its local behavior, and Git must retain the actual upstream ancestry so later syncs contain only new upstream commits.

Before changing Git state:

1. Read `AGENTS.md`, `WORKFLOW.md`, and `docs/upstream-sync.md`.
2. Read [references/integration-playbook.md](references/integration-playbook.md) completely.
3. Inspect the worktree, remotes, branch tips, merge base, prior integrated upstream head, and both `upstream/dev` and `upstream/main` before choosing a source ref.
4. Preserve unrelated tracked, staged, and untracked user files. Stop if they overlap files the merge must change.

Default to `upstream/dev` for functional synchronization. Do not silently include `upstream/main`-only release or prose commits; compare the refs and explain the choice. Never hard-code the hashes from the previous sync as the next target—use the ledger only as the prior boundary.

Use a feature branch and two real `--no-ff` merges:

- M1 merges the selected upstream ref into the feature branch.
- M2 merges the completed feature branch into local `develop`.

Never squash, rebase, reconstruct through cherry-picks, use `-s ours`, use `-X ours`, or resolve whole files by blindly selecting one side. Do not push, delete branches, force-update refs, or merge elsewhere unless the user separately requests it.

Keep the upstream merge open with `--no-commit` while resolving and testing. Resolve conflicts by behavior and preserve the fork invariants listed in the playbook. Treat dispositions in `docs/upstream-sync.md` as deliberate decisions: in particular, a previously deferred upstream subsystem remains deferred unless the user explicitly reopens that decision.

Require evidence before each merge commit. Platform-specific tests must run on the target platform or be explicitly waived by the user; record a waiver as residual risk, never as passed evidence. A known environment-sensitive failure may be classified separately only after an isolated rerun proves it is not reproducible.

Maintain `docs/upstream-sync.md` as the authoritative ledger. Enumerate every newly offered upstream commit, document each conflict and adaptation, record test results and waivers, and capture M1. Finalize M2 in a docs-only commit after M2 because a commit cannot contain its own final hash.

If implementation is delegated, keep workers sequential in the same open merge, give each explicit file/subsystem ownership, prohibit commits, and review every handoff before the next worker starts. Delegation does not broaden permission for Git promotion or pushing.
