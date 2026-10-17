# Codex linked-board sandbox verification — 2026-10-17

Issue #31 reports board writes through a workspace link being denied during
Review after earlier turns succeeded. This delivery backports the existing
`dev` fix from commit `d9905030064d285c1f0dfb0a578ad9f543d28886` to `main`,
with new live sandbox evidence and regression coverage. The production file
matches `origin/dev` exactly; these are not newly invented production fixes.
The investigation independently reproduced the missing junction grant and
Windows CLI quoting failure on the `main` baseline. It did **not** recreate
the historical four-turn model session or verify its reported token total.

## Defect and change

The normal file-board setup links `workspace/kanban` to the host board.
`scripts/symphony-setup-worktree.sh` uses `mklink /J` on Windows so setup works
without administrator privileges or Developer Mode. Python reports these
entries as directory junctions, with `is_junction() == True` and
`is_symlink() == False`.

The Codex backend previously scanned only `is_symlink()` entries. Junction
board targets were therefore absent from the CLI writable roots, wrapper
environment, and each turn's `sandboxPolicy`. The scan now recognizes both
kinds of links and resolves their final target. Ordinary directories remain
excluded. The existing `workspace-write` policy and top-level scan stay intact.

`test_codex_junction_roots_reach_command_env_and_every_turn` creates real
Windows junctions, including a junction chain and paths containing spaces.
Both cases failed against the main baseline because the scanner returned `[]`.
With the backported dev fix both pass, checking the resolved target, CLI/env injection, ordinary
directory exclusion, and initial/subsequent Review payloads. Those payload
checks exercise the backend builder; they are not model turns.

## Windows CLI argument repair

The first live backend launch after junction recognition exposed a second
failure: JSON-escaped backslashes in the injected TOML array were altered
between Git Bash and native Windows argv. A native Python/Node argv probe
observed doubled backslashes becoming single backslashes. Codex exited before
initialization with `invalid type: string ... expected a sequence` for
`sandbox_workspace_write.writable_roots`. The same argument parsed when passed
directly to the native Codex executable. The exact internal conversion step is
unknown; a narrow MSYS argument-conversion exclusion did not solve it.

The existing dev fix uses equivalent forward-slash Windows CLI paths before
JSON/TOML quoting. Environment and per-turn paths keep their resolved Windows
form. POSIX serialization stays unchanged. This avoids a new quoting scheme
and preserves paths containing spaces or apostrophes.

Three new Git Bash/native-Python argv regressions failed against main
and passed with the dev fix, covering drive paths, spaces/apostrophes, and UNC roots.
The actual Codex backend then launched and initialized successfully with both
an ordinary junction target and `host board user's` as the target name. These
checks did not attempt sandbox command execution or model inference.

## Live Codex sandbox evidence

Windows had Codex CLI **0.162.1** installed. A matching native Linux binary was
fetched only into the investigation scratch directory, without installation
or global configuration changes. Its platform version was selected from the
optional dependencies of the official `@openai/codex@0.162.1` npm package.

