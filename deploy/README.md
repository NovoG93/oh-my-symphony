# Deploying symphony-mcp

The `symphony-mcp` gateway runs as a **systemd user service** on the
`agenticOS` VM (user `symphony`), separate from the oh-my-symphony board UI
(`:9999`). It exposes the Streamable HTTP MCP endpoint on `:8080/mcp`.

## Configuration (environment)

| Variable | Default | Purpose |
|---|---|---|
| `SYMPHONY_MCP_HOST` | `0.0.0.0` | bind address (LAN-reachable for Hermes) |
| `SYMPHONY_MCP_PORT` | `8080` | MCP port |
| `SYMPHONY_BASE_URL` | `http://127.0.0.1:9999` | orchestrator REST API (loopback only) |
| `SYMPHONY_MCP_TOKEN_FILE` | `~/.config/symphony-mcp/token` | bearer token (mode 0600) |
| `SYMPHONY_MCP_ALLOWED_PROJECTS` | *(empty = deny all)* | comma-separated project allowlist |

## Install

```bash
# one-time: generate the bearer token (mode 0600)
mkdir -p ~/.config/symphony-mcp
openssl rand -hex 32 > ~/.config/symphony-mcp/token
chmod 600 ~/.config/symphony-mcp/token

# install + start (user service, survives logout/reboot via linger)
sudo loginctl enable-linger symphony
mkdir -p ~/.config/systemd/user
install -m 0644 deploy/symphony-mcp.service ~/.config/systemd/user/symphony-mcp.service
systemctl --user daemon-reload
systemctl --user enable --now symphony-mcp
```

## Verify

```bash
curl -s http://127.0.0.1:8080/health                       # {"status":"ok"}
curl -s -o /dev/null -w '%{http_code}\n' \
  -X POST http://127.0.0.1:8080/mcp -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"x","version":"1"}}}'
# expect 401 without a token
```

The bearer token is the only credential Hermes needs; it is **not** stored in
this repository.
