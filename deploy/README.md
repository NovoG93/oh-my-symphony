# Deploying `symphony-mcp`

The `symphony-mcp` gateway is a separate systemd user service (normally the
`symphony` user on `agenticOS`). It talks to the running oh-my-symphony REST
API and exposes the Streamable HTTP MCP endpoint at `/mcp` (default port
`8080`). `/health` is intentionally public; every `/mcp` request, including
`initialize` and all tool calls, requires the MCP gateway bearer token.

## Two independent credentials

There are two different trust boundaries. Never use one secret for both:

* `SYMPHONY_MCP_TOKEN` or `SYMPHONY_MCP_TOKEN_FILE` authenticates the caller
  (Hermes, Codex, or another MCP client) **to the MCP gateway**. The gateway
  checks this token on `/mcp` and does not forward it upstream.
* `SYMPHONY_API_TOKEN` or `SYMPHONY_API_TOKEN_FILE` is the downstream
  credential used by the gateway **to call Symphony**. The MCP client never
  sends this credential. It is needed only when the upstream web API is in
  `token` mode.

Mint each credential independently and store each in a separate file. Keep the
directory private and the files mode `0600`; do not commit, print, or log
either value.

```bash
install -d -m 700 ~/.config/symphony-mcp ~/.config/symphony
openssl rand -hex 32 > ~/.config/symphony-mcp/token
openssl rand -hex 32 > ~/.config/symphony/api-token
chmod 600 ~/.config/symphony-mcp/token ~/.config/symphony/api-token
```

The second file is only required for the upstream `token` variant below. If a
secret is supplied through an environment variable instead, take equivalent
care not to expose it in shell history, process listings, service logs, or
diagnostic output.

## Choose the upstream API mode

Configure the upstream web API with `SYMPHONY_API_AUTH_MODE` and follow the
canonical policy guide in [`../README.md`](../README.md). The MCP bearer is
required in all three cases; only the gateway-to-Symphony credential changes.

| Upstream API mode | MCP bearer (`SYMPHONY_MCP_TOKEN(_FILE)`) | Upstream API key (`SYMPHONY_API_TOKEN(_FILE)`) |
|---|---|---|
| `token` | Required | Required; forwarded as `Authorization: Bearer …` |
| `disabled` | Required | Omit it; no downstream key is needed |
| `capabilities` | Required | Omit it (if present, it is ignored by the upstream policy) |

In `capabilities` mode, `SYMPHONY_REMOTE_OPERATOR_CAPABILITIES` determines
what the gateway can do; a bearer token cannot expand those grants. In
`disabled` mode, normal API routes are open to the local gateway, but Debug
routes still require an explicit Debug grant. Do not infer the mode from the
MCP token: it is always a separate gateway credential.

The checked-in service is deliberately generic and contains no downstream API
secret. Configure Symphony's own web API mode on the Symphony service, as
described in the main guide. Configure only the gateway's corresponding
downstream credential with a systemd user drop-in:

```bash
mkdir -p ~/.config/systemd/user/symphony-mcp.service.d
cat > ~/.config/systemd/user/symphony-mcp.service.d/upstream.conf <<'EOF'
[Service]
# The upstream Symphony service is in token mode.
Environment=SYMPHONY_API_TOKEN_FILE=/home/symphony/.config/symphony/api-token
EOF
systemctl --user daemon-reload
systemctl --user restart symphony-mcp
```

For upstream `disabled` or `capabilities` mode, omit the drop-in entirely (or
remove `SYMPHONY_API_TOKEN(_FILE)` from it). Set
`SYMPHONY_API_AUTH_MODE`, `SYMPHONY_REMOTE_OPERATOR_CAPABILITIES`, and any
required exact trusted origins on the upstream Symphony service, not on the
MCP gateway. Restart both services after changing their respective settings.

## Gateway settings

| Variable | Default | Purpose |
|---|---|---|
| `SYMPHONY_MCP_HOST` | `0.0.0.0` | MCP bind address |
| `SYMPHONY_MCP_PORT` | `8080` | MCP listen port |
| `SYMPHONY_BASE_URL` | `http://127.0.0.1:9999` | upstream Symphony base URL |
| `SYMPHONY_MCP_TOKEN` | unset | gateway bearer (takes precedence over file) |
| `SYMPHONY_MCP_TOKEN_FILE` | `~/.config/symphony-mcp/token` | gateway bearer file |
| `SYMPHONY_API_TOKEN` | unset | downstream API bearer (takes precedence over file) |
| `SYMPHONY_API_TOKEN_FILE` | `~/.config/symphony/api-token` | downstream API bearer file |
| `SYMPHONY_MCP_ALLOWED_PROJECTS` | empty (deny all) | comma-separated project allowlist |
| `SYMPHONY_MCP_ALLOW_CONTROL` | `false` | enable mutating control tools |

`SYMPHONY_MCP_ALLOWED_PROJECTS` limits project-scoped operations. Leave it
empty to deny project-scoped operations, or set it to explicit project IDs
such as `oh-my-symphony,another-project`. Control
tools (`cancel`, `update`, `recover-blocked`, and `skip-document`) remain off
unless `SYMPHONY_MCP_ALLOW_CONTROL=1` (or another true value) is explicitly
configured. Keep this setting off for read-only clients.

## Install and start

```bash
sudo loginctl enable-linger symphony
mkdir -p ~/.config/systemd/user
install -m 0644 deploy/symphony-mcp.service ~/.config/systemd/user/symphony-mcp.service
systemctl --user daemon-reload
systemctl --user enable --now symphony-mcp
```

The service file contains the MCP token-file path and ordinary runtime
defaults, but intentionally does not contain `SYMPHONY_API_TOKEN`. Put the
downstream token and mode in a private drop-in as shown above. Check service
logs and permissions without printing secret contents:

```bash
systemctl --user status symphony-mcp
stat -c '%a %n' ~/.config/symphony-mcp/token ~/.config/symphony/api-token
```

## Verify authenticated MCP access

```bash
curl -s http://127.0.0.1:8080/health

curl -s -o /dev/null -w '%{http_code}\n' \
  -X POST http://127.0.0.1:8080/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"check","version":"1"}}}'
# expect 401: no MCP bearer

MCP_TOKEN="$(<~/.config/symphony-mcp/token)"
curl -s -X POST http://127.0.0.1:8080/mcp \
  -H "Authorization: Bearer ${MCP_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"check","version":"1"}}}'
```

Do not put the token in a URL query parameter; use the `Authorization` header.
MCP session initialization and subsequent tool calls must carry that header.
A successful gateway authentication does not bypass the upstream mode,
capability, project, or control checks.

## Codex Streamable HTTP client

Use Codex's environment-backed bearer configuration so the MCP secret is not
written into a checked-in config file. For example, with the token exported in
the Codex process environment:

```toml
[mcp_servers.symphony]
url = "http://agenticOS:8080/mcp"
bearer_token_env_var = "SYMPHONY_MCP_TOKEN"
```

Set `SYMPHONY_MCP_TOKEN` only in the client environment (or load it from the
private token file before starting Codex). As a desktop fallback, a static
header may be configured with the MCP client's supported header option, but
only with a private file/config mode `0600`; it leaves a long-lived secret at
rest and is less safe than `bearer_token_env_var`.

## Reverse proxies and project ports

If a reverse proxy fronts Symphony, expose every registered project port and
preserve the external hostname when switching projects. The MCP endpoint is a
separate upstream at `/mcp`; proxy its host and port explicitly and preserve
the `Authorization` header. Do not assume that proxying the board UI alone
makes MCP or project-specific ports reachable.
