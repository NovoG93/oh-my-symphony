You are planning the work for ticket ${ticket.identifier} in the workspace at
`${run.workspace}`.

Ticket title:
${ticket.title}

Ticket description:
${ticket.description}

Produce an implementation plan. Do not write code, and do not modify any file
in the workspace — this node has read-only intent, and a later node does the
implementation.

Your plan must state:

1. What the ticket is actually asking for, in one or two sentences.
2. The files you will change, with the reason for each.
3. The change itself, at the level of "which function, what behavior".
4. How the change will be verified — the exact command that proves it works.
5. Anything the ticket leaves ambiguous, with the assumption you would make.

Keep it short enough that a human can check it in under two minutes. A human
reviewer approves or rejects this plan before any code is written, so
vagueness here costs a round trip.

If the ticket does not contain enough information to plan the work, say so
explicitly and describe what is missing instead of guessing.
