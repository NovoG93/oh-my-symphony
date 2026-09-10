# systemd user units — surviving reboots and infra re-applies

`symphony project add|create` installs a **systemd user unit** for the new
project by default. `symphony service install` does the same for any
`WORKFLOW.md`, and `symphony service install-all` re-declares every project in
the registry — so a `terraform apply` + Ansible run that resets undeclared
home-directory state no longer silently deletes the orchestrator.

Without a unit, `symphony service start` launches a **detached subprocess**
recorded in `.symphony/run/<hash>.json`. That path still exists, and it stays
the default on hosts without a usable user systemd manager (macOS dev
machines, Windows, containers).

## What gets installed

A single unit at `~/.config/systemd/user/<name>.service`:

```
[Unit]
Description=Symphony orchestrator (<dir>)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=<workflow dir>
ExecStart=<python> -m symphony.cli <WORKFLOW.md> --host <host> --port <port>
Restart=always
RestartSec=3
KillMode=mixed
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
```

The `ExecStart` mirrors `service.build_orchestrator_command()` exactly, so a
systemd-managed orchestrator behaves like the detached one: same process
tree, same admin UI on the configured port.

Commands:

```bash
symphony service install ./WORKFLOW.md --host 0.0.0.0 --port 10000
symphony service install ./WORKFLOW.md --dry-run         # print the unit text, write nothing
symphony service install ./WORKFLOW.md --no-enable       # install but do not enable at boot
symphony service install-all                             # every project in the registry (for IaC)
symphony service unit-path ./WORKFLOW.md                 # print the unit path (for Ansible)
symphony service uninstall ./WORKFLOW.md                 # disable --now and remove
```

`symphony service install` is idempotent: the first run reports
`installed`, an unchanged re-run reports `unchanged` and does not touch the
file (stable mtime), and a hand-made unit that already serves the workflow is
reported as `overridden` (see below).

Every managed file starts with a marker line:

```
# Managed by `symphony service install` — customize via drop-in overrides, not by editing this file.
```

## Naming rules

`symphony-<slug>.service`, where `<slug>` is the workflow's **parent directory
name** lowercased with runs of non-alphanumerics collapsed to `-`:

| Workflow path | Unit name |
| --- | --- |
| `~/git/workmate-ai/WORKFLOW.md` | `symphony-workmate-ai.service` |
| `.../My Project (v2)/WORKFLOW.md` | `symphony-my-project-v2.service` |
| `.../a/b/sync/WORKFLOW.md` | `symphony-sync.service` |

The name is stable for a given directory, so `install-all` can re-run after
every infra apply without creating duplicates.

## Adoption of hand-made units

If a `*.service` file already exists in the unit directory whose `ExecStart`
references the **same resolved** `WORKFLOW.md` (e.g. a hand-written
`workmate-orchestrator.service`), the engine **adopts** it instead of creating
a second, competing unit:

- a drop-in is written to `<unit>.d/override.conf` containing the engine's
  `[Service]` settings (working directory, ExecStart, restart policy);
- the base unit file is left untouched, so operator customizations survive;
- `symphony service install` reports `overridden`.

Adoption is guarded: only a unit whose `ExecStart` names the *exact* resolved
workflow path is ever adopted.

`uninstall` removes a managed unit together with its drop-in directory. For an
adopted hand-made unit it removes **only** the engine's drop-in and leaves the
base file in place.

## Drop-in overrides

Never edit the managed unit file — the next `install` may replace its
settings. Customize through drop-ins instead (they win over the base file):

```bash
systemctl --user edit symphony-workmate-ai.service   # creates the same .d/ file
# or write ~/.config/systemd/user/symphony-workmate-ai.service.d/local.conf
systemctl --user daemon-reload
systemctl --user restart symphony-workmate-ai.service
```

When a workflow's host/port/python changes, `ensure_unit` writes the new
settings into `<unit>.d/override.conf` rather than rewriting the base file.

## start / stop / status

When a user systemd manager is available, `symphony service start` installs
(or extends) the unit and runs `systemctl --user restart <unit>`; the service
record gets `backend: systemd` plus the unit name.

- `symphony service stop` stops the unit the record names (detached records
  keep using signal-based termination).
- `symphony service status` reports the unit state, e.g.
  `running workflow=/path/WORKFLOW.md unit=symphony-proj.service state=active (running) port=10000 url=...`.
- `symphony service start --no-systemd` forces the legacy detached backend
  even on a host where a user manager exists.
- On hosts without a user manager (macOS, Windows, containers) everything
  falls back to the detached subprocess path automatically; nothing changes.

`restart` accepts `--no-systemd` too.

## Doctor

Where a user systemd manager exists, `symphony doctor` adds two checks:

```
PASS  service.unit          unit installed and enabled (symphony-workmate-ai.service)
WARN  service.unit          unit missing — run `symphony service install`
WARN  service.linger        linger disabled — run `loginctl enable-linger $USER`
```

Without linger, user units are killed with the last session — exactly the
"orchestrator quietly gone after logout/reboot" failure. Enable it once per
host/user: `loginctl enable-linger $USER`.

## IaC / Ansible

After a re-apply that may have reset undeclared state, re-declare the units
from the engine itself:

```yaml
- name: install orchestrator units
  ansible.builtin.command:
    cmd: "{{ symphony_engine_venv }}/bin/symphony service install \
          {{ item.path }}/WORKFLOW.md --host {{ item.host }} --port {{ item.port }}"
  loop: "{{ symphony_projects }}"
  changed_when: true
```

or, when all projects are already registered on the host:

```bash
~/oh-my-symphony/.venv/bin/symphony service install-all
```

`install-all` exits non-zero and lists the failing projects if any unit cannot
be installed, so an apply can assert on it.

## Environment overrides

- `SYMPHONY_SYSTEMD_UNIT_DIR` — unit directory override. Tests and sandboxes
  set this to a tmp path so they never touch the operator's real
  `~/.config/systemd/user`.