- [Official platform metadata](https://registry.npmjs.org/@openai%2fcodex/0.162.1-linux-x64)
- [Tarball](https://registry.npmjs.org/@openai/codex/-/codex-0.162.1-linux-x64.tgz)
- Version: `@openai/codex@0.162.1-linux-x64`; executable: `codex-cli 0.162.1`
- Tarball SHA-512 integrity, verified before extraction:
  `sha512-cjK2mdHIfEepZ1lQ1MX3aTQC+8ncIsvnacl/6vI2dIIcfP8/MrhTQ2+qRlkXFNDzJSj1d36quIszP+3/4CUJWQ==`

The native binary ran in WSL with a scratch-only `CODEX_HOME`. A real symlink
`linux-ws/kanban -> linux-host-board` pointed outside the workspace. One
`codex app-server` process accepted eight `command/exec` requests. These use
the same v2 `SandboxPolicy` shape as Symphony's `turn/start` payload.

The exact policies were:

```json
{"type":"workspaceWrite","writableRoots":[]}
{"type":"workspaceWrite","writableRoots":["/mnt/d/Git/symphony-multi-agent/.tmp/issue31-repro/linux-host-board"]}
```

Each request set `cwd` to
`/mnt/d/Git/symphony-multi-agent/.tmp/issue31-repro/linux-ws` and
`timeoutMs` to `30000`. Its command was `python3 -c` followed by a
`pathlib.Path(...).write_text(...)` call using either the link path or the
resolved host-board path. Distinct files were used for every command.

| Policy | Path used | First command | Repeated command |
| --- | --- | --- | --- |
| No target grant | Workspace symlink | Exit 1, errno 30, no file | Exit 1, errno 30, no file |
| No target grant | Resolved board | Exit 1, errno 30, no file | Exit 1, errno 30, no file |
| Resolved target granted | Workspace symlink | Exit 0, file verified | Exit 0, file verified |
| Resolved target granted | Resolved board | Exit 0, file verified | Exit 0, file verified |

Sandboxing remained enabled. No model inference or authentication was needed;
this proves repeated sandbox command execution, not a complete Kanban/model
workflow. The existing resolved-target policy works on this current CLI.

## Commands, raw evidence, and limits

On the investigation machine, raw artifacts are under
`D:\Git\symphony-multi-agent\.tmp\issue31-repro` (ignored, not committed):

- `linux-package-source.json`: official metadata and integrity.
- `linux_appserver_matrix.py`: complete reproducible RPC driver.
- `linux-appserver-rpc.json` and `linux-appserver-matrix.json`: requests,
  responses, policies, exit codes, and file existence checks.
- `junction-protocol-proof.json`: pre-fix real junction and backend payloads.
- `pytest-red-clean.log`, `pytest-argv-red.log`,
  `pytest-linked-board-green.log`, `pytest-backends.log`, `ruff.log`.
- `argv-diagnostic.json`: original native argument mismatch and direct-native
  parsing control.
- `post-fix-backend-smoke.json` and `post-fix-quoted-backend-smoke.json`:
  successful real backend initialization after injection.
- `pytest-canonical-focused.log`, `ruff-canonical.log`, and
  `canonical-quoted-backend-smoke.log`: verification after canonical dev
  integration, including existing combined Git-worktree/board and platform
  normalization coverage.

Commands executed against the isolated source worktree, with `PYTHONPATH`
pointing to its `src` and the existing repository Python environment:

```powershell
python -m pytest tests/test_backends.py -k "junction_roots or survives_windows_native_argv" -q
python -m pytest tests/test_backends.py -q
python -m ruff check src/symphony/backends/codex.py tests/test_backends.py
wsl.exe -e timeout 120 python3 /mnt/d/Git/symphony-multi-agent/.tmp/issue31-repro/linux_appserver_matrix.py
```

Results before canonical reconciliation: junction regression **2 failed before /
2 passed after**; native argv regression **3 failed before / 3 passed after**;
backend suite **158 passed, 6 skipped**; Ruff **passed**. After adopting the
canonical dev production file and its existing worktree/platform regressions,
the focused combined checks passed **8/8**, Ruff passed again, and real
backend initialization passed again with the quoted junction target. The
production file was checked with `git diff origin/dev --
src/symphony/backends/codex.py`, which produced no diff. The six existing skips
require Windows symlink privileges, which this machine lacks. The new real
junction regressions ran and passed without that privilege.

Windows backend launch and initialization succeeded, but elevated sandbox
command execution failed before running the command:
`windows sandbox: helper_unknown_error: setup refresh had errors`.
An inside-workspace `codex sandbox -P :workspace` control failed the same way,
so Windows OS enforcement remains blocked independently of this scanner fix.
No host setup repair or global sandbox configuration change was made.

A separate Linux direct-CLI experiment with explicit `-P :workspace` ignored
the legacy writable-roots config override. It is not the Symphony invocation
and is not used as evidence against the backend; the exact app-server policy
controls above passed. Historical failure on another platform or with another
configuration remains unverified.
