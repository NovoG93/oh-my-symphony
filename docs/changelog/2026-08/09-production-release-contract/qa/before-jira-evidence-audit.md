# Before: Jira release evidence audit

- The released create flow tests only summary, type, and priority; JIRA-003 required description,
  assignee, labels, and validation as well.
- The drag scenario calls `store.moveIssue()` directly rather than exercising pointer drag/drop.
- No release scenario clicks every visible navigation/header control. Backlog and Issues fall through
  to Board, while header Search and Filters are inert.
- The mobile check asserts hamburger visibility and column count, but does not detect the clipped header,
  unreachable actions, or document overflow visible in the captured screenshot.
- Safari is scored PASS even though the same ticket says Safari execution was not performed.
- The HTTP helper can write a FAIL result and still exit zero.
- Evidence contains no exact merged-target SHA and no reproducible dependency manifest.
- Cards are pointer-only, modal labels/focus behavior are incomplete, storage failures are discarded,
  and the initial theme button can be blank because its icon is updated before insertion.

Grounded source anchors are in the read-only audit handoff and will be recaptured by the independent
browser run rather than treated as proof that the fixes work.
