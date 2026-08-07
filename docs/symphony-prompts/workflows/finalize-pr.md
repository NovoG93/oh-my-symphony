You are preparing the pull request for ticket ${ticket.identifier} in the
workspace at `${run.workspace}`.

A human has already approved both the plan and the review. This node is the
one step in the workflow permitted to act outside the workspace, so it is
declared `external_side_effects: true` and will not be retried automatically
if it fails partway.

Do this, in order:

1. Confirm the working tree holds the intended change and nothing else. If
   there are stray files, stop and report rather than committing them.
2. Commit the work on the ticket branch with a message that says what changed
   and why, referencing ${ticket.identifier}.
3. Push the branch.
4. Open a pull request whose body contains: what changed, how it was
   verified, and anything the reviewer should look at closely.

Before creating the pull request, check whether one already exists for this
branch. This node may run again after an interrupted attempt, and a duplicate
PR is a real cost to the humans reviewing it — reuse the existing one and
report that you did.

Do not merge. Do not modify branch protection. Do not edit the ticket's
`state` field; the orchestrator owns board transitions for governed runs.

Report the pull-request URL as the last line of your output.
