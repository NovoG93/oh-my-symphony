"""Governed workflow engine — the DAG that runs *inside* one Symphony ticket.

Not to be confused with `symphony.workflow`, which parses `WORKFLOW.md`
(service configuration: tracker, hooks, backends, stage prompts). This
package owns node-level execution: schema, compilation, scheduling,
artifacts, approvals, and crash recovery.

The split in one line: `symphony.workflow` decides *how the service runs*;
`symphony.flow` decides *what happens inside a single ticket run*.
"""

from __future__ import annotations
